"""ControlPlane integration tests — gate/apply loop end-to-end."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import hydramind.control as control_api
import hydramind.control.control_apply as control_apply_module
import hydramind.control.control_decisions as control_decisions_module
import hydramind.control.control_gate_decisions as control_gate_decisions_module
import hydramind.control.control_plane as control_plane_module
import hydramind.control.session_service as session_service_module
from hydramind.control import (
    AgentReport,
    ApplyIntent,
    ApplyIntentKind,
    AttemptStatus,
    ControlPlane,
    DecisionAction,
    ExecutionLeaseError,
    FeedbackRecord,
    Gate,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    NodeState,
    NodeStatus,
    RuntimeDecisionKind,
    RuntimeSession,
    SessionService,
    SessionStatus,
    ToolExecutionStatus,
    VerifierResult,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    WorkflowRevision,
)


def test_control_apply_boundary_is_internal() -> None:
    assert hasattr(control_apply_module, "ApplyIntentExecutor")
    assert hasattr(control_gate_decisions_module, "GateDecisionExecutor")
    assert hasattr(control_apply_module, "coerce_apply_intent")
    assert not hasattr(ControlPlane, "_coerce_apply_intent")
    assert "control_apply" not in control_api.__all__
    assert "control_decisions" not in control_api.__all__
    assert "control_gate_decisions" not in control_api.__all__
    assert control_plane_module.RuntimeDecision is control_decisions_module.RuntimeDecision
    assert control_plane_module.RuntimeDecisionKind is control_decisions_module.RuntimeDecisionKind


def _single_node_blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="lonely",
        nodes=(WorkflowNodeSpec(key="only", role="solo"),),
    )


def _two_node_blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="duo",
        nodes=(
            WorkflowNodeSpec(key="a", role="planner"),
            WorkflowNodeSpec(key="b", role="writer", requires=("a",)),
        ),
    )


@pytest.fixture
async def setup() -> tuple[SessionService, RuntimeSession]:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_single_node_blueprint())
    return service, session


@pytest.mark.asyncio
async def test_node_apply_records_completion_without_completing_session(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    """Control RECORDS node completion; it does NOT auto-complete the session.

    Completing the LAST node via control alone leaves the session non-terminal
    (RUNNING). Only the explicit orchestrator-driven record transitions it to
    COMPLETED (ADR-0008).
    """
    service, session = setup
    plane = ControlPlane(service)
    decision = await plane.open_runtime_decision(
        session.id,
        AgentReport(node_key="only", agent_id="agent-1", output={"result": "ok"}),
    )
    # Control records node completion only — no session-terminal decision.
    assert decision.kind is RuntimeDecisionKind.CONTINUE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.RUNNING
    assert s.nodes["only"].status is NodeStatus.COMPLETED

    # The orchestrator-driven record is the only path to COMPLETED.
    recorded = await plane.record_session_complete(session.id, summary={"result": "ok"})
    assert recorded.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED
    assert s.summary_output == {"result": "ok"}


@pytest.mark.asyncio
async def test_active_execution_lease_requires_matching_report_token(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup
    plane = ControlPlane(service)
    execution = await plane.open_node_execution(
        session.id,
        "only",
        trace_id="trace-lease",
    )
    leased = await plane.grant_node_execution_lease(
        session.id,
        "only",
        execution.id,
        owner="worker-1",
        ttl_seconds=60,
    )

    with pytest.raises(ExecutionLeaseError, match="requires a lease token"):
        await plane.open_runtime_decision(
            session.id,
            AgentReport(
                node_key="only",
                agent_id="agent-1",
                execution_id=execution.id,
            ),
        )

    with pytest.raises(ExecutionLeaseError, match="mismatch"):
        await plane.open_runtime_decision(
            session.id,
            AgentReport(
                node_key="only",
                agent_id="agent-1",
                execution_id=execution.id,
                lease_token="wrong",
            ),
        )

    decision = await plane.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="only",
            agent_id="agent-1",
            execution_id=execution.id,
            lease_token=leased.lease_token,
            output={"result": "ok"},
        ),
    )

    assert decision.kind is RuntimeDecisionKind.CONTINUE
    s = await service.get_session(session.id)
    latest = s.nodes["only"].latest_attempt()
    assert latest is not None
    assert latest.lease_token is None
    assert latest.output["result"] == "ok"


@pytest.mark.asyncio
async def test_control_plane_records_tool_execution_ledger(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup
    plane = ControlPlane(service)
    execution = await plane.open_node_execution(
        session.id,
        "only",
        trace_id="trace-tool",
    )

    await plane.record_tool_execution_started(
        session.id,
        execution.id,
        tool_call_id="call-1",
        tool_name="image.generate",
        round_no=1,
        arguments={"prompt": "blue square"},
        trace_id="trace-tool",
    )
    completed = await plane.record_tool_execution_completed(
        session.id,
        execution.id,
        tool_call_id="call-1",
        is_error=True,
        result_preview={"content_preview": "provider rate limit"},
        content_length=42,
        error="provider rate limit",
    )

    stored = await service.get_session(session.id)
    ledger = stored.nodes["only"].latest_attempt().tool_executions
    assert completed.status is ToolExecutionStatus.FAILED
    assert ledger[0].status is ToolExecutionStatus.FAILED
    assert ledger[0].is_error is True
    assert ledger[0].error == "provider rate limit"


@pytest.mark.asyncio
async def test_expired_execution_lease_rejects_late_report(
    setup: tuple[SessionService, RuntimeSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session = setup
    plane = ControlPlane(service)
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service_module, "_now", lambda: opened_at)
    execution = await plane.open_node_execution(
        session.id,
        "only",
        trace_id="trace-expired",
    )
    leased = await plane.grant_node_execution_lease(
        session.id,
        "only",
        execution.id,
        owner="worker-1",
        ttl_seconds=1,
    )

    monkeypatch.setattr(
        session_service_module,
        "_now",
        lambda: opened_at + timedelta(seconds=2),
    )
    with pytest.raises(ExecutionLeaseError, match="lease expired"):
        await plane.open_runtime_decision(
            session.id,
            AgentReport(
                node_key="only",
                agent_id="agent-1",
                execution_id=execution.id,
                lease_token=leased.lease_token,
                output={"result": "late"},
            ),
        )

    s = await service.get_session(session.id)
    latest = s.nodes["only"].latest_attempt()
    assert latest is not None
    assert latest.status is AttemptStatus.RUNNING
    assert s.nodes["only"].status is NodeStatus.RUNNING


@pytest.mark.asyncio
async def test_gate_requires_decision_awaits(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    async def gate_fn(s: RuntimeSession, n: NodeState, r: AgentReport) -> Gate | None:
        return Gate(
            name="manual_review",
            node_key=n.key,
            outcome=GateOutcome.REQUIRES_DECISION,
        )

    plane = ControlPlane(service, gate_fn=gate_fn)
    decision = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent-1")
    )
    assert decision.kind is RuntimeDecisionKind.AWAIT_GATE
    assert decision.gate is not None
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.WAITING_GATE
    assert s.nodes["only"].status is NodeStatus.PENDING_GATE
    recorded = s.nodes["only"].latest_gate()
    assert recorded is not None
    assert recorded.detail["_apply_intent"]["kind"] == ApplyIntentKind.PAUSE.value
    assert recorded.detail["_apply_intent"]["authorization"]["source"] == "gate_result"


@pytest.mark.asyncio
async def test_gate_pass_advances_node(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    async def gate_fn(s: RuntimeSession, n: NodeState, r: AgentReport) -> Gate | None:
        return Gate(name="ok", node_key=n.key, outcome=GateOutcome.PASS)

    plane = ControlPlane(service, gate_fn=gate_fn)
    decision = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent-1", output={"x": 1})
    )
    # Gate PASS records node completion; session completion is the orchestrator's.
    assert decision.kind is RuntimeDecisionKind.CONTINUE
    assert decision.node_status is NodeStatus.COMPLETED


@pytest.mark.asyncio
async def test_gate_block_fails_session(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    async def gate_fn(s: RuntimeSession, n: NodeState, r: AgentReport) -> Gate | None:
        return Gate(name="policy", node_key=n.key, outcome=GateOutcome.BLOCK)

    plane = ControlPlane(service, gate_fn=gate_fn)
    decision = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent-1")
    )
    assert decision.kind is RuntimeDecisionKind.FAIL
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.FAILED


@pytest.mark.asyncio
async def test_apply_decision_revise_returns_node_to_queued(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    async def gate_fn(s: RuntimeSession, n: NodeState, r: AgentReport) -> Gate | None:
        if not n.gates:
            return Gate(name="rev", node_key=n.key, outcome=GateOutcome.REQUIRES_DECISION)
        return None

    plane = ControlPlane(service, gate_fn=gate_fn)
    first = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent-1")
    )
    assert first.kind is RuntimeDecisionKind.AWAIT_GATE
    assert first.gate is not None

    second = await plane.apply_decision(
        session.id,
        GateDecisionInput(gate_id=first.gate.id, action=DecisionAction.REVISE),
    )
    assert second.kind is RuntimeDecisionKind.CONTINUE
    s = await service.get_session(session.id)
    assert s.nodes["only"].status is NodeStatus.QUEUED
    assert s.status is SessionStatus.RUNNING
    latest = s.nodes["only"].latest_attempt()
    assert latest is not None
    assert latest.status is AttemptStatus.ABORTED
    assert latest.error == "gate rev requested revise"


@pytest.mark.asyncio
async def test_apply_decision_approve_records_node_but_not_session_completion(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    """Approving the final gate via apply_decision does NOT auto-complete the
    session — control records the node-level completion only; the orchestrator
    records session completion (ADR-0008)."""
    service, session = setup

    async def gate_fn(s: RuntimeSession, n: NodeState, r: AgentReport) -> Gate | None:
        return Gate(name="approve_me", node_key=n.key, outcome=GateOutcome.REQUIRES_DECISION)

    plane = ControlPlane(service, gate_fn=gate_fn)
    first = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent-1")
    )
    assert first.gate is not None

    final = await plane.apply_decision(
        session.id,
        GateDecisionInput(gate_id=first.gate.id, action=DecisionAction.APPROVE),
    )
    assert final.kind is RuntimeDecisionKind.CONTINUE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.RUNNING
    assert s.nodes["only"].status is NodeStatus.COMPLETED

    recorded = await plane.record_session_complete(session.id)
    assert recorded.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_agent_error_fails_session(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup
    plane = ControlPlane(service)
    decision = await plane.open_runtime_decision(
        session.id,
        AgentReport(node_key="only", agent_id="agent-1", error="LLM refused"),
    )
    assert decision.kind is RuntimeDecisionKind.FAIL
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.FAILED
    assert s.error_message == "LLM refused"


@pytest.mark.asyncio
async def test_multi_node_session_waits_for_all_nodes() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_two_node_blueprint())
    plane = ControlPlane(service)

    first = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="a", agent_id="planner")
    )
    assert first.kind is RuntimeDecisionKind.CONTINUE  # 'b' still queued
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.RUNNING
    assert s.nodes["a"].status is NodeStatus.COMPLETED
    assert s.nodes["b"].status is NodeStatus.QUEUED

    second = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="b", agent_id="writer")
    )
    # Last node completion is recorded; the session stays RUNNING until the
    # orchestrator records completion.
    assert second.kind is RuntimeDecisionKind.CONTINUE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.RUNNING
    assert s.nodes["b"].status is NodeStatus.COMPLETED

    recorded = await plane.record_session_complete(session.id)
    assert recorded.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_custom_apply_deriver_used(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    calls: list[str] = []

    async def custom_deriver(s: RuntimeSession, n: NodeState, r: AgentReport) -> ApplyIntent:
        calls.append(n.key)
        return ApplyIntent.complete(
            n.key,
            output={"by": "custom"},
            authorization={"source": "test_custom_deriver"},
        )

    plane = ControlPlane(service, apply_deriver=custom_deriver)
    await plane.open_runtime_decision(session.id, AgentReport(node_key="only", agent_id="agent"))
    assert calls == ["only"]
    s = await service.get_session(session.id)
    # The deriver's output is recorded on the node; control does not complete
    # the session (no summary_output is synthesized by control).
    assert s.summary_output == {}
    assert s.nodes["only"].status is NodeStatus.COMPLETED
    assert s.nodes["only"].latest_attempt().output["by"] == "custom"


@pytest.mark.asyncio
async def test_custom_apply_deriver_can_requeue_without_running_attempt_leak(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup

    async def retry_deriver(s: RuntimeSession, n: NodeState, r: AgentReport) -> ApplyIntent:
        return ApplyIntent.requeue(
            n.key,
            reason="retry with fresher context",
            authorization={"source": "test_retry_deriver"},
        )

    plane = ControlPlane(service, apply_deriver=retry_deriver)
    decision = await plane.open_runtime_decision(
        session.id, AgentReport(node_key="only", agent_id="agent")
    )

    assert decision.kind is RuntimeDecisionKind.CONTINUE
    assert decision.node_status is NodeStatus.QUEUED
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.RUNNING
    assert s.nodes["only"].status is NodeStatus.QUEUED
    latest = s.nodes["only"].latest_attempt()
    assert latest is not None
    assert latest.status is AttemptStatus.ABORTED
    assert latest.error == "retry with fresher context"


def test_apply_intent_validation_is_closed_over_transition_kinds() -> None:
    assert {item.value for item in ApplyIntentKind} == {
        "complete",
        "pause",
        "fail",
        "requeue",
        "workflow_revision",
    }
    with pytest.raises(ValidationError, match="pause intent requires gate"):
        ApplyIntent(kind=ApplyIntentKind.PAUSE, node_key="only")


@pytest.mark.asyncio
async def test_agent_report_persists_verifier_feedback(
    setup: tuple[SessionService, RuntimeSession],
) -> None:
    service, session = setup
    plane = ControlPlane(service)

    await plane.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="only",
            agent_id="agent",
            output={"answer": "ok"},
            verifier_results=(
                VerifierResult(
                    name="artifact-check",
                    passed=True,
                    evidence_refs=("artifacts/report.md",),
                ),
            ),
            feedback=(
                FeedbackRecord(
                    source="verifier",
                    message="delivery artifact is present",
                    target_node_key="only",
                ),
            ),
        ),
    )

    s = await service.get_session(session.id)
    output = s.nodes["only"].latest_attempt().output
    assert output["answer"] == "ok"
    assert output["_verifier_results"][0]["name"] == "artifact-check"
    assert output["_verifier_results"][0]["passed"] is True
    assert output["_feedback"][0]["message"] == "delivery artifact is present"


@pytest.mark.asyncio
async def test_control_plane_applies_workflow_revision() -> None:
    service = SessionService(InMemorySessionStore())
    current = _two_node_blueprint()
    session = await service.create_session(current)
    plane = ControlPlane(service)

    await plane.open_runtime_decision(
        session.id, AgentReport(node_key="a", agent_id="planner", output={"v": 1})
    )
    revised = WorkflowBlueprint(
        name="duo",
        nodes=(
            WorkflowNodeSpec(key="a", role="planner", description="v2"),
            WorkflowNodeSpec(key="b", role="writer", requires=("a",)),
            WorkflowNodeSpec(key="c", role="reviewer", requires=("b",)),
        ),
    )

    updated = await plane.apply_workflow_revision(
        session.id,
        WorkflowRevision(
            current_blueprint=current,
            revised_blueprint=revised,
            changed_node_keys=("a",),
            reason="planner output invalidated",
            metadata={"execution_plan": {"version": "2"}},
        ),
    )

    assert set(updated.nodes) == {"a", "b", "c"}
    assert updated.nodes["a"].status is NodeStatus.QUEUED
    assert updated.nodes["b"].status is NodeStatus.QUEUED
    assert updated.nodes["c"].status is NodeStatus.QUEUED
    assert updated.nodes["a"].latest_attempt().output == {"v": 1}
    assert updated.metadata["workflow_revisions"][-1]["added_node_keys"] == ["c"]
