"""S5b — durable turn lease + expired-turn recovery (control-owned).

Mirrors the node-execution lease tests: grant/heartbeat/release behave with
token validation; an expired turn lease is recovered (turn reverted to PENDING,
interaction marked resumable via ``recovered_from_turn_index``); a still-live
lease blocks recovery. Only ``SessionService`` (via ``ControlPlane``) mutates
the durable lease fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hydramind.control import (
    ControlPlane,
    SessionService,
    TurnLeaseError,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    durable_interaction_id,
)
from hydramind.control.store import InMemorySessionStore
from hydramind.kernel.contracts import TurnStatus

NODE_KEY = "collaborate"


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="turn-lease-wf",
        nodes=(WorkflowNodeSpec(key=NODE_KEY, role="coordinator"),),
    )


async def _control() -> tuple[ControlPlane, SessionService, str, str]:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    interaction_id = durable_interaction_id(session.id, NODE_KEY)
    await control.start_interaction(
        session.id,
        interaction_id=interaction_id,
        node_key=NODE_KEY,
        execution_id="exec-1",
        team_id="team",
        protocol_mode="team",
        topology="pipeline",
        member_ids=("writer", "reviewer"),
    )
    return control, service, session.id, interaction_id


@pytest.mark.asyncio
async def test_grant_heartbeat_release_turn_lease() -> None:
    control, _service, session_id, interaction_id = await _control()

    granted = await control.grant_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=0,
        agent_id="writer",
        owner="worker-a",
        ttl_seconds=120,
    )
    turn = granted.turn_by_index(0)
    assert turn is not None
    assert turn.turn_lease_owner == "worker-a"
    assert turn.turn_lease_token is not None
    assert turn.turn_lease_expires_at is not None
    assert turn.status is TurnStatus.PENDING
    token = turn.turn_lease_token

    # A second grant while a live lease is held is rejected.
    with pytest.raises(TurnLeaseError, match="already has a live lease"):
        await control.grant_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=0,
            agent_id="writer",
            owner="worker-b",
            ttl_seconds=120,
        )

    # Heartbeat extends the lease (token must match).
    heart = await control.heartbeat_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=0,
        lease_token=token,
        ttl_seconds=120,
    )
    assert heart.turn_by_index(0).last_heartbeat_at is not None

    with pytest.raises(TurnLeaseError, match="token mismatch"):
        await control.heartbeat_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=0,
            lease_token="wrong-token",
            ttl_seconds=120,
        )

    # Release on success clears the lease and marks the turn COMPLETED.
    released = await control.release_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=0,
        lease_token=token,
        completed=True,
    )
    done = released.turn_by_index(0)
    assert done is not None
    assert done.status is TurnStatus.COMPLETED
    assert done.turn_lease_token is None
    assert done.turn_lease_owner is None
    assert done.turn_lease_expires_at is None
    assert done.completed_at is not None


@pytest.mark.asyncio
async def test_expired_turn_lease_is_recovered_and_marks_resumable() -> None:
    control, service, session_id, interaction_id = await _control()

    # Turn 0 already completed in a prior attempt.
    await control.release_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=0,
        lease_token=(
            await control.grant_turn_lease(
                session_id,
                interaction_id=interaction_id,
                turn_index=0,
                agent_id="writer",
                owner="worker-a",
                ttl_seconds=120,
            )
        ).turn_by_index(0).turn_lease_token,
        completed=True,
    )

    # Turn 1 leased with a SHORT ttl, then time advances past expiry (crash).
    granted = await control.grant_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=1,
        agent_id="reviewer",
        owner="worker-b",
        ttl_seconds=1,
    )
    leased_turn = granted.turn_by_index(1)
    assert leased_turn is not None
    # Force expiry by rewinding the lease expiry into the past via the service
    # helper (use as_of in the future for recovery instead — see below).

    # A not-yet-expired lease blocks recovery.
    recovered_none = await service.recover_expired_turn_leases(
        session_id, as_of=datetime.now(UTC)
    )
    assert recovered_none == ()

    # Recover as-of a time well past the 1s ttl: the turn lease is expired.
    future = datetime.now(UTC) + timedelta(seconds=60)
    recovered = await service.recover_expired_turn_leases(session_id, as_of=future)
    assert len(recovered) == 1
    rec = recovered[0]
    assert rec.interaction_id == interaction_id
    assert rec.turn_index == 1
    assert rec.lease_owner == "worker-b"
    # The lowest not-yet-completed turn index is 1 (turn 0 is completed).
    assert rec.recovered_from_turn_index == 1

    interaction = await control.get_durable_interaction(session_id, interaction_id)
    assert interaction is not None
    # The expired turn is reverted to PENDING with its lease cleared.
    reverted = interaction.turn_by_index(1)
    assert reverted is not None
    assert reverted.status is TurnStatus.PENDING
    assert reverted.turn_lease_token is None
    assert reverted.turn_lease_owner is None
    # The interaction is now marked resumable.
    assert interaction.recovered_from_turn_index == 1
    # Turn 0 remains COMPLETED (not reverted).
    assert interaction.turn_by_index(0).status is TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_cannot_lease_completed_turn() -> None:
    control, _service, session_id, interaction_id = await _control()
    token = (
        await control.grant_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=0,
            agent_id="writer",
            owner="worker-a",
            ttl_seconds=120,
        )
    ).turn_by_index(0).turn_lease_token
    await control.release_turn_lease(
        session_id,
        interaction_id=interaction_id,
        turn_index=0,
        lease_token=token,
        completed=True,
    )
    with pytest.raises(TurnLeaseError, match="completed"):
        await control.grant_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=0,
            agent_id="writer",
            owner="worker-c",
            ttl_seconds=120,
        )
