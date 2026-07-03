"""GateContract.applies + GateRegistry composition tests."""

from __future__ import annotations

import pytest

from hydramind.control.models import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import GateOutcome, SessionStatus
from hydramind.gating import GateContract, GateRegistry, GateSeverity


def _session() -> RuntimeSession:
    return RuntimeSession(
        workflow_name="t",
        nodes={"a": NodeState(key="a"), "b": NodeState(key="b")},
        status=SessionStatus.RUNNING,
    )


def test_contract_applies_with_no_filters() -> None:
    c = GateContract(name="g")
    assert c.applies("anything", None) is True
    assert c.applies("anything", "evt") is True


def test_contract_filters_by_node_key() -> None:
    c = GateContract(name="g", applies_to_nodes=("publish",))
    assert c.applies("publish", None) is True
    assert c.applies("draft", None) is False


def test_contract_filters_by_trigger_event() -> None:
    c = GateContract(name="g", triggers=("done",))
    assert c.applies("x", "done") is True
    assert c.applies("x", "other") is False
    assert c.applies("x", None) is False


class _StaticEv:
    def __init__(self, contract: GateContract, outcome: GateOutcome | None) -> None:
        self.name = f"static:{contract.name}"
        self.contract = contract
        self._outcome = outcome

    async def evaluate(
        self, session: RuntimeSession, node: NodeState, report: AgentReport
    ) -> Gate | None:
        if self._outcome is None:
            return None
        return Gate(name=self.contract.name, node_key=node.key, outcome=self._outcome)


@pytest.mark.asyncio
async def test_registry_halt_wins_over_pass() -> None:
    """A later REQUIRES_DECISION must still halt even if an earlier evaluator passed."""
    registry = GateRegistry()
    registry.register(_StaticEv(GateContract(name="early_pass"), GateOutcome.PASS))
    registry.register(
        _StaticEv(GateContract(name="later_halt"), GateOutcome.REQUIRES_DECISION)
    )
    gate = await registry.to_gate_fn()(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x"),
    )
    assert gate is not None
    assert gate.name == "later_halt"
    assert gate.outcome is GateOutcome.REQUIRES_DECISION


@pytest.mark.asyncio
async def test_registry_returns_first_pass_when_no_halt() -> None:
    registry = GateRegistry()
    registry.register(_StaticEv(GateContract(name="p1"), GateOutcome.PASS))
    registry.register(_StaticEv(GateContract(name="p2"), GateOutcome.PASS))
    gate = await registry.to_gate_fn()(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x"),
    )
    assert gate is not None
    assert gate.name == "p1"


@pytest.mark.asyncio
async def test_registry_skips_inapplicable_evaluator() -> None:
    registry = GateRegistry()
    registry.register(
        _StaticEv(
            GateContract(name="filtered", applies_to_nodes=("other",)),
            GateOutcome.REQUIRES_DECISION,
        )
    )
    registry.register(_StaticEv(GateContract(name="catch"), GateOutcome.PASS))
    gate = await registry.to_gate_fn()(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x"),
    )
    assert gate is not None
    assert gate.name == "catch"


@pytest.mark.asyncio
async def test_advisory_evaluator_block_is_rejected() -> None:
    registry = GateRegistry()
    registry.register(
        _StaticEv(GateContract(name="rogue"), GateOutcome.BLOCK)  # severity defaults ADVISORY
    )
    with pytest.raises(RuntimeError, match="BLOCK"):
        await registry.to_gate_fn()(
            _session(),
            NodeState(key="a"),
            AgentReport(node_key="a", agent_id="x"),
        )


@pytest.mark.asyncio
async def test_blocking_evaluator_block_is_allowed() -> None:
    registry = GateRegistry()
    registry.register(
        _StaticEv(
            GateContract(name="policy", severity=GateSeverity.BLOCKING),
            GateOutcome.BLOCK,
        )
    )
    gate = await registry.to_gate_fn()(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x"),
    )
    assert gate is not None
    assert gate.outcome is GateOutcome.BLOCK


@pytest.mark.asyncio
async def test_registry_returns_none_when_no_evaluator_fires() -> None:
    registry = GateRegistry()
    registry.register(_StaticEv(GateContract(name="skip"), outcome=None))
    gate = await registry.to_gate_fn()(
        _session(),
        NodeState(key="a"),
        AgentReport(node_key="a", agent_id="x"),
    )
    assert gate is None


def test_registry_applicable_filtering() -> None:
    registry = GateRegistry()
    a = _StaticEv(GateContract(name="a", triggers=("done",)), GateOutcome.PASS)
    b = _StaticEv(GateContract(name="b"), GateOutcome.PASS)
    registry.register(a)
    registry.register(b)
    assert len(registry.applicable("any", None)) == 1  # only `b` (a needs trigger)
    assert len(registry.applicable("any", "done")) == 2
