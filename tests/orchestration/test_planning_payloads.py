"""Planner payload normalization boundary tests."""

from __future__ import annotations

import pytest

from hydramind.orchestration import GoalSpec
from hydramind.orchestration.planning_payloads import (
    execution_plan_from_payload,
    plan_delta_from_payload,
    strict_json_object,
    truncate_planner_response,
)


def test_strict_json_object_accepts_embedded_object() -> None:
    assert strict_json_object("analysis\n{\"ok\": true}\ntrailer") == {"ok": True}


def test_truncate_planner_response_keeps_bounded_repair_payload() -> None:
    payload = "x" * 13000

    truncated = truncate_planner_response(payload)

    assert len(truncated) < len(payload)
    assert truncated.startswith("x" * 12000)
    assert truncated.endswith("\n...[truncated]")


def test_execution_plan_payload_rejects_undeclared_tools() -> None:
    goal = GoalSpec(objective="write report", available_tools=("search.web",))
    payload = {
        "tasks": [
            {
                "key": "research",
                "tools": ["image.generate"],
            }
        ]
    }

    with pytest.raises(ValueError, match="uses undeclared tool"):
        execution_plan_from_payload(payload, goal)


def test_execution_plan_payload_preserves_native_team_and_goal_artifacts() -> None:
    goal = GoalSpec(
        objective="write report",
        available_tools=("search.web",),
        expected_artifacts=("report.md",),
    )
    payload = {
        "name": "goal-report",
        "tasks": [
            {
                "key": "research",
                "role": "researcher",
                "tools": ["search.web"],
                "team": {
                    "id": "research-team",
                    "tools": ["search.web"],
                    "members": [
                        {
                            "id": "researcher",
                            "role": "researcher",
                            "tools": ["search.web"],
                        }
                    ],
                },
            }
        ],
    }

    plan = execution_plan_from_payload(payload, goal)

    assert plan.name == "goal-report"
    assert plan.tasks[0].team is not None
    assert plan.tasks[0].team.id == "research-team"
    assert plan.tasks[0].contract.expected_artifacts == ("report.md",)


def test_plan_delta_payload_uses_feedback_when_refs_omitted() -> None:
    goal = GoalSpec(objective="revise")
    delta = plan_delta_from_payload(
        {"add_tasks": [{"key": "review"}]},
        goal,
        ("quality failed",),
    )

    assert delta.feedback_refs == ("quality failed",)
    assert delta.add_tasks[0].key == "review"
