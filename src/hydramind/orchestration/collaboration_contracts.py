"""Shared contracts for orchestration collaboration executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from hydramind.control.interaction_state import DurableInteraction
from hydramind.control.models import InteractionLogRecord
from hydramind.harness.base import (
    Message,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.kernel.contracts import (
    InteractionStatus,
    MessageRole,
    TurnStatus,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome
from hydramind.observability import ObservationEventKind
from hydramind.tools.base import ToolContext


class TraceEmitterFn(Protocol):
    async def __call__(
        self,
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, Any] | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None: ...


class InteractionLogRecorderFn(Protocol):
    async def __call__(
        self,
        record: InteractionLogRecord,
    ) -> InteractionLogRecord: ...


class DurableInteractionRecorder(Protocol):
    """Control-owned seam for writing AUTHORITATIVE durable interaction state.

    Distinct from ``InteractionLogRecorderFn`` (preview projection). The team
    executor records authoritative turn/message/outcome state through this seam
    as it runs (S5a, record-only). Implemented by a thin adapter over the
    control plane so only Control mutates the durable state.
    """

    async def start_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        node_key: str,
        execution_id: str,
        team_id: str,
        protocol_mode: str,
        topology: str,
        member_ids: tuple[str, ...] = (),
        workspace_id: str | None = None,
    ) -> DurableInteraction: ...

    async def record_interaction_turn(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        agent_id: str,
        status: TurnStatus = TurnStatus.COMPLETED,
    ) -> DurableInteraction: ...

    async def record_interaction_message(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        sender: str,
        content: str,
        role: MessageRole = MessageRole.AGENT,
        in_reply_to: str | None = None,
    ) -> DurableInteraction: ...

    async def record_interaction_outcome(
        self,
        session_id: str,
        *,
        interaction_id: str,
        vote: VoteOutcome | None = None,
        coordinator: CoordinatorOutcome | None = None,
    ) -> DurableInteraction: ...

    async def complete_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        status: InteractionStatus = InteractionStatus.COMPLETED,
        error: str | None = None,
    ) -> DurableInteraction: ...

    async def fail_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        error: str,
    ) -> DurableInteraction: ...

    async def find_resumable_interaction(
        self, session_id: str, node_key: str
    ) -> DurableInteraction | None: ...

    async def mark_interaction_resumed(
        self,
        session_id: str,
        *,
        interaction_id: str,
        recovered_from_turn_index: int,
    ) -> DurableInteraction: ...


class ToolRoundExecutorFn(Protocol):
    async def __call__(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        tool_calls: tuple[ToolCall, ...],
        round_no: int,
        tools: list[ToolSpec],
        agent_role: str,
        tool_context: ToolContext,
        origin: dict[str, Any] | None,
        successful_results_by_fingerprint: dict[str, tuple[str, str]],
    ) -> list[ToolResultBlock]: ...


class ToolContextFactoryFn(Protocol):
    def __call__(self, node_key: str, agent_role: str) -> ToolContext: ...


@dataclass(frozen=True)
class CollaborationExecutionRequest:
    """Inputs needed to execute one collaboration node."""

    session_id: str
    node_key: str
    execution_id: str
    trace_id: str
    messages: list[Message]
    system: str
    tools: list[ToolSpec] | None
    agent_role: str
    node_config: dict[str, Any]


__all__ = [
    "CollaborationExecutionRequest",
    "DurableInteractionRecorder",
    "InteractionLogRecorderFn",
    "ToolContextFactoryFn",
    "ToolRoundExecutorFn",
    "TraceEmitterFn",
]
