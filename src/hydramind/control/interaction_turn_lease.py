"""Control-owned durable TURN lease + expired-turn recovery (S5b).

This mirrors the node-execution lease pattern in ``session_execution.py`` /
``session_node_lifecycle.py`` but at the granularity of a single
:class:`DurableTurn` inside a :class:`DurableInteraction`:

- ``grant_durable_turn_lease`` hands a not-yet-completed turn to a worker
  (token/owner/expiry/last_heartbeat_at).
- ``heartbeat_durable_turn_lease`` extends the lease while the worker runs.
- ``release_durable_turn_lease`` clears the lease on success / handoff.
- ``recover_expired_durable_turn_leases`` finds turns whose lease has expired
  (a crashed worker) and marks the owning interaction RESUMABLE by setting
  ``recovered_from_turn_index`` to the lowest not-yet-completed turn index.

Only ``SessionService`` calls these (single-writer rule, AGENTS.md §3). Because
the durable aggregates are frozen pydantic models, every mutation rebuilds the
turn/interaction via ``model_copy`` and bumps the interaction ``version`` append
counter (consistent with ``session_durable_interactions.py``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from hydramind.control.interaction_state import DurableInteraction, DurableTurn
from hydramind.control.models import RuntimeSession
from hydramind.kernel.contracts import TurnStatus


class TurnLeaseError(RuntimeError):
    """Raised when durable turn lease validation fails."""


def turn_lease_ttl(ttl_seconds: int) -> timedelta:
    if ttl_seconds <= 0:
        raise TurnLeaseError("turn lease ttl_seconds must be positive")
    return timedelta(seconds=ttl_seconds)


def is_turn_lease_live(turn: DurableTurn, *, as_of: datetime) -> bool:
    if not turn.turn_lease_token or not turn.turn_lease_owner:
        return False
    if turn.turn_lease_expires_at is None:
        return False
    return turn.turn_lease_expires_at > as_of


def has_turn_lease_metadata(turn: DurableTurn) -> bool:
    return bool(
        turn.turn_lease_token
        or turn.turn_lease_owner
        or turn.turn_lease_expires_at is not None
    )


def _require(session: RuntimeSession, interaction_id: str) -> DurableInteraction:
    interaction = session.durable_interactions.get(interaction_id)
    if interaction is None:
        raise TurnLeaseError(
            f"durable interaction {interaction_id!r} not started for "
            f"session {session.id!r}"
        )
    return interaction


def _replace_turn(
    interaction: DurableInteraction,
    turn: DurableTurn,
    *,
    now: datetime,
) -> DurableInteraction:
    turns = tuple(
        turn if existing.turn_index == turn.turn_index else existing
        for existing in interaction.turns
    )
    return interaction.model_copy(
        update={
            "turns": turns,
            "version": interaction.version + 1,
            "updated_at": now,
        }
    )


def _ensure_turn(
    interaction: DurableInteraction,
    *,
    turn_index: int,
    agent_id: str,
    now: datetime,
) -> tuple[DurableInteraction, DurableTurn]:
    """Return (interaction, turn) for ``turn_index``, creating a PENDING turn.

    A turn lease can be granted for a turn that has not yet been recorded as a
    completed durable turn (the worker is about to run it). When the turn does
    not exist yet it is created PENDING; the caller then stamps the lease.
    """

    existing = interaction.turn_by_index(turn_index)
    if existing is not None:
        return interaction, existing
    turn = DurableTurn(
        id=f"turn-{interaction.id}-{turn_index}",
        interaction_id=interaction.id,
        turn_index=turn_index,
        agent_id=agent_id,
        status=TurnStatus.PENDING,
        created_at=now,
    )
    updated = interaction.model_copy(
        update={
            "turns": (*interaction.turns, turn),
            "version": interaction.version + 1,
            "updated_at": now,
        }
    )
    return updated, turn


def grant_durable_turn_lease(
    session: RuntimeSession,
    *,
    interaction_id: str,
    turn_index: int,
    agent_id: str,
    owner: str,
    ttl_seconds: int,
    lease_token: str | None,
    now: datetime,
) -> DurableInteraction:
    interaction = _require(session, interaction_id)
    interaction, turn = _ensure_turn(
        interaction, turn_index=turn_index, agent_id=agent_id, now=now
    )
    if turn.status is TurnStatus.COMPLETED:
        raise TurnLeaseError(
            f"turn {turn_index} of interaction {interaction_id!r} is completed"
        )
    owner_value = owner.strip()
    if not owner_value:
        raise TurnLeaseError("turn lease owner must not be empty")
    if is_turn_lease_live(turn, as_of=now):
        raise TurnLeaseError(
            f"turn {turn_index} of interaction {interaction_id!r} "
            "already has a live lease"
        )
    token = lease_token or f"turn-lease-{uuid.uuid4().hex[:24]}"
    if not token.strip():
        raise TurnLeaseError("turn lease token must not be empty")
    leased = turn.model_copy(
        update={
            "turn_lease_token": token,
            "turn_lease_owner": owner_value,
            "last_heartbeat_at": now,
            "turn_lease_expires_at": now + turn_lease_ttl(ttl_seconds),
        }
    )
    updated = _replace_turn(interaction, leased, now=now)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def assert_durable_turn_lease(
    turn: DurableTurn,
    lease_token: str,
    *,
    as_of: datetime,
    allow_expired: bool = False,
) -> None:
    token = lease_token.strip()
    if not token:
        raise TurnLeaseError("turn lease token must not be empty")
    if not turn.turn_lease_token or not turn.turn_lease_owner:
        raise TurnLeaseError(
            f"turn {turn.turn_index} does not have an active lease"
        )
    if turn.turn_lease_token != token:
        raise TurnLeaseError(f"turn {turn.turn_index} lease token mismatch")
    if turn.status is TurnStatus.COMPLETED:
        raise TurnLeaseError(f"turn {turn.turn_index} is already completed")
    if not allow_expired and not is_turn_lease_live(turn, as_of=as_of):
        raise TurnLeaseError(f"turn {turn.turn_index} lease expired")


def heartbeat_durable_turn_lease(
    session: RuntimeSession,
    *,
    interaction_id: str,
    turn_index: int,
    lease_token: str,
    ttl_seconds: int,
    now: datetime,
) -> DurableInteraction:
    interaction = _require(session, interaction_id)
    turn = interaction.turn_by_index(turn_index)
    if turn is None:
        raise TurnLeaseError(
            f"turn {turn_index} not found in interaction {interaction_id!r}"
        )
    assert_durable_turn_lease(turn, lease_token, as_of=now)
    refreshed = turn.model_copy(
        update={
            "last_heartbeat_at": now,
            "turn_lease_expires_at": now + turn_lease_ttl(ttl_seconds),
        }
    )
    updated = _replace_turn(interaction, refreshed, now=now)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


def clear_turn_lease(turn: DurableTurn) -> DurableTurn:
    return turn.model_copy(
        update={
            "turn_lease_token": None,
            "turn_lease_owner": None,
            "last_heartbeat_at": None,
            "turn_lease_expires_at": None,
        }
    )


def release_durable_turn_lease(
    session: RuntimeSession,
    *,
    interaction_id: str,
    turn_index: int,
    lease_token: str,
    completed: bool,
    now: datetime,
) -> DurableInteraction:
    interaction = _require(session, interaction_id)
    turn = interaction.turn_by_index(turn_index)
    if turn is None:
        raise TurnLeaseError(
            f"turn {turn_index} not found in interaction {interaction_id!r}"
        )
    assert_durable_turn_lease(turn, lease_token, as_of=now, allow_expired=True)
    cleared = clear_turn_lease(turn)
    if completed:
        cleared = cleared.model_copy(
            update={"status": TurnStatus.COMPLETED, "completed_at": now}
        )
    updated = _replace_turn(interaction, cleared, now=now)
    session.durable_interactions[interaction_id] = updated
    session.updated_at = now
    return updated


@dataclass(frozen=True)
class RecoveredDurableTurn:
    """Record of one expired turn lease that was recovered."""

    interaction_id: str
    node_key: str
    turn_index: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    recovered_from_turn_index: int


def _lowest_incomplete_turn_index(interaction: DurableInteraction) -> int:
    """Return the lowest turn index not recorded COMPLETED (the resume point)."""

    completed = {
        turn.turn_index
        for turn in interaction.turns
        if turn.status is TurnStatus.COMPLETED
    }
    index = 0
    while index in completed:
        index += 1
    return index


def recover_expired_durable_turn_leases(
    session: RuntimeSession,
    *,
    now: datetime,
) -> tuple[RecoveredDurableTurn, ...]:
    """Recover expired turn leases and mark their interactions resumable.

    A turn whose lease has expired (a crashed worker) is reverted to PENDING and
    its lease is cleared; the owning interaction's ``recovered_from_turn_index``
    is set to the lowest not-yet-completed turn index, so a fresh attempt resumes
    from there instead of replaying the whole node. A still-live lease blocks
    recovery (mirrors ``recover_expired_node_execution_leases``).
    """

    recovered: list[RecoveredDurableTurn] = []
    for interaction_id, interaction in list(session.durable_interactions.items()):
        current = interaction
        changed = False
        for turn in current.turns:
            if turn.status is TurnStatus.COMPLETED:
                continue
            if not has_turn_lease_metadata(turn):
                continue
            if is_turn_lease_live(turn, as_of=now):
                continue
            owner = turn.turn_lease_owner
            expires_at = turn.turn_lease_expires_at
            reverted = clear_turn_lease(turn).model_copy(
                update={"status": TurnStatus.PENDING}
            )
            current = _replace_turn(current, reverted, now=now)
            resume_index = _lowest_incomplete_turn_index(current)
            current = current.model_copy(
                update={"recovered_from_turn_index": resume_index}
            )
            changed = True
            recovered.append(
                RecoveredDurableTurn(
                    interaction_id=interaction_id,
                    node_key=current.node_key,
                    turn_index=turn.turn_index,
                    lease_owner=owner,
                    lease_expires_at=expires_at,
                    recovered_from_turn_index=resume_index,
                )
            )
        if changed:
            session.durable_interactions[interaction_id] = current
            session.updated_at = now
    return tuple(recovered)


__all__ = [
    "RecoveredDurableTurn",
    "TurnLeaseError",
    "assert_durable_turn_lease",
    "clear_turn_lease",
    "grant_durable_turn_lease",
    "has_turn_lease_metadata",
    "heartbeat_durable_turn_lease",
    "is_turn_lease_live",
    "recover_expired_durable_turn_leases",
    "release_durable_turn_lease",
    "turn_lease_ttl",
]
