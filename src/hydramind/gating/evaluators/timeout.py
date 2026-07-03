"""TimeoutEvaluator — flag attempts that have been RUNNING longer than allowed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hydramind.control.models import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import AttemptStatus, GateOutcome
from hydramind.gating.base import GateContract


class TimeoutEvaluator:
    """Return REQUIRES_DECISION when the latest RUNNING attempt has exceeded
    ``contract.timeout_seconds``.

    Returns ``None`` when:
    - ``contract.timeout_seconds`` is None
    - there is no attempt yet
    - the latest attempt has already finished
    - the elapsed time is within the budget
    """

    def __init__(self, contract: GateContract) -> None:
        if contract.timeout_seconds is None:
            raise ValueError(
                f"TimeoutEvaluator requires contract.timeout_seconds (got None) "
                f"for contract {contract.name!r}"
            )
        self.name = f"timeout:{contract.name}"
        self.contract = contract

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        assert self.contract.timeout_seconds is not None  # for type narrowing
        attempt = node.latest_attempt()
        if attempt is None or attempt.status is not AttemptStatus.RUNNING:
            return None
        budget = timedelta(seconds=self.contract.timeout_seconds)
        elapsed = datetime.now(UTC) - attempt.started_at
        if elapsed <= budget:
            return None
        return Gate(
            name=self.contract.name,
            node_key=node.key,
            outcome=GateOutcome.REQUIRES_DECISION,
            detail={
                "evaluator": self.name,
                "attempt_id": attempt.id,
                "elapsed_seconds": elapsed.total_seconds(),
                "budget_seconds": self.contract.timeout_seconds,
            },
        )
