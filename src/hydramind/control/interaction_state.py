"""Durable, control-owned native MAS interaction state (S5a).

This module introduces the AUTHORITATIVE durable form of native MAS interaction
state, distinct from the :class:`InteractionLogRecord` projection in
``models.py``:

- The kernel ``Interaction``/``Turn``/``Message`` value types
  (``hydramind.kernel.contracts``) stay orchestration-private and ephemeral —
  the runtime advances them in memory and they are lost on crash.
- ``InteractionLogRecord`` is an append-only PROJECTION carrying only a bounded
  ``content_preview`` (max 512 chars). It is NOT authoritative durable state.
- The aggregates here are the durable, control-owned source of truth: full
  authoritative message content, turn status, a version/append sequence, and a
  recovery marker. They are persisted on ``RuntimeSession`` and mutated ONLY by
  ``SessionService`` (single-writer rule, AGENTS.md §3).

S5a is RECORD-ONLY: these records are written through the control boundary as a
team runs, but they do not yet change scheduling, turn selection, or recovery.
The ``DurableTurn`` lease fields are present (so S5b can populate them without a
schema migration) but UNUSED in S5a.

Protocol outcomes reuse the S3b typed contracts
(``hydramind.mas.protocol_outcomes``) as the payload — the canonicalization /
tally logic is not duplicated here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hydramind.kernel.contracts import (
    InteractionStatus,
    MessageRole,
    TurnStatus,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DurableMessage(BaseModel):
    """Authoritative durable record for one interaction message.

    Unlike the ``InteractionLogRecord`` projection (which keeps only a bounded
    ``content_preview``), ``content`` here is the FULL authoritative message
    content.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    interaction_id: str
    turn_index: int = Field(ge=0)
    sender: str
    role: MessageRole = MessageRole.AGENT
    content: str = ""
    in_reply_to: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class DurableTurn(BaseModel):
    """Authoritative durable record for one scheduled interaction turn.

    The lease fields (``turn_lease_token``/``turn_lease_owner``/
    ``turn_lease_expires_at``/``last_heartbeat_at``) are present so S5b can add
    turn-level lease + resumability without a schema change. They are ALWAYS
    ``None`` in S5a (record-only; no lease logic here).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    interaction_id: str
    turn_index: int = Field(ge=0)
    agent_id: str
    status: TurnStatus = TurnStatus.PENDING
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    # --- S5b lease fields (declared now, unused in S5a) -------------------
    turn_lease_token: str | None = None
    turn_lease_owner: str | None = None
    turn_lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


class DurableProtocolOutcome(BaseModel):
    """Typed durable wrapper for a vote/coordinator protocol outcome.

    Reuses the S3b typed outcomes as the payload (no duplicate canonicalization
    logic). Exactly one of ``vote``/``coordinator`` is set per interaction.
    """

    model_config = ConfigDict(frozen=True)

    vote: VoteOutcome | None = None
    coordinator: CoordinatorOutcome | None = None
    recorded_at: datetime = Field(default_factory=_utc_now)


class DurableInteraction(BaseModel):
    """Authoritative, durable, control-owned native MAS interaction aggregate.

    Mutated only by ``SessionService``. ``version`` is a monotonic append/
    sequence counter bumped on every durable write (start/turn/message/outcome/
    completion), so a reader can detect how many authoritative appends have
    occurred. ``recovered_from_turn_index`` is a recovery marker reserved for
    S5b (always ``None`` in S5a record-only mode).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    node_key: str
    execution_id: str
    team_id: str
    protocol_mode: str
    topology: str
    member_ids: tuple[str, ...] = ()
    workspace_id: str | None = None
    status: InteractionStatus = InteractionStatus.PENDING
    turns: tuple[DurableTurn, ...] = ()
    messages: tuple[DurableMessage, ...] = ()
    outcome: DurableProtocolOutcome | None = None
    version: int = 0
    recovered_from_turn_index: int | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def turn_by_index(self, turn_index: int) -> DurableTurn | None:
        for turn in self.turns:
            if turn.turn_index == turn_index:
                return turn
        return None

    def message_by_id(self, message_id: str) -> DurableMessage | None:
        for message in self.messages:
            if message.id == message_id:
                return message
        return None


def durable_interaction_payload(interaction: DurableInteraction) -> dict[str, Any]:
    """JSON payload for one durable interaction (for projections/tests)."""

    return interaction.model_dump(mode="json")


def durable_interaction_id(session_id: str, node_key: str) -> str:
    """Stable durable-interaction id keyed by ``(session_id, node_key)`` (S5b).

    The durable interaction belongs to the NODE, not to a single node attempt.
    Attempts of the same node (each with its own ``execution_id``) share/continue
    one durable interaction, so a fresh attempt after a crash/requeue can locate
    and resume the prior in-progress interaction instead of starting a new one.
    """

    return f"interaction-{session_id}-{node_key}"


__all__ = [
    "DurableInteraction",
    "DurableMessage",
    "DurableProtocolOutcome",
    "DurableTurn",
    "durable_interaction_id",
    "durable_interaction_payload",
]
