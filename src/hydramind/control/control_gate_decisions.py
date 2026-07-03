"""Gate-decision apply helpers for ``ControlPlane``."""

from __future__ import annotations

from typing import Any

from hydramind.control.control_apply import ApplyIntentExecutor
from hydramind.control.control_decisions import RuntimeDecision
from hydramind.control.models import ApplyIntent, Gate, GateDecisionInput
from hydramind.control.session_service import SessionService
from hydramind.control.states import NodeStatus


class GateDecisionExecutor:
    """Apply external gate decisions through the control apply boundary."""

    def __init__(
        self,
        service: SessionService,
        apply_executor: ApplyIntentExecutor,
    ) -> None:
        self._service = service
        self._apply_executor = apply_executor

    async def apply_decision(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> RuntimeDecision:
        gate = await self._service.apply_gate_decision(session_id, decision)
        node = await self._service.get_node(session_id, gate.node_key)
        intent = _intent_from_gate_status(gate, node.status, decision)
        return await self._apply_executor.apply_node_intent(session_id, intent)


def _intent_from_gate_status(
    gate: Gate,
    node_status: NodeStatus,
    decision: GateDecisionInput,
) -> ApplyIntent:
    authorization = _gate_decision_authorization(gate, decision)
    if node_status is NodeStatus.NEEDS_REVISION:
        return ApplyIntent.requeue(
            gate.node_key,
            reason=f"gate {gate.name} requested {decision.action.value}",
            gate=gate,
            authorization=authorization,
        )
    if node_status is NodeStatus.APPROVED:
        return ApplyIntent.complete(
            gate.node_key,
            gate=gate,
            authorization=authorization,
        )
    if node_status is NodeStatus.FAILED:
        return ApplyIntent.fail(
            gate.node_key,
            error=f"gate {gate.name} rejected",
            gate=gate,
            authorization=authorization,
        )
    raise RuntimeError(f"unexpected node status {node_status} after decision {decision.action}")


def _gate_decision_authorization(
    gate: Gate,
    decision: GateDecisionInput,
) -> dict[str, Any]:
    return {
        "source": "gate_decision",
        "gate_id": gate.id,
        "action": decision.action.value,
    }


__all__ = ["GateDecisionExecutor"]
