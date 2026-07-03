"""Tests for the executed-capability dead-surface freeze (ADR-0007 / S90)."""

from __future__ import annotations

import pytest

from hydramind.mas import (
    EXECUTED_MODE_TOPOLOGY_PAIRS,
    AgentSpec,
    AggregationStrategy,
    ArbitrationStrategy,
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    SharedWorkspace,
    TeamSpec,
    UnexecutedProtocolError,
    executed_protocol_violations,
    require_executed_team,
)


def _team(protocol: CollaborationProtocol, workspace: SharedWorkspace | None = None) -> TeamSpec:
    return TeamSpec(
        id="team",
        members=(
            AgentSpec(id="a", role="writer"),
            AgentSpec(id="b", role="reviewer"),
        ),
        protocol=protocol,
        workspace=workspace,
    )


def test_default_protocol_is_within_executed_envelope() -> None:
    assert executed_protocol_violations(CollaborationProtocol()) == ()


def test_coordinator_arbitration_and_none_are_executed() -> None:
    assert (
        executed_protocol_violations(
            CollaborationProtocol(
                arbitration=ArbitrationStrategy.COORDINATOR, coordinator_id="a"
            )
        )
        == ()
    )
    assert (
        executed_protocol_violations(
            CollaborationProtocol(arbitration=ArbitrationStrategy.NONE)
        )
        == ()
    )


@pytest.mark.parametrize(
    "protocol",
    [
        # S100 executed the last frozen per-axis values; every CollaborationMode,
        # topology, aggregation, and arbitration value is now within the envelope.
        CollaborationProtocol(mode=CollaborationMode.DEBATE),
        CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            aggregation=AggregationStrategy.VOTE,
            arbitration=ArbitrationStrategy.MAJORITY,
        ),
        CollaborationProtocol(
            mode=CollaborationMode.DELEGATION,
            topology=CollaborationTopology.COORDINATOR,
            coordinator_id="a",
        ),
        CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            aggregation=AggregationStrategy.VOTE,
            arbitration=ArbitrationStrategy.MAJORITY,
        ),
        CollaborationProtocol(arbitration=ArbitrationStrategy.MAJORITY),
    ],
)
def test_all_per_axis_values_are_now_executed(protocol: CollaborationProtocol) -> None:
    # After S100 there is nothing frozen per-axis: the executed envelope equals the
    # full advertised CollaborationProtocol surface (execute-or-remove COMPLETE).
    assert executed_protocol_violations(protocol) == ()


def test_executed_envelope_equals_full_enums() -> None:
    from hydramind.mas.capability import (
        EXECUTED_AGGREGATIONS,
        EXECUTED_ARBITRATIONS,
        EXECUTED_MODES,
        EXECUTED_TOPOLOGIES,
    )

    assert EXECUTED_MODES == frozenset(CollaborationMode)
    assert EXECUTED_TOPOLOGIES == frozenset(CollaborationTopology)
    assert EXECUTED_AGGREGATIONS == frozenset(AggregationStrategy)
    assert EXECUTED_ARBITRATIONS == frozenset(ArbitrationStrategy)


def test_executed_mode_topology_matrix_names_supported_pairs() -> None:
    assert EXECUTED_MODE_TOPOLOGY_PAIRS == frozenset(
        {
            (CollaborationMode.TEAM, CollaborationTopology.BROADCAST),
            (CollaborationMode.TEAM, CollaborationTopology.PIPELINE),
            (CollaborationMode.TEAM, CollaborationTopology.COORDINATOR),
            (CollaborationMode.DEBATE, CollaborationTopology.BROADCAST),
            (CollaborationMode.VOTE, CollaborationTopology.BROADCAST),
            (CollaborationMode.DELEGATION, CollaborationTopology.COORDINATOR),
        }
    )


@pytest.mark.parametrize(
    "protocol",
    [
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.PIPELINE,
        ),
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.COORDINATOR,
            coordinator_id="a",
        ),
    ],
)
def test_executed_protocol_violations_rejects_unsupported_mode_topology_pairs(
    protocol: CollaborationProtocol,
) -> None:
    assert executed_protocol_violations(protocol) == (
        "mode/topology pair "
        f"{protocol.mode.value!r}/{protocol.topology.value!r} is not executed",
    )
    with pytest.raises(UnexecutedProtocolError, match="mode/topology pair"):
        require_executed_team(_team(protocol))


def test_pipeline_topology_is_executed() -> None:
    # S94 widened the executed envelope by exactly one topology (lock-step with
    # PipelineStrategy); a TEAM/PIPELINE team is now within the envelope.
    assert executed_protocol_violations(
        CollaborationProtocol(topology=CollaborationTopology.PIPELINE)
    ) == ()
    require_executed_team(
        _team(CollaborationProtocol(topology=CollaborationTopology.PIPELINE))
    )


def test_coordinator_topology_and_summary_aggregation_are_executed() -> None:
    # S95 widened the envelope lock-step with CoordinatorStrategy: COORDINATOR
    # topology + COORDINATOR_SUMMARY aggregation are now executed.
    assert executed_protocol_violations(
        CollaborationProtocol(
            topology=CollaborationTopology.COORDINATOR,
            aggregation=AggregationStrategy.COORDINATOR_SUMMARY,
            coordinator_id="a",
        )
    ) == ()
    require_executed_team(
        _team(
            CollaborationProtocol(
                topology=CollaborationTopology.COORDINATOR,
                aggregation=AggregationStrategy.COORDINATOR_SUMMARY,
                coordinator_id="a",
            )
        )
    )


def test_workspace_is_a_scope_marker_with_no_executable_refs() -> None:
    # S102 (execute-or-remove): SharedWorkspace.artifact_refs/memory_refs were
    # REMOVED (collaboration flows through the message-passing kernel seam, not
    # workspace references), so a workspace no longer affects the executed envelope.
    assert not hasattr(SharedWorkspace(id="w"), "artifact_refs")
    assert not hasattr(SharedWorkspace(id="w"), "memory_refs")
    require_executed_team(_team(CollaborationProtocol(), workspace=SharedWorkspace(id="w")))


def test_require_executed_team_passes_for_executed_team() -> None:
    require_executed_team(_team(CollaborationProtocol(coordinator_id="a")))


def test_require_executed_team_passes_for_debate_after_s100() -> None:
    # S100 executed DEBATE; the per-axis envelope no longer flags it.
    require_executed_team(_team(CollaborationProtocol(mode=CollaborationMode.DEBATE)))
