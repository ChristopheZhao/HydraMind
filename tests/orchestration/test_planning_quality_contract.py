"""GoalSpec.quality_contract projection onto agent plans and apply_delta.

The goal->plan quality-contract + expected-artifacts projection is a surviving
feature (``with_goal_quality_contract`` / ``with_goal_expected_artifacts`` in
``planning_contracts``), exercised here through the agent planner
(``ModelGoalPlanner`` over a scripted ``MockProvider``) and the projection helper
directly -- there is no rule-based planner anymore.
"""

from __future__ import annotations

import json

import pytest

from hydramind.control import GoalArtifactQualityContract
from hydramind.orchestration import (
    ExecutionPlan,
    GoalSpec,
    ModelGoalPlanner,
    PlanDelta,
    PlanTaskSpec,
)
from hydramind.orchestration.planning_contracts import with_goal_quality_contract
from hydramind.testing import MockProvider, ScriptedTurn


def _quality() -> GoalArtifactQualityContract:
    return GoalArtifactQualityContract(
        min_length=1000,
        required_sections=("引言",),
        min_reference_urls=3,
        min_image_refs=1,
    )


def _planner_for(*task_keys: str) -> ModelGoalPlanner:
    """A scripted agent planner returning a deterministic plan with given tasks."""
    payload = {
        "tasks": [
            {"key": key, "role": "executor", "description": f"do {key}"}
            for key in task_keys
        ],
    }
    backend = MockProvider(scripted=[ScriptedTurn(content=json.dumps(payload))])
    return ModelGoalPlanner(backend)


@pytest.mark.asyncio
async def test_agent_plan_attaches_quality_contract_to_last_task() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="produce report",
        expected_artifacts=("report.md",),
        quality_contract=quality,
    )

    plan = await _planner_for("research", "write").initial_plan(goal)

    assert plan.tasks[0].contract.quality_contract is None
    assert plan.tasks[-1].contract.quality_contract == quality


@pytest.mark.asyncio
async def test_agent_plan_single_task_carries_quality_contract() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="produce a single artifact",
        expected_artifacts=("only.md",),
        quality_contract=quality,
    )

    plan = await _planner_for("work").initial_plan(goal)

    assert len(plan.tasks) == 1
    assert plan.tasks[0].key == "work"
    assert plan.tasks[0].contract.quality_contract == quality


@pytest.mark.asyncio
async def test_apply_delta_preserves_quality_contract_when_last_task_unchanged() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="ship blog",
        expected_artifacts=("blog.md",),
        quality_contract=quality,
    )
    plan = await _planner_for("plan", "write").initial_plan(goal)

    revised = plan.apply_delta(
        PlanDelta(
            update_tasks=(
                PlanTaskSpec(key="plan", role="planner", description="updated"),
            ),
            rationale="touch plan only",
        )
    )

    assert revised.tasks[-1].key == "write"
    assert revised.tasks[-1].contract.quality_contract == quality


@pytest.mark.asyncio
async def test_apply_delta_reattaches_quality_contract_to_new_tail_task() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="ship blog",
        expected_artifacts=("blog.md",),
        quality_contract=quality,
    )
    plan = await _planner_for("write").initial_plan(goal)
    assert plan.tasks[-1].contract.quality_contract == quality

    revised = plan.apply_delta(
        PlanDelta(
            add_tasks=(
                PlanTaskSpec(
                    key="polish",
                    role="reviewer",
                    requires=("write",),
                ),
            ),
            rationale="add reviewer at tail",
        )
    )

    assert revised.tasks[-1].key == "polish"
    assert revised.tasks[-1].contract.quality_contract == quality
    # Earlier task should no longer carry the (now duplicated) contract.
    assert revised.tasks[0].key == "write"


def test_with_goal_quality_contract_is_idempotent() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="ship",
        suggested_tasks=(PlanTaskSpec(key="write", role="writer"),),
        quality_contract=quality,
    )
    plan = ExecutionPlan(
        name="goal-ship",
        goal=goal,
        tasks=(PlanTaskSpec(key="write", role="writer"),),
    )
    once = with_goal_quality_contract(plan)
    twice = with_goal_quality_contract(once)
    assert once == twice
    assert once.tasks[-1].contract.quality_contract == quality


@pytest.mark.asyncio
async def test_apply_delta_raises_when_removing_all_tasks_with_quality_contract() -> None:
    quality = _quality()
    goal = GoalSpec(
        objective="ship",
        expected_artifacts=("out.md",),
        quality_contract=quality,
    )
    plan = await _planner_for("write").initial_plan(goal)

    with pytest.raises(ValueError):
        plan.apply_delta(PlanDelta(remove_task_keys=("write",)))
