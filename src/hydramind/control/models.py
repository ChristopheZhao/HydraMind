"""Pydantic data models for the control plane.

These are the wire types used by SessionService, ControlPlane, and (after S4)
OrchestratorAgent. They are intentionally vendor-neutral and persistence-neutral
— Pydantic models, not ORM rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hydramind.control.interaction_state import DurableInteraction
from hydramind.control.states import (
    ApplyIntentKind,
    AttemptStatus,
    DecisionAction,
    GateOutcome,
    NodeStatus,
    SessionStatus,
    ToolExecutionStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class WorkflowNodeSpec(BaseModel):
    """One node in a workflow blueprint."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(..., description="Stable node identifier within the workflow.")
    role: str = Field(..., description="Logical role (e.g. 'planner', 'writer').")
    description: str = ""
    requires: tuple[str, ...] = Field(
        default=(),
        description="Other node keys that must complete before this one.",
    )
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowBlueprint(BaseModel):
    """Static description of a workflow's nodes. Supplied by user code.

    The reference project hard-coded this as DEFAULT_NODE_BLUEPRINT. In
    HydraMind it is always external configuration.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str = "1"
    nodes: tuple[WorkflowNodeSpec, ...]

    def node_spec(self, key: str) -> WorkflowNodeSpec:
        for n in self.nodes:
            if n.key == key:
                return n
        raise KeyError(f"node spec {key!r} not in blueprint {self.name!r}")


class SemanticRubricCheck(BaseModel):
    """One named rubric check the semantic verifier should grade."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    min_score: float = 0.6


class SemanticRubric(BaseModel):
    """Opt-in rubric used by the semantic verifier runner."""

    model_config = ConfigDict(frozen=True)

    checks: tuple[SemanticRubricCheck, ...] = ()
    enabled: bool = True


class GoalArtifactQualityContract(BaseModel):
    """Typed quality contract for a goal artifact's final content."""

    model_config = ConfigDict(frozen=True)

    min_length: int = 0
    required_sections: tuple[str, ...] = ()
    min_reference_urls: int = 0
    min_image_refs: int = 0
    min_local_image_refs: int = 0
    local_asset_refs_under_artifact_root: bool = True
    semantic_rubric: SemanticRubric | None = None


class TaskContract(BaseModel):
    """Delivery contract for a goal-derived task.

    The contract describes what a task must satisfy. It is data carried through
    planning/control surfaces; evaluators decide how to verify it.
    """

    model_config = ConfigDict(frozen=True)

    objective: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    negative_cases: tuple[str, ...] = ()
    verifier_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_contract: GoalArtifactQualityContract | None = None


class VerifierResult(BaseModel):
    """Typed verifier feedback attached to an agent report."""

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    score: float | None = None
    evidence_refs: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)
    repair_instruction: str | None = None


class FeedbackRecord(BaseModel):
    """Task-level feedback that can drive revise/replan decisions."""

    model_config = ConfigDict(frozen=True)

    source: str
    message: str
    target_node_key: str | None = None
    severity: str = "info"
    suggested_action: DecisionAction | None = None
    evidence_refs: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)


class ToolExecution(BaseModel):
    """Durable control-owned record for one tool call inside a node execution."""

    id: str = Field(default_factory=lambda: _new_id("tool-exec"))
    node_key: str
    execution_id: str
    trace_id: str | None = None
    tool_call_id: str
    tool_name: str
    round_no: int = 0
    status: ToolExecutionStatus = ToolExecutionStatus.STARTED
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_preview: dict[str, Any] = Field(default_factory=dict)
    is_error: bool | None = None
    content_length: int | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InteractionLogEventKind(StrEnum):
    """Append-only control-owned native MAS interaction log event kinds."""

    INTERACTION_STARTED = "interaction_started"
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    MESSAGE_SENT = "message_sent"
    VOTE_CAST = "vote_cast"
    INTERACTION_COMPLETED = "interaction_completed"
    INTERACTION_FAILED = "interaction_failed"


class InteractionLogRecord(BaseModel):
    """Durable control-owned record for one native MAS interaction event."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: _new_id("interaction-log"))
    session_id: str
    node_key: str
    execution_id: str
    trace_id: str | None = None
    interaction_id: str
    team_id: str
    workspace_id: str | None = None
    event_kind: InteractionLogEventKind
    actor: str | None = None
    turn_index: int | None = Field(default=None, ge=0)
    content_preview: str | None = Field(default=None, max_length=512)
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class NodeAttempt(BaseModel):
    """Control-owned execution record for one node run.

    ``attempt_no`` is retry metadata. New architecture docs should refer to
    this aggregate as a node execution, not as a top-level Attempt layer.
    """

    id: str = Field(default_factory=lambda: _new_id("att"))
    node_key: str
    attempt_no: int = 1
    trace_id: str | None = None
    status: AttemptStatus = AttemptStatus.RUNNING
    started_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    tool_executions: list[ToolExecution] = Field(default_factory=list)
    lease_token: str | None = None
    lease_owner: str | None = None
    last_heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None

    @property
    def execution_id(self) -> str:
        return self.id


NodeExecution = NodeAttempt


class Gate(BaseModel):
    """A recorded gate evaluation result attached to a node."""

    id: str = Field(default_factory=lambda: _new_id("gate"))
    name: str
    node_key: str
    outcome: GateOutcome
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    decision: GateDecision | None = None


class GateDecision(BaseModel):
    """Authoritative decision applied to a gate."""

    id: str = Field(default_factory=lambda: _new_id("dec"))
    gate_id: str
    action: DecisionAction
    actor: str = Field(..., description="who decided: agent id, human id, or 'system'")
    rationale: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class NodeState(BaseModel):
    """Runtime state of one workflow node."""

    key: str
    status: NodeStatus = NodeStatus.QUEUED
    attempts: list[NodeAttempt] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utc_now)

    def latest_attempt(self) -> NodeAttempt | None:
        return self.attempts[-1] if self.attempts else None

    def latest_gate(self) -> Gate | None:
        return self.gates[-1] if self.gates else None


class IdempotencyRecord(BaseModel):
    """Durable record that one idempotency key has been processed (S4c).

    The control-owned dedupe ledger entry. Once a key is claimed, a duplicate
    queue delivery / visibility-timeout re-delivery / crash re-delivery that
    replays the same unit of side effect finds the key already present and skips
    the effect instead of applying it twice. Only ``SessionService`` writes
    these (single-writer rule, AGENTS.md §3).
    """

    model_config = ConfigDict(frozen=True)

    key: str
    kind: str = Field(
        default="",
        description="Coarse class of the deduped effect (tool/memory/repair/interaction).",
    )
    claimed_at: datetime = Field(default_factory=_utc_now)
    detail: dict[str, Any] = Field(default_factory=dict)


class RuntimeSession(BaseModel):
    """Session-level Source of Truth. Mutated only by SessionService."""

    id: str = Field(default_factory=lambda: _new_id("sess"))
    workflow_name: str
    workflow_version: str = "1"
    version: int = 0
    status: SessionStatus = SessionStatus.QUEUED
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    summary_output: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_repair_attempts_used: int = Field(
        default=0,
        ge=0,
        description=(
            "Durable, control-owned count of auto-repair attempts consumed for "
            "this session. Caps runtime repair behavior across worker restarts; "
            "kept separate from planner JSON diagnostics. Only SessionService "
            "mutates this (S4b)."
        ),
    )
    processed_idempotency_keys: dict[str, IdempotencyRecord] = Field(
        default_factory=dict,
        description=(
            "Durable, control-owned dedupe ledger (S4c). Maps an idempotency "
            "key to the record of when it was first processed, so a duplicate "
            "queue delivery does not re-apply tool side effects, repair "
            "reservations, or interaction turns. Only SessionService mutates it."
        ),
    )
    durable_interactions: dict[str, DurableInteraction] = Field(
        default_factory=dict,
        description=(
            "Durable, control-owned authoritative native MAS interaction state "
            "(S5a). Maps interaction id -> DurableInteraction with full-content "
            "authoritative turns/messages + typed protocol outcome and a "
            "version/append sequence. This is the AUTHORITATIVE durable form; "
            "metadata['interaction_log'] (InteractionLogRecord) stays a bounded "
            "preview projection. Only SessionService mutates this."
        ),
    )


# --- agent → control-plane wire types ----------------------------------------


class AgentReport(BaseModel):
    """What an orchestrator/agent reports after a turn.

    The control plane consumes this to decide whether to apply, gate, or fail.
    """

    model_config = ConfigDict(frozen=True)

    node_key: str
    agent_id: str
    harness_id: str | None = Field(
        default=None,
        description="ExecutionHarness identity that produced this report.",
    )
    execution_id: str | None = None
    trace_id: str | None = None
    lease_token: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    boundary_event: str | None = Field(
        default=None,
        description="Named lifecycle marker the agent emits (used by gates in S3).",
    )
    gate_triggers: tuple[str, ...] = Field(
        default=(),
        description="Gate names the agent thinks should fire. Advisory only.",
    )
    verifier_results: tuple[VerifierResult, ...] = ()
    feedback: tuple[FeedbackRecord, ...] = ()
    error: str | None = None

    def persisted_output(self) -> dict[str, Any]:
        """Return output plus typed feedback evidence for node execution storage."""

        persisted = dict(self.output)
        if self.verifier_results:
            persisted["_verifier_results"] = [
                item.model_dump(mode="json") for item in self.verifier_results
            ]
        if self.feedback:
            persisted["_feedback"] = [
                item.model_dump(mode="json") for item in self.feedback
            ]
        return persisted


class GateDecisionInput(BaseModel):
    """Caller-supplied decision applied to a pending gate."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    action: DecisionAction
    actor: str = "system"
    rationale: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowRevision(BaseModel):
    """Control-owned workflow graph revision request.

    Orchestration may propose a new blueprint, but the control layer owns how
    that change is applied to a live RuntimeSession.
    """

    model_config = ConfigDict(frozen=True)

    current_blueprint: WorkflowBlueprint
    revised_blueprint: WorkflowBlueprint
    changed_node_keys: tuple[str, ...] = ()
    reason: str = ""
    feedback_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplyIntent(BaseModel):
    """Typed control-plane transition request.

    ``ControlPlane`` derives these from reports, gate outcomes, gate decisions,
    or explicit control APIs, then applies them through ``SessionService``.
    """

    model_config = ConfigDict(frozen=True)

    kind: ApplyIntentKind
    node_key: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    gate: Gate | None = None
    workflow_revision: WorkflowRevision | None = None
    reason: str = ""
    authorization: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def complete(
        cls,
        node_key: str,
        *,
        output: dict[str, Any] | None = None,
        gate: Gate | None = None,
        authorization: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApplyIntent:
        return cls(
            kind=ApplyIntentKind.COMPLETE,
            node_key=node_key,
            output=output or {},
            gate=gate,
            authorization=authorization or {},
            metadata=metadata or {},
        )

    @classmethod
    def pause(
        cls,
        node_key: str,
        *,
        gate: Gate,
        authorization: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApplyIntent:
        return cls(
            kind=ApplyIntentKind.PAUSE,
            node_key=node_key,
            gate=gate,
            authorization=authorization or {},
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        node_key: str,
        *,
        error: str,
        gate: Gate | None = None,
        authorization: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApplyIntent:
        return cls(
            kind=ApplyIntentKind.FAIL,
            node_key=node_key,
            error=error,
            gate=gate,
            authorization=authorization or {},
            metadata=metadata or {},
        )

    @classmethod
    def requeue(
        cls,
        node_key: str,
        *,
        reason: str = "",
        gate: Gate | None = None,
        authorization: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApplyIntent:
        return cls(
            kind=ApplyIntentKind.REQUEUE,
            node_key=node_key,
            reason=reason,
            gate=gate,
            authorization=authorization or {},
            metadata=metadata or {},
        )

    @classmethod
    def graph_update(
        cls,
        revision: WorkflowRevision,
        *,
        authorization: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApplyIntent:
        return cls(
            kind=ApplyIntentKind.WORKFLOW_REVISION,
            workflow_revision=revision,
            authorization=authorization or {},
            metadata=metadata or {},
        )

    @model_validator(mode="after")
    def _validate_shape(self) -> ApplyIntent:
        if self.kind is ApplyIntentKind.WORKFLOW_REVISION:
            if self.workflow_revision is None:
                raise ValueError("workflow revision intent requires workflow_revision")
            if self.node_key is not None:
                raise ValueError("workflow revision intent must not target a node")
            return self

        if self.node_key is None:
            raise ValueError(f"{self.kind.value} intent requires node_key")
        if self.kind is ApplyIntentKind.PAUSE and self.gate is None:
            raise ValueError("pause intent requires gate")
        if self.kind is ApplyIntentKind.FAIL and not self.error:
            raise ValueError("fail intent requires error")
        if self.workflow_revision is not None:
            raise ValueError(f"{self.kind.value} intent must not carry workflow_revision")
        return self


# Resolve forward references between Gate and GateDecision.
Gate.model_rebuild()
