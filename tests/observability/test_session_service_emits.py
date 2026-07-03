"""Integration: SessionService emits the expected events when wired to an Emitter."""

from __future__ import annotations

import pytest

from hydramind.control import (
    DecisionAction,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    SessionService,
    SessionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.observability import Emitter, ListObserver


def _bp() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="t",
        nodes=(
            WorkflowNodeSpec(key="a", role="x"),
            WorkflowNodeSpec(key="b", role="x", requires=("a",)),
        ),
    )


def _wired() -> tuple[SessionService, ListObserver]:
    obs = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([obs]))
    return service, obs


@pytest.mark.asyncio
async def test_create_session_emits_session_created() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    assert obs.kinds() == ["session_created"]
    assert obs.events[0].session_id == s.id
    assert obs.events[0].detail["workflow_name"] == "t"


@pytest.mark.asyncio
async def test_happy_path_emits_full_lifecycle() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    await service.start_node(s.id, "a")
    await service.start_attempt(s.id, "a")
    await service.complete_node(s.id, "a", output={"x": 1})
    await service.complete_session(s.id, summary={"all": "good"})

    kinds = obs.kinds()
    assert kinds == [
        "session_created",
        "session_running",
        "node_started",
        "attempt_started",
        "node_execution_completed",
        "node_completed",
        "session_completed",
    ]


@pytest.mark.asyncio
async def test_control_events_carry_execution_correlation() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    await service.start_node(s.id, "a")
    execution = await service.start_node_execution(s.id, "a", trace_id="trace-a")
    await service.complete_node(s.id, "a", output={"x": 1})
    await service.complete_session(s.id, summary={"all": "good"})

    correlated = {
        event.kind.value: event
        for event in obs.events
        if event.kind.value
        in {
            "attempt_started",
            "node_execution_started",
            "node_execution_completed",
            "node_completed",
            "session_completed",
        }
    }
    for event in correlated.values():
        assert event.trace_id == "trace-a"
        assert event.execution_id == execution.id


@pytest.mark.asyncio
async def test_execution_lease_events_carry_correlation() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    await service.start_node(s.id, "a")
    execution = await service.start_node_execution(s.id, "a", trace_id="trace-a")

    await service.grant_execution_lease(
        s.id,
        "a",
        execution.id,
        owner="worker-1",
        lease_token="lease-1",
    )
    await service.heartbeat_execution_lease(
        s.id,
        execution.id,
        lease_token="lease-1",
    )
    await service.release_execution_lease(
        s.id,
        execution.id,
        lease_token="lease-1",
    )

    lease_events = [
        event
        for event in obs.events
        if event.kind.value.startswith("execution_lease_")
    ]
    assert [event.kind.value for event in lease_events] == [
        "execution_lease_granted",
        "execution_lease_heartbeat",
        "execution_lease_released",
    ]
    for event in lease_events:
        assert event.trace_id == "trace-a"
        assert event.execution_id == execution.id
        assert event.actor == "worker-1"


@pytest.mark.asyncio
async def test_fail_node_emits_failure_events() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    await service.start_node(s.id, "a")
    await service.start_attempt(s.id, "a")
    await service.fail_node(s.id, "a", error="boom")
    await service.fail_session(s.id, error="boom")
    failures = [e for e in obs.events if e.kind.value in {"node_failed", "session_failed"}]
    assert len(failures) == 2
    assert failures[0].detail["error"] == "boom"


@pytest.mark.asyncio
async def test_gate_and_decision_emit_events() -> None:
    service, obs = _wired()
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    await service.start_node(s.id, "a")
    execution = await service.start_node_execution(s.id, "a", trace_id="trace-gate")
    gate = await service.record_gate(
        s.id, "a", name="check", outcome=GateOutcome.REQUIRES_DECISION
    )
    await service.mark_node_pending_gate(s.id, "a")
    await service.mark_session_waiting_gate(s.id)
    await service.apply_gate_decision(
        s.id,
        GateDecisionInput(
            gate_id=gate.id, action=DecisionAction.APPROVE, actor="user-1"
        ),
    )
    kinds = obs.kinds()
    assert "gate_recorded" in kinds
    assert "session_waiting_gate" in kinds
    assert "decision_applied" in kinds
    decision_event = next(e for e in obs.events if e.kind.value == "decision_applied")
    assert decision_event.actor == "user-1"
    assert decision_event.detail["action"] == "approve"
    assert decision_event.trace_id == "trace-gate"
    assert decision_event.execution_id == execution.id


@pytest.mark.asyncio
async def test_emitter_optional_keeps_service_silent() -> None:
    """When no emitter is provided, S2 backward-compat is preserved."""
    service = SessionService(InMemorySessionStore())  # no emitter
    s = await service.create_session(_bp())
    await service.mark_session_running(s.id)
    s2 = await service.get_session(s.id)
    assert s2.status is SessionStatus.RUNNING  # works without telemetry
