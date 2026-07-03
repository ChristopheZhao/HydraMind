"""Native team observability event emission boundary."""

from __future__ import annotations

from typing import Any

from hydramind.harness.base import InvocationResult, ModelHint
from hydramind.mas import TeamSpec
from hydramind.mas.protocol_outcomes import canonicalize_vote
from hydramind.observability import ObservationEventKind, compact_text
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
    TraceEmitterFn,
)
from hydramind.orchestration.collaboration_interaction import team_detail


class NativeTeamEventEmitter:
    """Emit native team trace events without owning team scheduling."""

    def __init__(
        self,
        *,
        model_hint: ModelHint,
        emit_trace: TraceEmitterFn,
    ) -> None:
        self._model_hint = model_hint
        self._emit_trace = emit_trace

    async def invocation_started(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
    ) -> None:
        await self._emit_trace(
            ObservationEventKind.MODEL_INVOKE_STARTED,
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            detail={
                "role": request.agent_role,
                "round": 0,
                "message_count": len(request.messages),
                "tool_count": len(request.tools or ()),
                "model_hint": self._model_hint.value,
                **team_detail(team),
            },
        )

    async def coordinator_handoff(
        self,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        turn_detail: dict[str, Any],
    ) -> None:
        await self._emit_team_event(
            ObservationEventKind.AGENT_HANDOFF,
            request=request,
            actor=actor,
            detail={**turn_detail, "handoff": "coordinator"},
        )

    async def turn_started(
        self,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        turn_detail: dict[str, Any],
    ) -> None:
        await self._emit_team_event(
            ObservationEventKind.AGENT_TURN_STARTED,
            request=request,
            actor=actor,
            detail=dict(turn_detail),
        )

    async def turn_completed(
        self,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        turn_detail: dict[str, Any],
        member_result: dict[str, Any],
    ) -> None:
        await self._emit_team_event(
            ObservationEventKind.AGENT_TURN_COMPLETED,
            request=request,
            actor=actor,
            detail={
                **turn_detail,
                "stop_reason": member_result["stop_reason"],
                "tool_call_count": member_result["tool_call_count"],
            },
        )

    async def message_sent(
        self,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        turn_detail: dict[str, Any],
        content: str,
    ) -> None:
        await self._emit_team_event(
            ObservationEventKind.AGENT_MESSAGE_SENT,
            request=request,
            actor=actor,
            detail={
                **turn_detail,
                "content_preview": compact_text(content),
            },
        )

    async def vote(
        self,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        turn_detail: dict[str, Any],
        content: str,
    ) -> None:
        await self._emit_team_event(
            ObservationEventKind.AGENT_VOTE,
            request=request,
            actor=actor,
            detail={
                **turn_detail,
                "vote": content.strip(),
                "vote_canonical": canonicalize_vote(content),
                "content_preview": compact_text(content),
            },
        )

    async def invocation_completed(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        invocation: InvocationResult,
    ) -> None:
        await self._emit_trace(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            detail={
                "role": request.agent_role,
                "round": 0,
                "model_id": invocation.model_id,
                "stop_reason": invocation.stop_reason.value,
                "tool_call_count": 0,
                "content_preview": compact_text(invocation.content),
                "usage": invocation.usage.model_dump(),
                **team_detail(team),
            },
        )

    async def _emit_team_event(
        self,
        kind: ObservationEventKind,
        *,
        request: CollaborationExecutionRequest,
        actor: str,
        detail: dict[str, Any],
    ) -> None:
        await self._emit_trace(
            kind,
            session_id=request.session_id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            actor=actor,
            detail=detail,
        )


__all__ = ["NativeTeamEventEmitter"]
