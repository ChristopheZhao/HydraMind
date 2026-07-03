"""PLAN-20260621-001 M1: per-member turn strategies are swappable and distinct.

These tests pin the behavioral difference between the default batch-drain member
strategy and the explicit-submit member strategy, and prove a single-shot fake
fails the single-action conformance bar — so swapping a harness genuinely changes how
each MAS member is driven, not just an outer label.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.harness.base import (
    InvocationResult,
    Message,
    ModelHint,
    StopReason,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
)
from hydramind.orchestration.collaboration_member_strategy import (
    DefaultMemberTurnStrategy,
    ExplicitSubmitMemberTurnStrategy,
    MemberTurnTooling,
)
from hydramind.tools.base import ToolContext

WRITE = ToolSpec(name="artifact.write_text", description="write a file")


def _tool_turn(*calls: ToolCall) -> InvocationResult:
    return InvocationResult(
        content="acting", tool_calls=tuple(calls), stop_reason=StopReason.TOOL_USE
    )


def _plain(content: str) -> InvocationResult:
    return InvocationResult(content=content, stop_reason=StopReason.END_TURN)


def _call(name: str) -> ToolCall:
    return ToolCall(id=name, name="artifact.write_text", arguments={"k": name})


class _FakeSubagent:
    """Minimal subagent: replays scripted post-first-turn responses on send()."""

    id = "fake-sub"

    def __init__(self, scripted: list[InvocationResult]) -> None:
        self._scripted = list(scripted)
        self._index = 0
        self.sent: list[Message] = []

    async def send(self, message: Message) -> InvocationResult:
        self.sent.append(message)
        result = self._scripted[self._index]
        self._index += 1
        return result


def _request() -> CollaborationExecutionRequest:
    return CollaborationExecutionRequest(
        session_id="s",
        node_key="n",
        execution_id="e",
        trace_id="t",
        messages=[],
        system="sys",
        tools=[WRITE],
        agent_role="writer",
        node_config={},
    )


def _tooling(batch_sizes: list[int], *, max_tool_rounds: int = 4) -> MemberTurnTooling:
    """Tooling whose execute_tool_round records the per-round tool-call count."""

    async def _execute_tool_round(
        *, tool_calls: tuple[ToolCall, ...], **_: Any
    ) -> list[ToolResultBlock]:
        batch_sizes.append(len(tool_calls))
        return [ToolResultBlock(tool_call_id=c.id, content="ok") for c in tool_calls]

    def _tool_context_for(node_key: str, agent_role: str) -> ToolContext:
        return ToolContext()

    async def _emit_trace(kind: Any, **_: Any) -> None:
        return None

    return MemberTurnTooling(
        execute_tool_round=_execute_tool_round,
        tool_context_for=_tool_context_for,
        emit_trace=_emit_trace,
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=max_tool_rounds,
    )


@pytest.mark.asyncio
async def test_default_strategy_batches_all_tool_calls_in_one_round() -> None:
    batches: list[int] = []
    strategy = DefaultMemberTurnStrategy(_tooling(batches))
    subagent = _FakeSubagent([_plain("done")])
    first = _tool_turn(_call("a"), _call("b"))

    result, drained = await strategy.drive_member(
        subagent=subagent,
        request=_request(),
        first_result=first,
        tools=[WRITE],
        agent_role="writer",
        tool_origin={},
    )

    assert result.content == "done"
    assert not result.tool_calls
    assert drained == 2
    # Both tool calls executed in a SINGLE round (batch drain).
    assert batches == [2]
    assert strategy.name == "default-drain"


@pytest.mark.asyncio
async def test_react_strategy_executes_one_action_per_turn() -> None:
    batches: list[int] = []
    strategy = ExplicitSubmitMemberTurnStrategy(_tooling(batches))
    # After action A, the model emits the second tool call; then a plain final.
    subagent = _FakeSubagent([_tool_turn(_call("b")), _plain("done")])
    first = _tool_turn(_call("a"), _call("b"))

    result, drained = await strategy.drive_member(
        subagent=subagent,
        request=_request(),
        first_result=first,
        tools=[WRITE],
        agent_role="writer",
        tool_origin={},
    )

    assert result.content == "done"
    assert not result.tool_calls
    assert drained == 2
    # ONE action per turn (act/observe), not a batch — two single-action rounds.
    assert batches == [1, 1]
    assert strategy.name == "explicit-submit"


@pytest.mark.asyncio
async def test_react_strategy_terminates_on_explicit_submit() -> None:
    batches: list[int] = []
    strategy = ExplicitSubmitMemberTurnStrategy(_tooling(batches))
    subagent = _FakeSubagent([])
    first = _plain('{"done": true, "submit": "final answer"}')

    result, drained = await strategy.drive_member(
        subagent=subagent,
        request=_request(),
        first_result=first,
        tools=[WRITE],
        agent_role="writer",
        tool_origin={},
    )

    assert result.content == "final answer"
    assert not result.tool_calls
    assert drained == 0
    assert batches == []


@pytest.mark.asyncio
async def test_react_strategy_times_out_when_member_never_submits() -> None:
    # A "single-shot fake" that keeps emitting tool calls and never submits must
    # exhaust the budget (the discriminating negative bar) rather than be mistaken
    # for a clean completion.
    batches: list[int] = []
    strategy = ExplicitSubmitMemberTurnStrategy(_tooling(batches, max_tool_rounds=3))
    subagent = _FakeSubagent([_tool_turn(_call("x")) for _ in range(5)])
    first = _tool_turn(_call("x"))

    result, drained = await strategy.drive_member(
        subagent=subagent,
        request=_request(),
        first_result=first,
        tools=[WRITE],
        agent_role="writer",
        tool_origin={},
    )

    # Budget exhausted after max_tool_rounds single actions; pending calls cleared
    # so the scheduler still records a completed turn (harness owns termination).
    assert drained == 3
    assert batches == [1, 1, 1]
    assert not result.tool_calls
