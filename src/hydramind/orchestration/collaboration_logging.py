"""Native team interaction log request shaping."""

from __future__ import annotations

from typing import Any

from hydramind.control.models import InteractionLogEventKind, InteractionLogRecord
from hydramind.harness.base import InvocationResult
from hydramind.mas import TeamSpec
from hydramind.observability import compact_text
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
    InteractionLogRecorderFn,
)


class NativeTeamInteractionLogger:
    """Submit native team interaction log records through a control-owned seam."""

    def __init__(
        self,
        record_interaction_event: InteractionLogRecorderFn | None,
    ) -> None:
        self._record_interaction_event = record_interaction_event

    async def interaction_started(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.INTERACTION_STARTED,
            detail={
                "mode": team.protocol.mode.value,
                "topology": team.protocol.topology.value,
                "member_ids": [member.id for member in team.members],
            },
        )

    async def turn_started(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        actor: str,
        turn_index: int,
        detail: dict[str, Any],
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.TURN_STARTED,
            actor=actor,
            turn_index=turn_index,
            detail=detail,
        )

    async def turn_completed(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        actor: str,
        turn_index: int,
        member_result: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.TURN_COMPLETED,
            actor=actor,
            turn_index=turn_index,
            detail={
                **detail,
                "stop_reason": member_result["stop_reason"],
                "tool_call_count": member_result["tool_call_count"],
            },
        )

    async def message_sent(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        actor: str,
        turn_index: int,
        content: str,
        detail: dict[str, Any],
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.MESSAGE_SENT,
            actor=actor,
            turn_index=turn_index,
            content=content,
            detail=detail,
        )

    async def vote_cast(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        actor: str,
        turn_index: int,
        content: str,
        detail: dict[str, Any],
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.VOTE_CAST,
            actor=actor,
            turn_index=turn_index,
            content=content,
            detail=detail,
        )

    async def interaction_completed(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        invocation: InvocationResult,
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.INTERACTION_COMPLETED,
            detail={
                "model_id": invocation.model_id,
                "stop_reason": invocation.stop_reason.value,
                "tool_call_count": 0,
            },
        )

    async def interaction_failed(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        error: Exception,
    ) -> None:
        await self._record(
            request=request,
            team=team,
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            event_kind=InteractionLogEventKind.INTERACTION_FAILED,
            detail={
                "error_type": type(error).__name__,
                "error_preview": compact_text(str(error)),
            },
        )

    async def _record(
        self,
        *,
        request: CollaborationExecutionRequest,
        team: TeamSpec,
        interaction_id: str,
        workspace_id: str | None,
        event_kind: InteractionLogEventKind,
        actor: str | None = None,
        turn_index: int | None = None,
        content: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._record_interaction_event is None:
            return
        await self._record_interaction_event(
            InteractionLogRecord(
                session_id=request.session_id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                trace_id=request.trace_id,
                interaction_id=interaction_id,
                team_id=team.id,
                workspace_id=workspace_id,
                event_kind=event_kind,
                actor=actor,
                turn_index=turn_index,
                content_preview=compact_text(content) if content is not None else None,
                detail=detail or {},
            )
        )


__all__ = ["NativeTeamInteractionLogger"]
