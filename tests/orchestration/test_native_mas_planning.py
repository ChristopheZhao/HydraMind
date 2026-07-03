"""Native MAS specs through planning contracts."""

from __future__ import annotations

import json

import pytest

from hydramind.mas import (
    AgentSpec,
    CollaborationProtocol,
    SharedWorkspace,
    TeamSpec,
)
from hydramind.orchestration import (
    ExecutionPlan,
    GoalSpec,
    ModelGoalPlanner,
    PlanTaskSpec,
)
from hydramind.testing import MockProvider, ScriptedTurn


def _team() -> TeamSpec:
    return TeamSpec(
        id="writing-team",
        tools=("artifact.write_text",),
        members=(
            AgentSpec(
                id="writer",
                role="writer",
                tools=("artifact.write_text",),
            ),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol=CollaborationProtocol(coordinator_id="reviewer"),
        workspace=SharedWorkspace(id="draft-workspace"),
    )


def test_plan_task_projects_team_without_raw_subagent_config() -> None:
    task = PlanTaskSpec(
        key="draft",
        role="coordinator",
        team=_team(),
        tools=("artifact.write_text",),
    )

    node = task.to_workflow_node()

    assert node.config["execution_mode"] == "team"
    assert node.config["mas_team"]["id"] == "writing-team"
    assert node.config["mas_team"]["protocol"]["coordinator_id"] == "reviewer"
    assert "subagents" not in node.config


def test_execution_plan_metadata_preserves_native_mas_specs() -> None:
    goal = GoalSpec(objective="write", available_tools=("artifact.write_text",))
    plan = ExecutionPlan(
        name="native-mas-plan",
        goal=goal,
        tasks=(
            PlanTaskSpec(
                key="draft",
                role="coordinator",
                team=_team(),
                tools=("artifact.write_text",),
            ),
        ),
    )

    restored = ExecutionPlan.model_validate(plan.as_session_metadata()["execution_plan"])

    assert restored.tasks[0].team == _team()
    assert restored.to_blueprint().nodes[0].config["execution_mode"] == "team"


@pytest.mark.asyncio
async def test_model_planner_accepts_native_team_payload() -> None:
    payload = {
        "name": "model-team-plan",
        "rationale": "model selected native team",
        "tasks": [
            {
                "key": "draft",
                "role": "coordinator",
                "description": "Draft and review.",
                "tools": ["artifact.write_text"],
                "team": _team().model_dump(mode="json"),
            }
        ],
    }
    planner = ModelGoalPlanner(
        MockProvider(scripted=[ScriptedTurn(content=json.dumps(payload))])
    )

    plan = await planner.initial_plan(
        GoalSpec(
            objective="write with a team",
            available_tools=("artifact.write_text",),
        )
    )

    assert plan.tasks[0].team == _team()
    assert plan.to_blueprint().nodes[0].config["mas_team"]["id"] == "writing-team"


def test_team_member_tools_must_be_allowed_by_task_tools() -> None:
    with pytest.raises(ValueError, match="declares tools but task allows no tools"):
        PlanTaskSpec(
            key="draft",
            role="coordinator",
            team=_team(),
        )
