"""Typed observation events emitted by the HydraMind runtime."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObservationEventKind(StrEnum):
    SESSION_CREATED = "session_created"
    SESSION_RUNNING = "session_running"
    SESSION_WAITING_GATE = "session_waiting_gate"
    SESSION_RESUMING = "session_resuming"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    SESSION_CANCELLED = "session_cancelled"
    NODE_STARTED = "node_started"
    NODE_PENDING_GATE = "node_pending_gate"
    NODE_APPROVED = "node_approved"
    NODE_NEEDS_REVISION = "node_needs_revision"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_REVISED = "node_revised"
    ATTEMPT_STARTED = "attempt_started"
    NODE_EXECUTION_STARTED = "node_execution_started"
    NODE_EXECUTION_COMPLETED = "node_execution_completed"
    NODE_EXECUTION_FAILED = "node_execution_failed"
    NODE_EXECUTION_ABORTED = "node_execution_aborted"
    MODEL_INVOKE_STARTED = "model_invoke_started"
    MODEL_INVOKE_COMPLETED = "model_invoke_completed"
    AGENT_TURN_STARTED = "agent_turn_started"
    AGENT_TURN_COMPLETED = "agent_turn_completed"
    AGENT_MESSAGE_SENT = "agent_message_sent"
    AGENT_HANDOFF = "agent_handoff"
    AGENT_VOTE = "agent_vote"
    TOOL_DRAIN_ROUND = "tool_drain_round"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    GATE_RECORDED = "gate_recorded"
    DECISION_APPLIED = "decision_applied"
    EXECUTION_LEASE_GRANTED = "execution_lease_granted"
    EXECUTION_LEASE_HEARTBEAT = "execution_lease_heartbeat"
    EXECUTION_LEASE_RELEASED = "execution_lease_released"
    EXECUTION_HARNESS_SELECTED = "execution_harness_selected"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


class ObservationEvent(BaseModel):
    """One immutable runtime event."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=_new_event_id)
    kind: ObservationEventKind
    session_id: str
    node_key: str | None = None
    trace_id: str | None = None
    execution_id: str | None = None
    parent_event_id: str | None = None
    actor: str | None = None
    source: str = "control"
    level: str = "info"
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
