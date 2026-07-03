"""Tests for built-in safety/mechanism evaluators: schema_check, timeout, human_in_loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from hydramind.control.models import (
    AgentReport,
    NodeAttempt,
    NodeState,
    RuntimeSession,
)
from hydramind.control.states import AttemptStatus, GateOutcome, SessionStatus
from hydramind.gating import (
    GateContract,
    HumanInLoopEvaluator,
    SchemaCheckEvaluator,
    TimeoutEvaluator,
)


def _session() -> RuntimeSession:
    return RuntimeSession(workflow_name="t", status=SessionStatus.RUNNING)


class _ExpectedOutput(BaseModel):
    title: str
    score: int


@pytest.mark.asyncio
async def test_schema_check_passes_on_valid_payload() -> None:
    ev = SchemaCheckEvaluator(GateContract(name="check"), _ExpectedOutput)
    gate = await ev.evaluate(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x", output={"title": "ok", "score": 5}),
    )
    assert gate is not None
    assert gate.outcome is GateOutcome.PASS


@pytest.mark.asyncio
async def test_schema_check_requires_decision_on_invalid_payload() -> None:
    ev = SchemaCheckEvaluator(GateContract(name="check"), _ExpectedOutput)
    gate = await ev.evaluate(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x", output={"title": "ok"}),
    )
    assert gate is not None
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert "errors" in gate.detail


@pytest.mark.asyncio
async def test_timeout_no_attempt_returns_none() -> None:
    ev = TimeoutEvaluator(GateContract(name="t", timeout_seconds=1.0))
    gate = await ev.evaluate(
        _session(), NodeState(key="a"), AgentReport(node_key="a", agent_id="x")
    )
    assert gate is None


@pytest.mark.asyncio
async def test_timeout_within_budget_returns_none() -> None:
    ev = TimeoutEvaluator(GateContract(name="t", timeout_seconds=60.0))
    node = NodeState(key="a", attempts=[NodeAttempt(node_key="a")])
    gate = await ev.evaluate(_session(), node, AgentReport(node_key="a", agent_id="x"))
    assert gate is None


@pytest.mark.asyncio
async def test_timeout_exceeded_requires_decision() -> None:
    ev = TimeoutEvaluator(GateContract(name="t", timeout_seconds=0.001))
    attempt = NodeAttempt(node_key="a")
    object.__setattr__(attempt, "started_at", datetime.now(UTC) - timedelta(seconds=5))
    node = NodeState(key="a", attempts=[attempt])
    gate = await ev.evaluate(_session(), node, AgentReport(node_key="a", agent_id="x"))
    assert gate is not None
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert gate.detail["elapsed_seconds"] > 0


@pytest.mark.asyncio
async def test_timeout_finished_attempt_returns_none() -> None:
    ev = TimeoutEvaluator(GateContract(name="t", timeout_seconds=0.001))
    finished = NodeAttempt(node_key="a", status=AttemptStatus.SUCCEEDED)
    node = NodeState(key="a", attempts=[finished])
    gate = await ev.evaluate(_session(), node, AgentReport(node_key="a", agent_id="x"))
    assert gate is None


def test_timeout_requires_timeout_seconds_in_contract() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        TimeoutEvaluator(GateContract(name="t"))


@pytest.mark.asyncio
async def test_human_in_loop_always_requires_decision() -> None:
    ev = HumanInLoopEvaluator(
        GateContract(name="review", description="please review", applies_to_nodes=("publish",))
    )
    gate = await ev.evaluate(
        _session(),
        NodeState(key="publish"),
        AgentReport(node_key="publish", agent_id="x"),
    )
    assert gate is not None
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert gate.detail["prompt"] == "please review"
