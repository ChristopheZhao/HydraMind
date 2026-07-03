"""Native team interaction runtime boundary tests."""

from __future__ import annotations

from hydramind.harness import MessageRole
from hydramind.mas import AgentSpec, SharedWorkspace, TeamSpec
from hydramind.observability.event_details import EVENT_DETAIL_SCHEMA_VERSION
from hydramind.orchestration.collaboration_runtime import (
    NativeTeamInteractionRuntime,
)


def test_pipeline_runtime_threads_prior_output_as_seed_message() -> None:
    team = TeamSpec(
        id="pipeline-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    runtime = NativeTeamInteractionRuntime(team=team, interaction_id="interaction-exec")

    first = runtime.next_turn()
    assert first is not None
    assert first.member.id == "writer"
    assert first.seed_messages == ()
    assert first.is_coordinator_handoff is False
    assert first.turn_detail == {
        "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
        "interaction_id": "interaction-exec",
        "turn_index": 0,
        "team_id": "pipeline-team",
        "role": "writer",
        "topology": "pipeline",
    }

    runtime.record_member_message(scheduled_turn=first, content="draft")
    second = runtime.next_turn()

    assert second is not None
    assert second.member.id == "reviewer"
    assert second.turn_detail["turn_index"] == 1
    assert len(second.seed_messages) == 1
    seed = second.seed_messages[0]
    assert seed.role is MessageRole.ASSISTANT
    assert seed.name == "writer"
    assert seed.content == "draft"


def test_workspace_id_projects_into_interaction_and_turn_detail() -> None:
    team = TeamSpec(
        id="workspace-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
        workspace=SharedWorkspace(
            id="draft-room",
            scope="team",
            metadata={"purpose": "draft review"},
        ),
    )
    runtime = NativeTeamInteractionRuntime(team=team, interaction_id="interaction-exec")

    first = runtime.next_turn()

    assert runtime.workspace_id == "draft-room"
    assert first is not None
    assert first.workspace_id == "draft-room"
    assert first.turn_detail["workspace_id"] == "draft-room"
    assert first.turn_detail == {
        "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
        "interaction_id": "interaction-exec",
        "turn_index": 0,
        "team_id": "workspace-team",
        "role": "writer",
        "topology": "pipeline",
        "workspace_id": "draft-room",
    }


def test_coordinator_runtime_marks_handoff_and_threads_worker_outputs() -> None:
    team = TeamSpec(
        id="coordinator-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
            AgentSpec(id="lead", role="lead"),
        ),
        protocol={
            "topology": "coordinator",
            "aggregation": "coordinator_summary",
            "coordinator_id": "lead",
        },
    )
    runtime = NativeTeamInteractionRuntime(team=team, interaction_id="interaction-exec")

    writer = runtime.next_turn()
    assert writer is not None
    assert writer.member.id == "writer"
    assert writer.is_coordinator_handoff is False
    assert writer.seed_messages == ()
    runtime.record_member_message(scheduled_turn=writer, content="draft")

    reviewer = runtime.next_turn()
    assert reviewer is not None
    assert reviewer.member.id == "reviewer"
    assert reviewer.is_coordinator_handoff is False
    assert reviewer.seed_messages == ()
    runtime.record_member_message(scheduled_turn=reviewer, content="review")

    lead = runtime.next_turn()
    assert lead is not None
    assert lead.member.id == "lead"
    assert lead.is_coordinator_handoff is True
    assert lead.turn_detail == {
        "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
        "interaction_id": "interaction-exec",
        "turn_index": 2,
        "team_id": "coordinator-team",
        "role": "lead",
        "topology": "coordinator",
    }
    assert [(message.name, message.content) for message in lead.seed_messages] == [
        ("writer", "draft"),
        ("reviewer", "review"),
    ]
