"""HumanInLoopEvaluator — explicit "halt for review" gate."""

from __future__ import annotations

from hydramind.control.models import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import GateOutcome
from hydramind.gating.base import GateContract


class HumanInLoopEvaluator:
    """Always returns REQUIRES_DECISION when the contract applies.

    Use this when an explicit human (or external system) decision is required
    before the node can advance. Pair with ``ControlPlane.apply_decision`` to
    consume the resulting decision.
    """

    def __init__(self, contract: GateContract) -> None:
        self.name = f"human_in_loop:{contract.name}"
        self.contract = contract

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        return Gate(
            name=self.contract.name,
            node_key=node.key,
            outcome=GateOutcome.REQUIRES_DECISION,
            detail={
                "evaluator": self.name,
                "prompt": self.contract.description or "review required",
            },
        )
