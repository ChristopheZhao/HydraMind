"""Swappable per-member turn strategies for native MAS collaboration.

The per-member multi-turn loop is the harness's execution STRATEGY, not the
orchestration scheduler's. ``NativeTeamExecutor`` (orchestration) owns WHO acts
next and the durable interaction record; a ``MemberTurnStrategy`` owns HOW one
member's turn is driven to completion (single-agent tool loop + termination).

This is the seam that lets a swapped ``ExecutionHarness`` (e.g. the explicit-submit
strategy) reach each MAS member, matching the concept-map boundary where the
orchestration layer coordinates MULTIPLE per-agent harness strategies rather than
the second harness wrapping only the outer team node (PLAN-20260621-001 M1).

Invariant: a strategy never mutates ``RuntimeSession``/durable state, never
spawns subagents, and never evaluates gates. It drives an already-spawned
subagent through the control-owned tool-round seam and emits observability only.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    ToolCall,
    ToolSpec,
)
from hydramind.observability import ObservationEventKind, compact_text
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
    ToolContextFactoryFn,
    ToolRoundExecutorFn,
    TraceEmitterFn,
)
from hydramind.orchestration.execution_harness import SubagentHandle


class MemberTurnStrategy(Protocol):
    """Drives one already-spawned MAS member's turn to completion."""

    name: str

    async def drive_member(
        self,
        *,
        subagent: SubagentHandle,
        request: CollaborationExecutionRequest,
        first_result: InvocationResult,
        tools: list[ToolSpec],
        agent_role: str,
        tool_origin: dict[str, Any],
    ) -> tuple[InvocationResult, int]: ...


class MemberTurnTooling:
    """Control/runtime-owned per-round mechanics shared by member strategies.

    Owns one tool round (execute the round through the control-owned tool seam,
    re-send the tool message to the subagent, emit observability). Strategies
    decide the LOOP and termination; sharing this keeps default and explicit-submit
    identical in tool-execution semantics so they differ ONLY in loop policy.
    """

    def __init__(
        self,
        *,
        execute_tool_round: ToolRoundExecutorFn,
        tool_context_for: ToolContextFactoryFn,
        emit_trace: TraceEmitterFn,
        model_hint: ModelHint,
        max_tool_rounds: int,
    ) -> None:
        self._execute_tool_round = execute_tool_round
        self._tool_context_for = tool_context_for
        self._emit_trace = emit_trace
        self._model_hint = model_hint
        self._max_tool_rounds = max_tool_rounds

    @property
    def max_tool_rounds(self) -> int:
        return self._max_tool_rounds

    async def run_tool_round(
        self,
        *,
        subagent: SubagentHandle,
        request: CollaborationExecutionRequest,
        tool_calls: tuple[ToolCall, ...],
        tools: list[ToolSpec],
        agent_role: str,
        tool_origin: dict[str, Any],
        round_no: int,
        successful_results_by_fingerprint: dict[str, tuple[str, str]],
    ) -> InvocationResult:
        tool_context = self._tool_context_for(request.node_key, agent_role)
        tool_results = await self._execute_tool_round(
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            tool_calls=tool_calls,
            round_no=round_no,
            tools=tools,
            agent_role=agent_role,
            tool_context=tool_context,
            origin=tool_origin,
            successful_results_by_fingerprint=successful_results_by_fingerprint,
        )
        tool_message = Message(role=MessageRole.TOOL, tool_results=tuple(tool_results))
        await self._emit_trace(
            ObservationEventKind.MODEL_INVOKE_STARTED,
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            detail={
                "role": agent_role,
                "round": round_no,
                "message_count": 1,
                "tool_count": len(tools),
                "model_hint": self._model_hint.value,
                **_origin_detail(tool_origin),
            },
        )
        current = await subagent.send(tool_message)
        await self._emit_trace(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            detail={
                "role": agent_role,
                "round": round_no,
                "model_id": current.model_id,
                "stop_reason": current.stop_reason.value,
                "tool_call_count": len(current.tool_calls),
                "content_preview": compact_text(current.content),
                "usage": current.usage.model_dump(),
                **_origin_detail(tool_origin),
            },
        )
        return current


class DefaultMemberTurnStrategy:
    """Batch-drain member strategy (the default-harness strategy).

    Executes ALL pending tool calls per round until none remain. Reproduces the
    historical ``CollaborationExecutor._drain_subagent_tool_calls`` behavior
    verbatim, so the un-swapped path is byte-for-byte unchanged.
    """

    name = "default-drain"

    def __init__(self, tooling: MemberTurnTooling) -> None:
        self._tooling = tooling

    async def drive_member(
        self,
        *,
        subagent: SubagentHandle,
        request: CollaborationExecutionRequest,
        first_result: InvocationResult,
        tools: list[ToolSpec],
        agent_role: str,
        tool_origin: dict[str, Any],
    ) -> tuple[InvocationResult, int]:
        current = first_result
        drained = 0
        successful: dict[str, tuple[str, str]] = {}
        for round_no in range(1, self._tooling.max_tool_rounds + 1):
            if not current.tool_calls:
                return current, drained
            drained += len(current.tool_calls)
            current = await self._tooling.run_tool_round(
                subagent=subagent,
                request=request,
                tool_calls=current.tool_calls,
                tools=tools,
                agent_role=agent_role,
                tool_origin=tool_origin,
                round_no=round_no,
                successful_results_by_fingerprint=successful,
            )
        return current, drained


class ExplicitSubmitMemberTurnStrategy:
    """Explicit-submit member strategy (the explicit-submit-harness strategy).

    One action per turn: execute a SINGLE tool action (``tool_calls[0]``),
    observe, then continue. Terminates on an explicit ``{"done": true,
    "submit": ...}`` payload, a plain no-tool response (the member yields its
    contribution), or budget exhaustion. Mirrors
    ``ExplicitSubmitExecutionHarness._run_submit_loop`` at the per-member
    granularity, so a swapped explicit-submit harness genuinely drives each MAS
    member rather than only the outer team node. (Single-action act/observe reads
    as "ReAct-style", but ReAct is an agent/scaffold pattern, not a harness-level
    identity — see ADR-0012 rename note.)
    """

    name = "explicit-submit"

    def __init__(self, tooling: MemberTurnTooling) -> None:
        self._tooling = tooling

    async def drive_member(
        self,
        *,
        subagent: SubagentHandle,
        request: CollaborationExecutionRequest,
        first_result: InvocationResult,
        tools: list[ToolSpec],
        agent_role: str,
        tool_origin: dict[str, Any],
    ) -> tuple[InvocationResult, int]:
        current = first_result
        drained = 0
        successful: dict[str, tuple[str, str]] = {}
        for round_no in range(1, self._tooling.max_tool_rounds + 1):
            submitted = _explicit_submit(current.content)
            if submitted is not None:
                return _submit_result(current, submitted), drained
            if not current.tool_calls:
                return current, drained
            action = (current.tool_calls[0],)
            drained += len(action)
            current = await self._tooling.run_tool_round(
                subagent=subagent,
                request=request,
                tool_calls=action,
                tools=tools,
                agent_role=agent_role,
                tool_origin=tool_origin,
                round_no=round_no,
                successful_results_by_fingerprint=successful,
            )
        submitted = _explicit_submit(current.content)
        if submitted is not None:
            return _submit_result(current, submitted), drained
        # Budget exhausted: yield the member's last content and clear pending tool
        # calls so the scheduler records a completed turn (the harness owns
        # termination, not the scheduler).
        return current.model_copy(update={"tool_calls": ()}), drained


def _submit_result(current: InvocationResult, submitted: str) -> InvocationResult:
    return current.model_copy(update={"content": submitted, "tool_calls": ()})


def _explicit_submit(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("done") is not True:
        return None
    if "submit" not in payload:
        return None
    submitted = payload["submit"]
    if isinstance(submitted, str):
        return submitted
    return json.dumps(submitted, ensure_ascii=False, sort_keys=True)


def _origin_detail(origin: dict[str, Any] | None) -> dict[str, Any]:
    if not origin:
        return {}
    return {"origin": dict(origin)}


__all__ = [
    "DefaultMemberTurnStrategy",
    "ExplicitSubmitMemberTurnStrategy",
    "MemberTurnStrategy",
    "MemberTurnTooling",
]
