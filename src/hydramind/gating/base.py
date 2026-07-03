"""GateContract / GateEvaluator / GateRegistry — gating fundamentals."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from hydramind.control.control_plane import GateFn
from hydramind.control.models import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import GateOutcome


class GateSeverity(StrEnum):
    ADVISORY = "advisory"
    BLOCKING = "blocking"


class GateContract(BaseModel):
    """Typed declaration of what a gate guards. Data, not code."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    triggers: tuple[str, ...] = Field(
        default=(),
        description="boundary_event names that activate this gate; empty = always-on.",
    )
    applies_to_nodes: tuple[str, ...] = Field(
        default=(),
        description="node keys this gate applies to; empty = all nodes.",
    )
    severity: GateSeverity = GateSeverity.ADVISORY
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def applies(self, node_key: str, boundary_event: str | None) -> bool:
        node_ok = (not self.applies_to_nodes) or node_key in self.applies_to_nodes
        trigger_ok = (
            (not self.triggers)
            or (boundary_event is not None and boundary_event in self.triggers)
        )
        return node_ok and trigger_ok


@runtime_checkable
class GateEvaluator(Protocol):
    """Runnable evaluator. Returns a Gate when applicable, None to skip."""

    name: str
    contract: GateContract

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None: ...


class GateRegistry:
    """Composes evaluators into a single GateFn the control plane consumes.

    Order of registration matters: the first evaluator that returns a non-None
    Gate wins. Register cheap/general checks first, expensive/specific ones last.
    """

    def __init__(self, evaluators: Iterable[GateEvaluator] | None = None) -> None:
        self._evaluators: list[GateEvaluator] = list(evaluators or ())

    def register(self, evaluator: GateEvaluator) -> None:
        if evaluator.contract.severity is GateSeverity.ADVISORY:
            self._assert_no_block_capability(evaluator)
        self._evaluators.append(evaluator)

    def applicable(
        self, node_key: str, boundary_event: str | None
    ) -> list[GateEvaluator]:
        return [
            e
            for e in self._evaluators
            if e.contract.applies(node_key, boundary_event)
        ]

    def to_gate_fn(self) -> GateFn:
        """Compose evaluators with **halt-wins** semantics.

        BLOCK (only from BLOCKING-severity evaluators) and REQUIRES_DECISION
        outcomes return immediately — the session must halt. PASS outcomes
        are remembered but evaluation continues, so a later REQUIRES_DECISION
        still wins. If only PASS outcomes are produced, the first PASS is
        returned; if no evaluator fires at all, return None.
        """

        async def _gate_fn(
            session: RuntimeSession, node: NodeState, report: AgentReport
        ) -> Gate | None:
            first_pass: Gate | None = None
            for evaluator in self._evaluators:
                if not evaluator.contract.applies(node.key, report.boundary_event):
                    continue
                gate = await evaluator.evaluate(session, node, report)
                if gate is None:
                    continue
                if gate.outcome is GateOutcome.BLOCK:
                    if evaluator.contract.severity is not GateSeverity.BLOCKING:
                        raise RuntimeError(
                            f"evaluator {evaluator.name!r} returned BLOCK but its "
                            f"contract severity is {evaluator.contract.severity}"
                        )
                    return gate
                if gate.outcome is GateOutcome.REQUIRES_DECISION:
                    return gate
                # PASS: remember but keep checking — a later evaluator may halt.
                if first_pass is None:
                    first_pass = gate
            return first_pass

        return _gate_fn

    @staticmethod
    def _assert_no_block_capability(evaluator: GateEvaluator) -> None:
        """Best-effort safety: an ADVISORY evaluator must not be a known blocker."""
        # We cannot inspect future evaluate() returns; runtime check in to_gate_fn
        # is authoritative. This stub is reserved for future static metadata.
        return
