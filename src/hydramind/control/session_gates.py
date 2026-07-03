"""Gate mutation helpers for ``SessionService``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hydramind.control.models import (
    Gate,
    GateDecision,
    GateDecisionInput,
    NodeState,
    RuntimeSession,
)
from hydramind.control.states import DecisionAction, GateOutcome, NodeStatus

NodeTransition = Callable[[NodeState, NodeStatus], None]


class GateNotFoundError(LookupError):
    """Raised when applying a decision to a gate that no longer exists."""


@dataclass(frozen=True)
class GateDecisionResult:
    gate: Gate
    node: NodeState
    target_status: NodeStatus


def record_gate(
    node: NodeState,
    *,
    node_key: str,
    name: str,
    outcome: GateOutcome,
    detail: dict[str, Any] | None,
    now: datetime,
) -> Gate:
    gate = Gate(
        name=name,
        node_key=node_key,
        outcome=outcome,
        detail=detail or {},
    )
    node.gates.append(gate)
    node.updated_at = now
    return gate


def apply_gate_decision(
    session: RuntimeSession,
    decision_input: GateDecisionInput,
    *,
    now: datetime,
    transition_node: NodeTransition,
) -> GateDecisionResult:
    gate, node = find_gate(session, decision_input.gate_id)
    decision = GateDecision(
        gate_id=gate.id,
        action=decision_input.action,
        actor=decision_input.actor,
        rationale=decision_input.rationale,
        payload=dict(decision_input.payload),
    )
    gate.decision = decision
    node.updated_at = now

    target = target_status_for_decision(decision_input.action)
    transition_node(node, target)
    return GateDecisionResult(gate=gate, node=node, target_status=target)


def target_status_for_decision(action: DecisionAction) -> NodeStatus:
    """Map an EXTERNAL ``DecisionAction`` authorization to the recorded node status.

    The ``DecisionAction`` is supplied by an external human/system gate decision
    (HITL/policy authorization, a legitimate mechanism). This is a deterministic
    record of that external decision — control does not synthesize the decision
    itself (ADR-0008); it records the action the external authority chose.
    """
    return {
        DecisionAction.APPROVE: NodeStatus.APPROVED,
        DecisionAction.REVISE: NodeStatus.NEEDS_REVISION,
        DecisionAction.REPLAN: NodeStatus.NEEDS_REVISION,
        DecisionAction.REJECT: NodeStatus.FAILED,
    }[action]


def find_gate(
    session: RuntimeSession,
    gate_id: str,
) -> tuple[Gate, NodeState]:
    for node in session.nodes.values():
        for gate in node.gates:
            if gate.id == gate_id:
                return gate, node
    raise GateNotFoundError(gate_id)
