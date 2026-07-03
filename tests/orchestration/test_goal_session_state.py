"""Goal session plan-state boundary tests."""

from __future__ import annotations

import pytest

from hydramind.control import (
    ControlPlane,
    InMemorySessionStore,
    SessionService,
    TaskContract,
)
from hydramind.orchestration.goal_session_state import GoalSessionState
from hydramind.orchestration.planning import (
    ExecutionPlan,
    GoalSpec,
    PlanDelta,
    PlanTaskSpec,
)


def test_goal_session_metadata_merges_runtime_overrides() -> None:
    state = GoalSessionState(
        ControlPlane(SessionService(InMemorySessionStore()))
    )
    plan = ExecutionPlan(
        name="goal-example",
        goal=GoalSpec(objective="Do work"),
        tasks=(PlanTaskSpec(key="work", role="writer"),),
    )

    metadata = state.session_metadata(
        plan,
        metadata={
            "request_id": "req-1",
            "runtime_overrides": {
                "memory_store_kind": "sqlite",
                "preserve": True,
            },
        },
        runtime_overrides={
            "memory_store_path": "/tmp/memory.sqlite",
            "preserve": False,
        },
    )

    assert metadata["goal"]["objective"] == "Do work"
    assert metadata["execution_plan"]["tasks"][0]["key"] == "work"
    assert metadata["request_id"] == "req-1"
    assert metadata["runtime_overrides"] == {
        "memory_store_kind": "sqlite",
        "memory_store_path": "/tmp/memory.sqlite",
        "preserve": False,
    }


@pytest.mark.asyncio
async def test_goal_session_state_applies_delta_through_control_revision() -> None:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    state = GoalSessionState(control)
    base_plan = ExecutionPlan(
        name="goal-dynamic",
        goal=GoalSpec(objective="Research and write"),
        tasks=(
            PlanTaskSpec(
                key="research",
                role="researcher",
                contract=TaskContract(objective="Find facts."),
            ),
        ),
    )
    session = await service.create_session(
        base_plan.to_blueprint(),
        metadata=state.session_metadata(base_plan),
    )

    updated = await state.apply_plan_delta(
        session.id,
        PlanDelta(
            add_tasks=(
                PlanTaskSpec(
                    key="write",
                    role="writer",
                    requires=("research",),
                ),
            ),
            update_tasks=(
                PlanTaskSpec(
                    key="research",
                    role="researcher",
                    description="Find narrower facts.",
                ),
            ),
            rationale="add writer and revise research",
            feedback_refs=("gate://research/review",),
        ),
    )
    stored = await service.get_session(session.id)

    assert [task.key for task in updated.tasks] == ["research", "write"]
    assert set(stored.nodes) == {"research", "write"}
    assert stored.metadata["execution_plan"]["tasks"][0]["description"] == (
        "Find narrower facts."
    )
    assert stored.metadata["execution_plan"]["tasks"][1]["key"] == "write"
    revision = stored.metadata["workflow_revisions"][-1]
    assert revision["reason"] == "add writer and revise research"
    assert revision["feedback_refs"] == ["gate://research/review"]
    assert revision["added_node_keys"] == ["write"]
    assert revision["changed_node_keys"] == ["research"]
