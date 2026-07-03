"""Goal repair runtime boundary tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from hydramind.control import (
    ControlPlane,
    DecisionAction,
    Gate,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    NodeStatus,
    RuntimeDecision,
    RuntimeDecisionKind,
    SessionService,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.orchestration.goal_repair_runtime import GoalRepairRuntime
from hydramind.orchestration.planning import PlanDelta, PlanTaskSpec


class FakeResumer:
    def __init__(self, decision: RuntimeDecision) -> None:
        self._decision = decision
        self.decisions: list[GateDecisionInput] = []

    async def resume_session(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> RuntimeDecision:
        del session_id
        self.decisions.append(decision)
        return self._decision


@pytest.mark.asyncio
async def test_prepare_replan_decision_uses_gate_feedback_without_choosing_plan() -> None:
    control, session_id, gate = await _session_with_gate(
        {
            "failed_verifiers": [
                {
                    "name": "artifact.exists",
                    "repair_instruction": "write the missing report",
                }
            ],
        }
    )
    feedback_seen: list[tuple[str, ...]] = []

    async def revise_goal_session(
        requested_session_id: str,
        feedback: tuple[str, ...],
    ) -> PlanDelta:
        assert requested_session_id == session_id
        feedback_seen.append(feedback)
        return PlanDelta(
            add_tasks=(PlanTaskSpec(key="write_report", role="writer"),),
            feedback_refs=feedback,
        )

    runtime = GoalRepairRuntime(
        control=control,
        revise_goal_session=revise_goal_session,
        agent_for_session=_unused_agent_for_session,
    )

    prepared = await runtime.prepare_gate_decision(
        session_id,
        GateDecisionInput(
            gate_id=gate.id,
            action=DecisionAction.REPLAN,
            payload={"feedback": "operator requested replan"},
        ),
    )

    assert prepared.action is DecisionAction.REPLAN
    assert feedback_seen == [
        ("operator requested replan", "write the missing report")
    ]


@pytest.mark.asyncio
async def test_auto_repair_approves_required_tool_gate_after_nonempty_delta() -> None:
    control, session_id, gate = await _session_with_gate(
        {
            "failed_verifiers": [
                {
                    "name": "required_tools.completed",
                    "repair_instruction": "call image.generate",
                }
            ],
        }
    )
    feedback_seen: list[tuple[str, ...]] = []
    complete = RuntimeDecision(
        kind=RuntimeDecisionKind.COMPLETE,
        session_id=session_id,
        node_key="",
        node_status=NodeStatus.COMPLETED,
    )
    resumer = FakeResumer(complete)

    async def revise_goal_session(
        requested_session_id: str,
        feedback: tuple[str, ...],
    ) -> PlanDelta:
        assert requested_session_id == session_id
        feedback_seen.append(feedback)
        return PlanDelta(
            add_tasks=(PlanTaskSpec(key="use_image_generate", role="executor"),),
            feedback_refs=feedback,
        )

    runtime = GoalRepairRuntime(
        control=control,
        revise_goal_session=revise_goal_session,
        agent_for_session=_agent_factory(resumer),
        max_repair_attempts=1,
    )

    repaired = await runtime.maybe_auto_repair(
        session_id,
        _awaiting_gate(session_id, gate),
    )

    assert repaired.kind is RuntimeDecisionKind.COMPLETE
    assert feedback_seen == [("call image.generate",)]
    assert len(resumer.decisions) == 1
    assert resumer.decisions[0].action is DecisionAction.APPROVE
    assert resumer.decisions[0].payload == {
        "original_action": "replan",
        "replan_applied_before_approval": True,
    }


@pytest.mark.asyncio
async def test_auto_repair_empty_delta_surfaces_gate_without_resuming() -> None:
    control, session_id, gate = await _session_with_gate(
        {
            "failed_verifiers": [
                {
                    "name": "artifact.exists",
                    "repair_instruction": "write the missing report",
                }
            ],
        }
    )
    feedback_seen: list[tuple[str, ...]] = []

    async def revise_goal_session(
        requested_session_id: str,
        feedback: tuple[str, ...],
    ) -> PlanDelta:
        assert requested_session_id == session_id
        feedback_seen.append(feedback)
        return PlanDelta(feedback_refs=feedback)

    runtime = GoalRepairRuntime(
        control=control,
        revise_goal_session=revise_goal_session,
        agent_for_session=_unused_agent_for_session,
        max_repair_attempts=1,
    )
    awaiting = _awaiting_gate(session_id, gate)

    repaired = await runtime.maybe_auto_repair(session_id, awaiting)

    assert repaired is awaiting
    assert feedback_seen == [("write the missing report",)]


@pytest.mark.asyncio
async def test_reserve_auto_repair_attempt_is_durable_and_control_owned() -> None:
    # One shared durable store; service/control-plane are the only mutators.
    store = InMemorySessionStore()
    service = SessionService(store)
    control = ControlPlane(service)
    session = await service.create_session(
        WorkflowBlueprint(
            name="repair-budget-durable",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        )
    )
    session_id = session.id

    # reserve up to max_attempts, then refuse beyond budget.
    assert await control.reserve_auto_repair_attempt(session_id, max_attempts=2) is True
    assert await control.reserve_auto_repair_attempt(session_id, max_attempts=2) is True
    assert await control.reserve_auto_repair_attempt(session_id, max_attempts=2) is False

    # The durable counter incremented and is persisted on the session.
    refetched = await service.get_session(session_id)
    assert refetched.auto_repair_attempts_used == 2
    assert await control.auto_repair_attempts_used(session_id) == 2

    # A FRESH control plane over the SAME store (worker restart) still sees the
    # budget exhausted — the cap is enforced from durable state, not in-process.
    fresh_service = SessionService(store)
    fresh_control = ControlPlane(fresh_service)
    assert (
        await fresh_control.reserve_auto_repair_attempt(session_id, max_attempts=2)
        is False
    )

    # max_attempts <= 0 never reserves.
    assert await control.reserve_auto_repair_attempt(session_id, max_attempts=0) is False


@pytest.mark.asyncio
async def test_auto_repair_budget_survives_runtime_restart() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    control = ControlPlane(service)
    session = await service.create_session(
        WorkflowBlueprint(
            name="repair-budget-restart",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        )
    )
    session_id = session.id
    gate = await service.record_gate(
        session_id,
        "work",
        name="verifier_feedback",
        outcome=GateOutcome.REQUIRES_DECISION,
        detail={
            "failed_verifiers": [
                {
                    "name": "required_tools.completed",
                    "repair_instruction": "call image.generate",
                }
            ],
        },
    )

    complete = RuntimeDecision(
        kind=RuntimeDecisionKind.COMPLETE,
        session_id=session_id,
        node_key="",
        node_status=NodeStatus.COMPLETED,
    )
    resumer = FakeResumer(complete)
    revise_calls: list[tuple[str, ...]] = []

    async def revise_goal_session(
        requested_session_id: str,
        feedback: tuple[str, ...],
    ) -> PlanDelta:
        del requested_session_id
        revise_calls.append(feedback)
        return PlanDelta(
            add_tasks=(PlanTaskSpec(key="use_image_generate", role="executor"),),
            feedback_refs=feedback,
        )

    # First runtime instance consumes the single allowed attempt.
    runtime = GoalRepairRuntime(
        control=control,
        revise_goal_session=revise_goal_session,
        agent_for_session=_agent_factory(resumer),
        max_repair_attempts=1,
    )
    repaired = await runtime.maybe_auto_repair(
        session_id,
        _awaiting_gate(session_id, gate),
    )
    assert repaired.kind is RuntimeDecisionKind.COMPLETE
    assert len(revise_calls) == 1
    assert await service.auto_repair_attempts_used(session_id) == 1

    # Simulate a worker restart: a FRESH runtime + control over the SAME store.
    # The durable budget is already exhausted, so no further repair is attempted
    # and the awaiting-gate decision is returned unchanged.
    fresh_service = SessionService(store)
    fresh_control = ControlPlane(fresh_service)
    fresh_resumer = FakeResumer(complete)

    async def must_not_revise(
        requested_session_id: str,
        feedback: tuple[str, ...],
    ) -> PlanDelta:
        del requested_session_id, feedback
        raise AssertionError("revise should not run once durable budget is exhausted")

    fresh_runtime = GoalRepairRuntime(
        control=fresh_control,
        revise_goal_session=must_not_revise,
        agent_for_session=_agent_factory(fresh_resumer),
        max_repair_attempts=1,
    )
    awaiting = _awaiting_gate(session_id, gate)
    after_restart = await fresh_runtime.maybe_auto_repair(session_id, awaiting)

    assert after_restart is awaiting
    assert fresh_resumer.decisions == []
    assert await fresh_service.auto_repair_attempts_used(session_id) == 1


async def _session_with_gate(detail: dict) -> tuple[ControlPlane, str, Gate]:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    session = await service.create_session(
        WorkflowBlueprint(
            name="goal-repair-runtime",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        )
    )
    gate = await service.record_gate(
        session.id,
        "work",
        name="verifier_feedback",
        outcome=GateOutcome.REQUIRES_DECISION,
        detail=detail,
    )
    return control, session.id, gate


def _awaiting_gate(session_id: str, gate: Gate) -> RuntimeDecision:
    return RuntimeDecision(
        kind=RuntimeDecisionKind.AWAIT_GATE,
        session_id=session_id,
        node_key=gate.node_key,
        node_status=NodeStatus.PENDING_GATE,
        gate=gate,
    )


def _agent_factory(
    resumer: FakeResumer,
) -> Callable[[str], Awaitable[FakeResumer]]:
    async def agent_for_session(session_id: str) -> FakeResumer:
        del session_id
        return resumer

    return agent_for_session


async def _unused_agent_for_session(session_id: str) -> FakeResumer:
    del session_id
    raise AssertionError("agent_for_session should not be called")
