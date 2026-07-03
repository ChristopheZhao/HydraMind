"""Model-backed goal planner."""

from __future__ import annotations

import json

import pytest

import hydramind.orchestration as orchestration_api
import hydramind.orchestration.builtin_prompts.planner as planner_prompts
import hydramind.orchestration.planning as planning_module
import hydramind.orchestration.planning_diagnostics as planning_diagnostics
import hydramind.orchestration.planning_invocation as planning_invocation
from hydramind.memory import InMemoryMemoryStore, MemoryScope
from hydramind.orchestration import (
    GoalSpec,
    MemoryContextPolicy,
    MemoryContextQuery,
    ModelGoalPlanner,
    PlanDelta,
    StoreMemoryContextRetriever,
)
from hydramind.testing import MockProvider, ScriptedTurn


def test_model_goal_planner_helpers_stay_on_internal_boundaries() -> None:
    assert callable(planner_prompts.render_initial_plan_prompt)
    assert callable(planner_prompts.render_revise_plan_prompt)
    assert callable(planner_prompts.render_planner_json_repair_prompt)
    assert callable(planning_diagnostics.planner_diagnostics)
    assert callable(planning_diagnostics.with_planner_diagnostics)
    assert callable(planning_diagnostics.with_delta_diagnostics)
    assert callable(planning_invocation.PlannerJsonInvoker)
    assert not hasattr(planning_module, "StaticGoalPlanner")
    assert not hasattr(planning_module, "FallbackGoalPlanner")
    assert "_initial_plan_prompt" not in planning_module.__dict__
    assert "_revise_plan_prompt" not in planning_module.__dict__
    assert "_planner_json_repair_prompt" not in planning_module.__dict__
    assert "_planner_diagnostics" not in planning_module.__dict__
    assert "_with_planner_diagnostics" not in planning_module.__dict__
    assert not hasattr(ModelGoalPlanner, "_invoke_planner_json")
    assert not hasattr(ModelGoalPlanner, "_repair_planner_json")
    assert not hasattr(ModelGoalPlanner, "_invoke_harness")
    assert "builtin_prompts" not in orchestration_api.__all__
    assert "planning_diagnostics" not in orchestration_api.__all__
    assert "planning_invocation" not in orchestration_api.__all__
    assert "planning_static" not in orchestration_api.__all__


@pytest.mark.asyncio
async def test_model_goal_planner_builds_execution_plan_from_json() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "name": "goal-research-report",
                  "rationale": "split research and writing",
                  "tasks": [
                    {
                      "key": "research",
                      "role": "researcher",
                      "description": "collect facts",
                      "tools": ["search.web"],
                      "acceptance_criteria": ["facts gathered"]
                    },
                    {
                      "key": "write",
                      "role": "writer",
                      "description": "write report",
                      "requires": ["research"],
                      "tools": ["artifact.write_text"],
                      "execution_mode": "subagent",
                      "expected_artifacts": ["report.md"]
                    }
                  ]
                }
                """
            )
        ]
    )
    goal = GoalSpec(
        objective="Research HydraMind and write report",
        available_tools=("search.web", "artifact.write_text"),
    )

    plan = await ModelGoalPlanner(backend).initial_plan(goal)

    assert plan.name == "goal-research-report"
    assert [task.key for task in plan.tasks] == ["research", "write"]
    assert plan.tasks[1].requires == ("research",)
    assert plan.tasks[1].execution_mode == "subagent"
    assert plan.tasks[1].contract.expected_artifacts == ("report.md",)
    assert plan.metadata["planner_diagnostics"] == {
        "planner": "ModelGoalPlanner",
        "operation": "initial_plan",
        "status": "succeeded",
        "invoke_attempts": 1,
        "retry_count": 0,
        "repair_count": 0,
        "max_invoke_retries": 1,
        "max_json_repairs": 1,
        "phases": ["initial"],
    }
    assert backend.invocations[0]["role"] == "planner"
    assert backend.invocations[0]["system"] == planner_prompts.PLANNER_SYSTEM


@pytest.mark.asyncio
async def test_model_goal_planner_injects_opted_in_memory_context() -> None:
    store = InMemoryMemoryStore()
    await store.append(
        MemoryScope.WORKFLOW,
        "goal-research-report",
        "episode.previous",
        {"finding": "prefer concise reports"},
    )
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "name": "goal-with-memory",
                  "tasks": [
                    {"key": "write", "role": "writer"}
                  ]
                }
                """
            )
        ]
    )
    goal = GoalSpec(
        objective="Write a report using prior lessons",
        memory_context=MemoryContextPolicy(
            enabled=True,
            queries=(
                MemoryContextQuery(
                    scope=MemoryScope.WORKFLOW,
                    scope_id="goal-research-report",
                    key_prefix="episode.",
                    limit=1,
                ),
            ),
        ),
    )

    plan = await ModelGoalPlanner(
        backend,
        memory_retriever=StoreMemoryContextRetriever(store),
    ).initial_plan(goal)

    prompt = json.loads(backend.invocations[0]["messages"][0].content)
    entries = prompt["memory_context"]["entries"]
    assert entries[0]["value"] == {"finding": "prefer concise reports"}
    assert entries[0]["evidence_ref"].startswith(
        "memory://workflow/goal-research-report/"
    )
    assert "memory_context" not in plan.metadata


@pytest.mark.asyncio
async def test_model_goal_planner_requires_policy_opt_in_for_memory_context() -> None:
    store = InMemoryMemoryStore()
    await store.append(MemoryScope.GLOBAL, "global", "episode.any", {"finding": "x"})
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content='{"name": "goal-no-memory", "tasks": [{"key": "work"}]}'
            )
        ]
    )

    await ModelGoalPlanner(
        backend,
        memory_retriever=StoreMemoryContextRetriever(store),
    ).initial_plan(GoalSpec(objective="do work"))

    prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert "memory_context" not in prompt


@pytest.mark.asyncio
async def test_model_goal_planner_preserves_goal_expected_artifacts() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "name": "goal-delivery-report",
                  "tasks": [
                    {
                      "key": "write",
                      "role": "writer",
                      "description": "write the report"
                    }
                  ]
                }
                """
            )
        ]
    )
    goal = GoalSpec(
        objective="Write the delivery report",
        expected_artifacts=("reports/delivery.md",),
    )

    plan = await ModelGoalPlanner(backend).initial_plan(goal)

    assert plan.tasks[0].contract.expected_artifacts == ("reports/delivery.md",)


@pytest.mark.asyncio
async def test_model_goal_planner_retries_transient_invoke_failure() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "name": "goal-after-retry",
                  "tasks": [
                    {"key": "work", "role": "executor"}
                  ]
                }
                """
            )
        ]
    )
    original_invoke = backend.complete
    calls = 0

    async def flaky_invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("read operation timed out")
        return await original_invoke(*args, **kwargs)

    backend.complete = flaky_invoke  # type: ignore[method-assign]

    plan = await ModelGoalPlanner(backend).initial_plan(GoalSpec(objective="do work"))

    assert plan.name == "goal-after-retry"
    assert plan.metadata["planner"] == "model"
    assert plan.metadata["planner_diagnostics"]["invoke_attempts"] == 2
    assert plan.metadata["planner_diagnostics"]["retry_count"] == 1
    assert calls == 2


@pytest.mark.asyncio
async def test_model_goal_planner_exhausted_retry_surfaces_error() -> None:
    backend = MockProvider()
    calls = 0

    async def failing_invoke(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        raise TimeoutError("read operation timed out")

    backend.complete = failing_invoke  # type: ignore[method-assign]

    with pytest.raises(TimeoutError, match="read operation timed out"):
        await ModelGoalPlanner(backend, max_invoke_retries=1).initial_plan(
            GoalSpec(objective="do work")
        )
    assert calls == 2


@pytest.mark.asyncio
async def test_model_goal_planner_accepts_embedded_json_object() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                Here is the plan:

                ```json
                {
                  "name": "goal-embedded-json",
                  "tasks": [
                    {"key": "work", "role": "executor"}
                  ]
                }
                ```
                """
            )
        ]
    )

    plan = await ModelGoalPlanner(backend).initial_plan(
        GoalSpec(objective="do work")
    )

    assert plan.name == "goal-embedded-json"
    assert [task.key for task in plan.tasks] == ["work"]
    assert len(backend.invocations) == 1


@pytest.mark.asyncio
async def test_model_goal_planner_repairs_non_json_response() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content="I would start by researching, then writing."),
            ScriptedTurn(
                content="""
                {
                  "name": "goal-repaired",
                  "tasks": [
                    {"key": "research", "role": "researcher", "tools": ["search.web"]}
                  ]
                }
                """
            ),
        ]
    )

    plan = await ModelGoalPlanner(backend).initial_plan(
        GoalSpec(objective="research", available_tools=("search.web",))
    )

    assert plan.name == "goal-repaired"
    assert plan.tasks[0].tools == ("search.web",)
    assert plan.metadata["planner_diagnostics"]["repair_count"] == 1
    assert plan.metadata["planner_diagnostics"]["phases"] == [
        "initial",
        "json_repair",
    ]
    assert len(backend.invocations) == 2
    repair_message = backend.invocations[1]["messages"][0].content
    assert "planner_json_repair" in repair_message
    assert "I would start by researching" in repair_message


@pytest.mark.asyncio
async def test_model_goal_planner_repairs_json_with_missing_tasks() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"rationale": "too vague"}'),
            ScriptedTurn(
                content="""
                {
                  "name": "goal-semantic-repair",
                  "tasks": [
                    {"key": "work", "role": "executor"}
                  ]
                }
                """
            ),
        ]
    )

    plan = await ModelGoalPlanner(backend).initial_plan(
        GoalSpec(objective="complete task")
    )

    assert plan.name == "goal-semantic-repair"
    assert [task.key for task in plan.tasks] == ["work"]
    assert len(backend.invocations) == 2
    repair_message = backend.invocations[1]["messages"][0].content
    assert "planner response must include a non-empty tasks list" in repair_message


@pytest.mark.asyncio
async def test_model_goal_planner_rejects_undeclared_tools() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "tasks": [
                    {"key": "bad", "role": "executor", "tools": ["shell.exec"]}
                  ]
                }
                """
            )
        ]
    )

    with pytest.raises(ValueError, match="undeclared tool"):
        await ModelGoalPlanner(backend).initial_plan(
            GoalSpec(objective="do work", available_tools=("search.web",))
        )


@pytest.mark.asyncio
async def test_model_goal_planner_reports_repair_failure() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content="not json"),
            ScriptedTurn(content="still not json"),
        ]
    )

    with pytest.raises(ValueError, match="planner JSON repair failed"):
        await ModelGoalPlanner(backend).initial_plan(GoalSpec(objective="do work"))


@pytest.mark.asyncio
async def test_model_goal_planner_builds_plan_delta_from_feedback() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "tasks": [
                    {"key": "draft", "role": "writer"}
                  ]
                }
                """
            ),
            ScriptedTurn(
                content="""
                {
                  "rationale": "add review after failed verifier",
                  "feedback_refs": ["quality failed"],
                  "add_tasks": [
                    {
                      "key": "review",
                      "role": "reviewer",
                      "requires": ["draft"]
                    }
                  ]
                }
                """
            ),
        ]
    )
    goal = GoalSpec(objective="draft and review")
    planner = ModelGoalPlanner(backend)
    plan = await planner.initial_plan(goal)

    delta = await planner.revise_plan(goal, plan, feedback=("quality failed",))
    updated = plan.apply_delta(delta)

    assert isinstance(delta, PlanDelta)
    assert delta.add_tasks[0].key == "review"
    assert delta.add_tasks[0].requires == ("draft",)
    assert delta.feedback_refs == ("quality failed",)
    assert delta.metadata["planner_diagnostics"]["operation"] == "revise_plan"
    assert updated.metadata["last_plan_delta_diagnostics"] == delta.metadata[
        "planner_diagnostics"
    ]
