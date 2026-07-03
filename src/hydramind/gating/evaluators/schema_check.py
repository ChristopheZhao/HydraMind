"""SchemaCheckEvaluator — validate AgentReport.output against a Pydantic model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from hydramind.control.models import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import GateOutcome
from hydramind.gating.base import GateContract

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class SchemaCheckEvaluator:
    """Validate an agent's report.output against a Pydantic model.

    PASS when validation succeeds.
    REQUIRES_DECISION when validation fails (so the caller decides whether to
    revise the node or accept anyway).
    """

    def __init__(self, contract: GateContract, schema: type[BaseModel]) -> None:
        self.name = f"schema_check:{contract.name}"
        self.contract = contract
        self._schema = schema

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        try:
            self._schema.model_validate(report.output)
        except ValidationError as e:
            return Gate(
                name=self.contract.name,
                node_key=node.key,
                outcome=GateOutcome.REQUIRES_DECISION,
                detail={
                    "evaluator": self.name,
                    "schema": self._schema.__name__,
                    "errors": e.errors(),
                },
            )
        return Gate(
            name=self.contract.name,
            node_key=node.key,
            outcome=GateOutcome.PASS,
            detail={"evaluator": self.name, "schema": self._schema.__name__},
        )
