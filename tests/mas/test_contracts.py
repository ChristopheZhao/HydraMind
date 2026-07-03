"""Native MAS contract validation."""

from __future__ import annotations

import pytest

from hydramind.mas import (
    AgentSpec,
    AggregationStrategy,
    ArbitrationStrategy,
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    SharedWorkspace,
    TeamSpec,
    WorkspaceScope,
)


def test_public_native_mas_contracts_round_trip_json() -> None:
    team = TeamSpec(
        id="analysis-team",
        tools=("artifact.write_text",),
        members=(
            AgentSpec(
                id="researcher",
                role="researcher",
                description="Find facts",
                tools=("artifact.write_text",),
            ),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol=CollaborationProtocol(
            mode=CollaborationMode.TEAM,
            coordinator_id="reviewer",
        ),
        workspace=SharedWorkspace(
            id="ws-analysis",
            scope=WorkspaceScope.TEAM,
            metadata={"purpose": "draft review"},
        ),
    )

    payload = team.model_dump(mode="json")
    restored = TeamSpec.model_validate(payload)

    assert restored == team
    assert payload["protocol"]["mode"] == "team"
    assert restored.declared_tools() == ("artifact.write_text",)


@pytest.mark.parametrize("field_name", ["artifact_refs", "memory_refs"])
def test_shared_workspace_rejects_removed_reference_fields(field_name: str) -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        SharedWorkspace(id="ws", **{field_name: ("removed",)})


def test_team_rejects_duplicate_member_ids() -> None:
    with pytest.raises(ValueError, match="duplicate team member"):
        TeamSpec(
            id="dup-team",
            members=(
                AgentSpec(id="same", role="researcher"),
                AgentSpec(id="same", role="reviewer"),
            ),
        )


def test_team_rejects_unknown_coordinator_id() -> None:
    with pytest.raises(ValueError, match="is not a team member"):
        TeamSpec(
            id="bad-coordinator",
            members=(AgentSpec(id="writer", role="writer"),),
            protocol=CollaborationProtocol(coordinator_id="reviewer"),
        )


def test_team_member_tools_must_fit_team_tool_contract() -> None:
    with pytest.raises(ValueError, match="outside team tools"):
        TeamSpec(
            id="tool-team",
            tools=("search.web",),
            members=(
                AgentSpec(
                    id="image",
                    role="designer",
                    tools=("image.generate",),
                ),
            ),
        )


def test_vote_mode_requires_vote_aggregation_and_broadcast_topology() -> None:
    with pytest.raises(ValueError, match="vote mode requires aggregation=vote"):
        CollaborationProtocol(mode=CollaborationMode.VOTE)

    with pytest.raises(ValueError, match="vote mode requires topology=broadcast"):
        CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            topology=CollaborationTopology.COORDINATOR,
            aggregation=AggregationStrategy.VOTE,
            arbitration=ArbitrationStrategy.MAJORITY,
            coordinator_id="lead",
        )


def test_vote_mode_requires_majority_arbitration() -> None:
    with pytest.raises(ValueError, match="vote mode requires arbitration=majority"):
        CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            aggregation=AggregationStrategy.VOTE,
        )

    with pytest.raises(ValueError, match="vote mode requires arbitration=majority"):
        CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            aggregation=AggregationStrategy.VOTE,
            arbitration=ArbitrationStrategy.NONE,
        )

    assert CollaborationProtocol(
        mode=CollaborationMode.VOTE,
        aggregation=AggregationStrategy.VOTE,
        arbitration=ArbitrationStrategy.MAJORITY,
    ).arbitration is ArbitrationStrategy.MAJORITY


def test_vote_aggregation_requires_vote_mode() -> None:
    with pytest.raises(ValueError, match="vote aggregation requires mode=vote"):
        CollaborationProtocol(aggregation=AggregationStrategy.VOTE)


def test_debate_rounds_metadata_defaults_and_accepts_positive_integer() -> None:
    assert CollaborationProtocol(mode=CollaborationMode.DEBATE).metadata == {}
    assert CollaborationProtocol(
        mode=CollaborationMode.DEBATE,
        metadata={"rounds": 1},
    ).metadata == {"rounds": 1}


@pytest.mark.parametrize("rounds", ["1", True, 0, -1])
def test_debate_rounds_metadata_must_be_positive_integer(rounds: object) -> None:
    with pytest.raises(ValueError, match=r"debate metadata\.rounds"):
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            metadata={"rounds": rounds},
        )


def test_coordinator_summary_requires_coordinator_topology_and_identity() -> None:
    with pytest.raises(
        ValueError,
        match="coordinator_summary aggregation requires topology=coordinator",
    ):
        CollaborationProtocol(aggregation=AggregationStrategy.COORDINATOR_SUMMARY)

    with pytest.raises(
        ValueError,
        match=r"coordinator_summary aggregation requires protocol\.coordinator_id",
    ):
        CollaborationProtocol(
            topology=CollaborationTopology.COORDINATOR,
            aggregation=AggregationStrategy.COORDINATOR_SUMMARY,
        )


def test_coordinator_topology_requires_coordinator_identity() -> None:
    with pytest.raises(
        ValueError,
        match=r"coordinator topology requires protocol\.coordinator_id",
    ):
        CollaborationProtocol(topology=CollaborationTopology.COORDINATOR)


def test_delegation_requires_coordinator_topology_and_identity() -> None:
    with pytest.raises(ValueError, match="delegation mode requires topology=coordinator"):
        CollaborationProtocol(mode=CollaborationMode.DELEGATION)

    with pytest.raises(
        ValueError,
        match=r"delegation mode requires protocol\.coordinator_id",
    ):
        CollaborationProtocol(
            mode=CollaborationMode.DELEGATION,
            topology=CollaborationTopology.COORDINATOR,
        )
