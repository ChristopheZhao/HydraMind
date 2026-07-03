"""Runtime decision contracts for the control plane."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from hydramind.control.models import Gate
from hydramind.control.states import NodeStatus


class RuntimeDecisionKind(StrEnum):
    """The kind of agent-driven outcome being RECORDED (not a decision control made).

    Per ADR-0008, control is a recorder: these values label the outcome the
    orchestrator/agent drove, which control appended to the durable record.
    (The ``RuntimeDecision`` symbol name is retained; a cosmetic rename is
    deferred to S102 — the enforcement is the architecture invariant test.)
    """

    CONTINUE = "continue"  # node advanced, no gate
    AWAIT_GATE = "await_gate"  # paused on gate; caller must apply decision
    COMPLETE = "complete"  # session recorded terminal by the orchestrator
    FAIL = "fail"  # session failed


class RuntimeDecision(BaseModel):
    """Append-only RECORD of one agent-driven outcome (a recorder's record).

    Returned by ``ControlPlane.open_runtime_decision`` /
    ``record_session_complete``. It does NOT represent a business-flow decision
    control made — control records what the orchestrator/agent decided
    (ADR-0008). The name is kept for now; the rename is deferred to S102.
    """

    model_config = ConfigDict(frozen=True)

    kind: RuntimeDecisionKind
    session_id: str
    node_key: str
    node_status: NodeStatus
    gate: Gate | None = None
    error: str | None = None
    execution_id: str | None = None


__all__ = ["RuntimeDecision", "RuntimeDecisionKind"]
