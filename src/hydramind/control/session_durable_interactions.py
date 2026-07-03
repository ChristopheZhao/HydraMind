"""Control-owned durable interaction state bookkeeping (S5a).

Pure helpers that mutate a ``RuntimeSession`` in place, mirroring
``session_interactions.py``. They are invoked ONLY by ``SessionService`` (the
single writer). Each mutation bumps the ``DurableInteraction.version`` append
counter and is idempotent-friendly: re-recording the same logical turn /
message / outcome does not double-append.

The durable aggregate is the authoritative source of truth (full message
content); the ``InteractionLogRecord`` projection is unaffected.
"""

from __future__ import annotations

from datetime import datetime

from hydramind.control.interaction_state import (
    DurableInteraction,
    DurableMessage,
    DurableProtocolOutcome,
    DurableTurn,
)
from hydramind.control.models import RuntimeSession
from hydramind.kernel.contracts import (
    InteractionStatus,
    MessageRole,
    TurnStatus,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome


def _bumped(
    interaction: DurableInteraction,
    *,
    now: datetime,
    **updates: object,
) -> DurableInteraction:
    return interaction.model_copy(
        update={
            **updates,
            "version": interaction.version + 1,
            "updated_at": now,
        }
    )


def start_durable_interaction(
    session: RuntimeSession,
    *,
    interaction_id: str,
    node_key: str,
    execution_id: str,
    team_id: str,
    protocol_mode: str,
    topology: str,
    member_ids: tuple[str, ...],
    workspace_id: str | None,
    now: datetime,
) -> DurableInteraction:
    """Create (or return existing) the durable interaction aggregate.

    Idempotent: if the interaction already exists it is returned unchanged so a
    duplicate/replayed start does not reset version or recorded turns.
    """

    existing = session.durable_interactions.get(interaction_id)
    if existing is not None:
        return existing
    interaction = DurableInteraction(
        id=interaction_id,
        session_id=session.id,
        node_key=node_key,
        execution_id=execution_id,
        team_id=team_id,
        protocol_mode=protocol_mode,
        topology=topology,
        member_ids=member_ids,
        workspace_id=workspace_id,
        status=InteractionStatus.RUNNING,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.durable_interactions[interaction_id] = interaction
    session.updated_at = now
    return interaction


def _require(session: RuntimeSession, interaction_id: str) -> DurableInteraction:
    interaction = session.durable_interactions.get(interaction_id)
    if interaction is None:
        raise KeyError(
            f"durable interaction {interaction_id!r} not started for "
            f"session {session.id!r}"
        )
    return interaction


def record_durable_turn(
    session: RuntimeSession,
    *,
    interaction_id: str,
    turn_index: int,
    agent_id: str,
    status: TurnStatus,
    now: datetime,
) -> DurableInteraction:
    """Append or complete a durable turn (idempotent by ``turn_index``).

    A duplicate delivery of the same logical turn (same ``turn_index`` already
    in the target ``status``) is a no-op, so no double turn is appended.
    """

    interaction = _require(session, interaction_id)
    existing = interaction.turn_by_index(turn_index)
    completed_at = now if status is TurnStatus.COMPLETED else None
    if existing is not None:
        if existing.status is status:
            return interaction
        turn = existing.model_copy(
            update={"status": status, "completed_at": completed_at}
        )
        turns = tuple(
            turn if t.turn_index == turn_index else t for t in interaction.turns
        )
    else:
        turn = DurableTurn(
            id=f"turn-{interaction_id}-{turn_index}",
            interaction_id=interaction_id,
            turn_index=turn_index,
            agent_id=agent_id,
            status=status,
            created_at=now,
            completed_at=completed_at,
        )
        turns = (*interaction.turns, turn)
    updated = _bumped(interaction, now=now, turns=turns)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def record_durable_message(
    session: RuntimeSession,
    *,
    interaction_id: str,
    turn_index: int,
    sender: str,
    content: str,
    role: MessageRole = MessageRole.AGENT,
    in_reply_to: str | None = None,
    now: datetime,
) -> DurableInteraction:
    """Append an authoritative durable message with FULL content.

    Idempotent by stable message id (``msg-{interaction}-{turn}-{sender}``): a
    duplicate delivery of the same logical message is a no-op.
    """

    interaction = _require(session, interaction_id)
    message_id = f"msg-{interaction_id}-{turn_index}-{sender}"
    if interaction.message_by_id(message_id) is not None:
        return interaction
    message = DurableMessage(
        id=message_id,
        interaction_id=interaction_id,
        turn_index=turn_index,
        sender=sender,
        role=role,
        content=content,
        in_reply_to=in_reply_to,
        created_at=now,
    )
    updated = _bumped(
        interaction,
        now=now,
        messages=(*interaction.messages, message),
    )
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def record_durable_outcome(
    session: RuntimeSession,
    *,
    interaction_id: str,
    vote: VoteOutcome | None = None,
    coordinator: CoordinatorOutcome | None = None,
    now: datetime,
) -> DurableInteraction:
    """Record the typed protocol outcome on the durable interaction.

    Idempotent: if an outcome is already recorded it is left in place.
    """

    interaction = _require(session, interaction_id)
    if interaction.outcome is not None:
        return interaction
    if vote is None and coordinator is None:
        return interaction
    outcome = DurableProtocolOutcome(
        vote=vote,
        coordinator=coordinator,
        recorded_at=now,
    )
    updated = _bumped(interaction, now=now, outcome=outcome)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def mark_durable_interaction_resumed(
    session: RuntimeSession,
    *,
    interaction_id: str,
    recovered_from_turn_index: int,
    now: datetime,
) -> DurableInteraction:
    """Set the recovery marker when an interaction is resumed from durable state.

    Idempotent: if the marker is already at ``recovered_from_turn_index`` the
    interaction is returned unchanged (no spurious version bump on replay).
    """

    interaction = _require(session, interaction_id)
    if interaction.recovered_from_turn_index == recovered_from_turn_index:
        return interaction
    updated = _bumped(
        interaction,
        now=now,
        recovered_from_turn_index=recovered_from_turn_index,
    )
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def complete_durable_interaction(
    session: RuntimeSession,
    *,
    interaction_id: str,
    status: InteractionStatus,
    error: str | None = None,
    now: datetime,
) -> DurableInteraction:
    """Mark the durable interaction COMPLETED/FAILED (idempotent on status)."""

    interaction = _require(session, interaction_id)
    if interaction.status is status:
        return interaction
    updated = _bumped(interaction, now=now, status=status, error=error)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


__all__ = [
    "complete_durable_interaction",
    "mark_durable_interaction_resumed",
    "record_durable_message",
    "record_durable_outcome",
    "record_durable_turn",
    "start_durable_interaction",
]
