"""Verifier feedback gate evaluator."""

from __future__ import annotations

import pytest

from hydramind.control import (
    AgentReport,
    ControlPlane,
    FeedbackRecord,
    GateOutcome,
    InMemorySessionStore,
    RuntimeDecisionKind,
    SessionService,
    SessionStatus,
    ToolExecutionStatus,
    VerifierResult,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.gating import GateRegistry, VerifierFeedbackEvaluator


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="verify",
        nodes=(WorkflowNodeSpec(key="write", role="writer"),),
    )


@pytest.mark.asyncio
async def test_verifier_feedback_evaluator_skips_reports_without_verifiers() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())
    node = session.nodes["write"]
    evaluator = VerifierFeedbackEvaluator()

    gate = await evaluator.evaluate(
        session,
        node,
        AgentReport(node_key="write", agent_id="writer", output={"ok": True}),
    )

    assert gate is None


@pytest.mark.asyncio
async def test_verifier_feedback_evaluator_passes_when_all_verifiers_pass() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())
    node = session.nodes["write"]
    evaluator = VerifierFeedbackEvaluator()

    gate = await evaluator.evaluate(
        session,
        node,
        AgentReport(
            node_key="write",
            agent_id="writer",
            verifier_results=(VerifierResult(name="artifact", passed=True),),
        ),
    )

    assert gate is not None
    assert gate.outcome is GateOutcome.PASS
    assert gate.detail["failed_verifiers"] == []


@pytest.mark.asyncio
async def test_verifier_feedback_evaluator_halts_with_failed_evidence() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())
    node = session.nodes["write"]
    evaluator = VerifierFeedbackEvaluator()

    gate = await evaluator.evaluate(
        session,
        node,
        AgentReport(
            node_key="write",
            agent_id="writer",
            verifier_results=(
                VerifierResult(
                    name="artifact",
                    passed=False,
                    repair_instruction="write report artifact",
                ),
            ),
            feedback=(
                FeedbackRecord(
                    source="artifact",
                    message="report.md missing",
                    target_node_key="write",
                    severity="error",
                ),
            ),
        ),
    )

    assert gate is not None
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert gate.detail["failed_verifiers"][0]["name"] == "artifact"
    assert gate.detail["feedback"][0]["message"] == "report.md missing"


@pytest.mark.asyncio
async def test_control_plane_pauses_on_failed_verifier_feedback() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())
    registry = GateRegistry([VerifierFeedbackEvaluator()])
    plane = ControlPlane(service, gate_fn=registry.to_gate_fn())

    decision = await plane.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="write",
            agent_id="writer",
            output={"draft": "v1"},
            verifier_results=(
                VerifierResult(name="quality", passed=False, score=0.2),
            ),
        ),
    )
    stored = await service.get_session(session.id)

    assert decision.kind is RuntimeDecisionKind.AWAIT_GATE
    assert stored.status is SessionStatus.WAITING_GATE
    assert stored.nodes["write"].latest_gate().detail["failed_verifiers"][0]["score"] == 0.2


@pytest.mark.asyncio
async def test_required_tool_feedback_halts_when_missing_tool_has_no_pending_node() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(
        WorkflowBlueprint(
            name="required-tools",
            nodes=(WorkflowNodeSpec(key="search", role="researcher"),),
        ),
        metadata={
            "goal": {
                "required_tools": ["search.web", "image.generate"],
            },
            "execution_plan": {
                "tasks": [
                    {"key": "search", "tools": ["search.web"]},
                ],
            },
        },
    )
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "search")
    execution = await service.start_node_execution(session.id, "search")
    await service.record_tool_execution_started(
        session.id,
        execution.id,
        tool_call_id="call-search",
        tool_name="search.web",
        round_no=1,
    )
    await service.record_tool_execution_completed(
        session.id,
        execution.id,
        tool_call_id="call-search",
        is_error=False,
    )
    stored = await service.get_session(session.id)
    evaluator = VerifierFeedbackEvaluator()

    gate = await evaluator.evaluate(
        stored,
        stored.nodes["search"],
        AgentReport(node_key="search", agent_id="researcher", output={"ok": True}),
    )

    assert gate is not None
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert gate.detail["failed_verifiers"][0]["name"] == "required_tools.completed"
    assert gate.detail["required_tool_progress"]["completed_tools"] == ["search.web"]
    assert gate.detail["required_tool_progress"]["missing_tools"] == ["image.generate"]
    assert gate.detail["feedback"][0]["suggested_action"] == "replan"


@pytest.mark.asyncio
async def test_required_tool_feedback_skips_when_pending_node_covers_missing_tool() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(
        WorkflowBlueprint(
            name="required-tools",
            nodes=(
                WorkflowNodeSpec(key="search", role="researcher"),
                WorkflowNodeSpec(key="image", role="executor", requires=("search",)),
            ),
        ),
        metadata={
            "goal": {
                "required_tools": ["search.web", "image.generate"],
            },
            "execution_plan": {
                "tasks": [
                    {"key": "search", "tools": ["search.web"]},
                    {"key": "image", "tools": ["image.generate"]},
                ],
            },
        },
    )
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "search")
    execution = await service.start_node_execution(session.id, "search")
    await service.record_tool_execution_started(
        session.id,
        execution.id,
        tool_call_id="call-search",
        tool_name="search.web",
        round_no=1,
    )
    tool = await service.record_tool_execution_completed(
        session.id,
        execution.id,
        tool_call_id="call-search",
        is_error=False,
    )
    stored = await service.get_session(session.id)
    evaluator = VerifierFeedbackEvaluator()

    gate = await evaluator.evaluate(
        stored,
        stored.nodes["search"],
        AgentReport(node_key="search", agent_id="researcher", output={"ok": True}),
    )

    assert tool.status is ToolExecutionStatus.SUCCEEDED
    assert gate is None
