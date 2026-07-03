"""Kernel primitive contract tests (S91)."""

from __future__ import annotations

import pytest

from hydramind.kernel import (
    Interaction,
    InteractionStatus,
    Message,
    MessageRole,
    Turn,
    TurnStatus,
)
from hydramind.mas import CollaborationProtocol, CollaborationTopology


def test_message_defaults_and_validation() -> None:
    msg = Message(id="m1", interaction_id="i1", sender="writer", content="hi")
    assert msg.role is MessageRole.AGENT
    assert msg.turn_index == 0
    with pytest.raises(ValueError, match="must not be empty"):
        Message(id=" ", interaction_id="i1", sender="writer")
    with pytest.raises(ValueError, match="turn_index must be non-negative"):
        Message(id="m1", interaction_id="i1", sender="writer", turn_index=-1)


def test_turn_validation() -> None:
    turn = Turn(index=0, agent_id="writer")
    assert turn.status is TurnStatus.PENDING
    with pytest.raises(ValueError, match="must not be empty"):
        Turn(index=0, agent_id="")
    with pytest.raises(ValueError, match="non-negative"):
        Turn(index=-1, agent_id="writer")


def test_interaction_construction_and_helpers() -> None:
    interaction = Interaction(
        id="i1",
        team_id="team",
        member_ids=("writer", "reviewer"),
        protocol=CollaborationProtocol(coordinator_id="reviewer"),
        turns=(Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),),
    )
    assert interaction.status is InteractionStatus.PENDING
    assert interaction.acted_agent_ids() == ("writer",)


def test_interaction_requires_members() -> None:
    with pytest.raises(ValueError, match="at least one member"):
        Interaction(id="i1", team_id="team", member_ids=())


def test_interaction_rejects_duplicate_members() -> None:
    with pytest.raises(ValueError, match="duplicate member"):
        Interaction(id="i1", team_id="team", member_ids=("a", "a"))


def test_interaction_rejects_turn_for_unknown_member() -> None:
    with pytest.raises(ValueError, match="not a declared member"):
        Interaction(
            id="i1",
            team_id="team",
            member_ids=("writer",),
            turns=(Turn(index=0, agent_id="ghost"),),
        )


def test_interaction_rejects_unknown_coordinator() -> None:
    with pytest.raises(ValueError, match="not a member"):
        Interaction(
            id="i1",
            team_id="team",
            member_ids=("writer",),
            protocol=CollaborationProtocol(coordinator_id="reviewer"),
        )


def test_interaction_rejects_coordinator_topology_without_identity() -> None:
    with pytest.raises(ValueError, match="coordinator topology requires"):
        Interaction(
            id="i1",
            team_id="team",
            member_ids=("writer", "reviewer"),
            protocol=CollaborationProtocol(
                topology=CollaborationTopology.COORDINATOR
            ),
        )
