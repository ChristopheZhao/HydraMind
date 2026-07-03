"""Typed ``ExecutionHarness`` contract around an execution episode (S2b; N1).

See ``docs/architecture/95-execution-harness-correction.md`` Phase 2 and
``docs/plans/active/PLAN-20260619-001.md`` §0.4 + §8 (N1 contract).

This module names the *episode-level* execution seam. A single node execution
is one episode: prompt/context construction, mode dispatch (direct / subagent /
team), the (multi-turn) tool loop, report building, and verifier integration.
The default :class:`HydraMindExecutionHarness` composes the *existing*
orchestration pieces (via
:class:`~hydramind.orchestration.agent_invocation.AgentNodeInvoker`) behind this
typed contract; it does not reimplement them.

Harness scope (PLAN-20260619-001 §0.4, research-faithful): the ``ExecutionHarness``
is the BROAD single-agent execution envelope. Its typed I/O therefore EXPRESSES
the full execution-policy surface it owns — the multi-turn loop, context/prompt
policy, memory-retrieval policy, permission/budget/timeout constraints, in-loop
verifier integration, trace/evidence EMISSION, subagent strategy, and
recovery/retry STRATEGY. The carve-out is by OWNERSHIP TYPE, not by topic
(§0.3 litmus): the harness PROPOSES outcomes and EMITS evidence; Control owns the
DURABLE state transitions and GateResult AUTHORIZATION. ``run_episode`` therefore
never mutates ``RuntimeSession`` — it returns an :class:`ExecutionEpisodeOutcome`
whose typed outputs (final result, model invocations, tool/verifier evidence,
trace refs, failure classification, proposed transitions, recovery signals)
Control later consumes to decide apply/gate/fail.

N1 is ADDITIVE + behavior-preserving: the enriched request/policy/outcome fields
are all optional/defaulted; the default harness keeps composing
``AgentNodeInvoker`` and populates the new outputs from already-available report
data. Swapping the harness changes neither who WRITES durable state nor who
AUTHORIZES transitions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from hydramind.control.models import (
    AgentReport,
    RuntimeSession,
    VerifierResult,
)
from hydramind.control.states import ApplyIntentKind
from hydramind.harness.base import (
    InvocationResult,
    Message,
    ToolSpec,
)
from hydramind.harness.provider import ModelProvider

if TYPE_CHECKING:
    from hydramind.orchestration.agent_invocation import AgentNodeInvoker


class FailureCategory(StrEnum):
    """Typed, best-effort failure classification for an execution episode."""

    NONE = "none"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    UNRESOLVED_TOOL_CALLS = "unresolved_tool_calls"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class CompactionResult(BaseModel):
    """Outcome of a context-compaction request owned by the execution harness."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    summary: str
    tokens_before: int = 0
    tokens_after: int = 0


class SubagentContext(BaseModel):
    """Optional context bundle the parent passes into a fresh subagent."""

    model_config = ConfigDict(frozen=True)

    seed_messages: tuple[Message, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentHandle(ABC):
    """Opaque handle to a harness-owned subagent/team member.

    The subagent owns its own context window. Parent-to-child isolation is
    relaxed within an interaction: when seed messages are provided, every
    conformant execution runtime threads them into the child's context before it
    acts. The relaxation is scoped to the interaction and does not move provider
    or vendor SDK concerns above the harness boundary.
    """

    id: str
    role: str

    @abstractmethod
    async def send(self, message: Message) -> InvocationResult: ...

    @abstractmethod
    async def close(self, *, return_summary: bool = True) -> str: ...


# --- typed execution-policy surface (broad harness inputs, §0.4) --------------


class ExecutionConstraints(BaseModel):
    """Typed permission / network / process + budget/timeout limits the harness owns.

    DECLARED forward-surface, NOT yet ENFORCED. A blank policy (all defaults) means
    "no harness-level constraint declared", preserving current behavior. The default
    :class:`HydraMindExecutionHarness` does NOT enforce these — actual sandbox /
    network / process / artifact-root containment lives in the tool-sandbox layer
    (``runtime_tools`` + the tool runner). Setting a non-default here is therefore a
    DECLARATION, not enforcement; do NOT read ``network_allowed`` /
    ``process_allowed`` / ``allowed_tool_names`` as a live security boundary. Only
    ``max_turns`` is consumed today (``explicit_submit_execution_harness`` bounds the loop);
    ``None`` budgets/timeouts mean unbounded.
    """

    model_config = ConfigDict(frozen=True)

    network_allowed: bool = True
    process_allowed: bool = True
    allowed_tool_names: tuple[str, ...] = ()
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)


class RecoveryPolicy(BaseModel):
    """Typed in-episode recovery/retry STRATEGY (not durable repair-budget state).

    The harness owns the retry STRATEGY; the durable repair-attempt counter and
    its single-writer remain control-plane state (§0.3).
    """

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=0, ge=0)
    retry_on: tuple[FailureCategory, ...] = ()


class SubagentPolicy(BaseModel):
    """Typed subagent/team config policy the harness owns (ADR-0012 reconciliation).

    The harness owns sub-agent *configuration/policy* (enabled, fan-out cap) and
    emits the delegation request; the spawn ACT is orchestration-owned
    (``SubagentSpawner``). ``None`` ``max_subagents`` means no harness-declared cap.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_subagents: int | None = Field(default=None, ge=1)


class ExecutionHarnessFeature(StrEnum):
    """Portable execution-harness features owned above the provider seam."""

    MULTI_TURN = "multi_turn"
    SUBAGENTS = "subagents"
    TEAM_INTERACTION = "team_interaction"
    COMPACTION = "compaction"
    TOOL_LOOP_STRATEGY = "tool_loop_strategy"
    OBSERVABILITY_EMISSION = "observability_emission"
    VERIFIER_INTEGRATION = "verifier_integration"
    RECOVERY_STRATEGY = "recovery_strategy"
    BUDGET_POLICY = "budget_policy"


_HARNESS_CAPABILITY_FIELD_BY_FEATURE: dict[ExecutionHarnessFeature, str] = {
    ExecutionHarnessFeature.MULTI_TURN: "supports_multi_turn",
    ExecutionHarnessFeature.SUBAGENTS: "supports_subagents",
    ExecutionHarnessFeature.TEAM_INTERACTION: "supports_team_interaction",
    ExecutionHarnessFeature.COMPACTION: "supports_compaction",
    ExecutionHarnessFeature.TOOL_LOOP_STRATEGY: "supports_tool_loop_strategy",
    ExecutionHarnessFeature.OBSERVABILITY_EMISSION: "supports_observability_emission",
    ExecutionHarnessFeature.VERIFIER_INTEGRATION: "supports_verifier_integration",
    ExecutionHarnessFeature.RECOVERY_STRATEGY: "supports_recovery_strategy",
    ExecutionHarnessFeature.BUDGET_POLICY: "supports_budget_policy",
}


class ExecutionHarnessCapabilityError(RuntimeError):
    """Raised when the execution harness cannot satisfy a required feature."""


class ExecutionHarnessCapabilities(BaseModel):
    """Harness-owned support declaration for broad execution policy.

    This is the capability surface orchestration consumes above the provider
    seam; callers should require features through this type.
    """

    model_config = ConfigDict(frozen=True)

    supports_multi_turn: bool = True
    supports_subagents: bool = True
    supports_team_interaction: bool = True
    supports_compaction: bool = False
    supports_tool_loop_strategy: bool = True
    supports_observability_emission: bool = True
    supports_verifier_integration: bool = True
    supports_recovery_strategy: bool = True
    supports_budget_policy: bool = True
    max_context_tokens: int = Field(default=0, ge=0)
    notes: str = ""

    def supports(self, *features: ExecutionHarnessFeature) -> bool:
        """Return whether all requested execution-harness features are present."""

        return all(
            getattr(self, _HARNESS_CAPABILITY_FIELD_BY_FEATURE[feature])
            for feature in features
        )

    def require(
        self,
        *features: ExecutionHarnessFeature,
        harness_name: str = "execution harness",
        operation: str | None = None,
    ) -> None:
        """Raise if any requested execution-harness feature is unavailable."""

        missing = [
            feature
            for feature in features
            if not getattr(self, _HARNESS_CAPABILITY_FIELD_BY_FEATURE[feature])
        ]
        if not missing:
            return
        missing_names = ", ".join(feature.value for feature in missing)
        suffix = f" required for {operation}" if operation else ""
        raise ExecutionHarnessCapabilityError(
            f"{harness_name} does not support execution harness feature: "
            f"{missing_names}{suffix}"
        )


class SubagentSpawnRequest(BaseModel):
    """Typed request for harness-owned subagent/team-member creation."""

    model_config = ConfigDict(frozen=True)

    role: str
    instructions: str
    tools: tuple[ToolSpec, ...] = ()
    parent_context: SubagentContext | None = None


class ContextCompactionRequest(BaseModel):
    """Typed request for harness-owned context compaction policy."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    keep_last_n: int = Field(default=20, ge=0)


@runtime_checkable
class ExecutionHarnessRuntime(Protocol):
    """Operational harness surface for capability declaration + compaction policy.

    It is intentionally distinct from the model/provider seam. Direct model turns
    go through ``ModelProvider.complete``. The harness declares its sub-agent
    *capability policy* here (``capabilities``) and homes in-episode context
    compaction (``compact_context``) — but it does NOT spawn: per ADR-0012 the
    spawn ACT is orchestration-owned (``SubagentSpawner``), fed the harness's typed
    delegation request (``SubagentSpawnRequest``) + this capability policy.
    """

    capabilities: ExecutionHarnessCapabilities

    async def compact_context(
        self,
        request: ContextCompactionRequest,
    ) -> CompactionResult: ...


class ProviderExecutionHarnessRuntime:
    """Harness-owned runtime surface backed by a fixed model provider.

    Providers stay pure model-call dependencies. This runtime owns the sub-agent
    *capability declaration* and compaction policy while delegating individual
    model turns to ``ModelProvider.complete``. It does NOT spawn sub-agents: the
    spawn ACT is orchestration-owned (``hydramind.orchestration.subagent_spawn``),
    which consults this ``capabilities`` policy (ADR-0012 narrow harness).
    """

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self.capabilities = ExecutionHarnessCapabilities(
            supports_multi_turn=True,
            supports_subagents=True,
            supports_team_interaction=True,
            supports_compaction=False,
            supports_tool_loop_strategy=True,
            supports_observability_emission=True,
            supports_verifier_integration=True,
            supports_recovery_strategy=True,
            supports_budget_policy=True,
            max_context_tokens=provider.context_limit(),
            notes=(
                "Provider-backed execution runtime. The sub-agent capability "
                "policy is declared here; the spawn act is orchestration-owned; "
                "model calls use ModelProvider.complete."
            ),
        )

    @property
    def provider(self) -> ModelProvider:
        """The wrapped model provider used to back orchestration-spawned sub-agents.

        Read-only accessor so the orchestration ``SubagentSpawner`` can be built
        straight from this runtime (provider + capability policy) without
        re-instantiating it. The provider is not promoted above the harness
        boundary — orchestration already holds it directly.
        """

        return self._provider

    async def compact_context(
        self,
        request: ContextCompactionRequest,
    ) -> CompactionResult:
        """Context compaction is HOMED here (harness layer) but NOT implemented.

        Architectural placement is the point: compaction is in-episode context
        management, which is a harness responsibility — so the capability and its
        typed surface live on this ``ExecutionHarnessRuntime``, never on the
        provider (model) layer. The implementation is deliberately deferred: no
        runtime path requires it today (the multi-turn loop is bounded by
        ``max_tool_rounds``), so ``supports_compaction=False`` gates it OFF and
        ``require`` below fails loud rather than silently no-op'ing. The trailing
        ``NotImplementedError`` is the guard for a future state where the flag is
        flipped on before a real compaction policy exists.
        """

        self.capabilities.require(
            ExecutionHarnessFeature.COMPACTION,
            harness_name=self._provider.name,
            operation="compact_context",
        )
        raise NotImplementedError("provider-backed explicit compaction is not implemented")


class ExecutionHarnessPolicy(BaseModel):
    """Typed ownership descriptor for the single-agent harness's execution policy.

    Expresses the harness's execution-policy ownership as typed I/O (ADR-0010 §F)
    via the SELF-CONTAINED knobs it actually owns: the multi-turn loop, the
    permission/budget/timeout constraints, the in-episode recovery STRATEGY, and the
    subagent config/policy. Every field is defaulted, so an empty policy reproduces
    the current default behavior (ADDITIVE).

    Trimmed in PLAN-20260623-001 (A): the inert ``*_ref`` carrier fields — and the
    sub-policies that were only refs or dead mirrors of live policy (prompt-context,
    memory, tool-environment, evaluation, observability) — were removed. They were
    dangling typed identifiers implying an external resolver that does not exist
    (96 §9 "wide in type, thin in carrying"); per ADR-0010 §F they are NOT re-added
    without a real runtime reader. The harness's evaluation/trace/memory ownership is
    carried by the live ``ExecutionHarnessCapabilities`` (``supports_*``) and the
    emitted outcome evidence, NOT by inert input refs. The
    ``test_execution_harness_policy_carries_no_unresolved_ref`` contract guard pins
    the no-``*_ref`` rule.
    """

    model_config = ConfigDict(frozen=True)

    multi_turn: bool = True
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    recovery: RecoveryPolicy = Field(default_factory=RecoveryPolicy)
    subagents: SubagentPolicy = Field(default_factory=SubagentPolicy)


class ResumeContext(BaseModel):
    """Typed lease/resume context handed to the harness for a resumed episode.

    Carries control-owned identifiers ONLY (lease token, prior attempt/resume
    markers); the harness reads them but does not own the durable lease.
    """

    model_config = ConfigDict(frozen=True)

    lease_token: str | None = None
    resume_from_attempt_id: str | None = None
    prior_failure: FailureCategory | None = None
    retry_no: int = Field(default=0, ge=0)


class ExecutionEpisodeRequest(BaseModel):
    """Typed input to one execution episode (one node turn).

    ``node_config`` is the node recipe/config — an *input*, not a load-bearing
    output — so a plain ``dict`` is acceptable here. The ``policy`` field carries
    the broad typed execution-policy surface (§0.4); ``resume`` carries the typed
    control-owned lease/resume context. Both default to empty so existing callers
    keep working (ADDITIVE). ``session`` IS the control-owned session/interaction
    snapshot the harness reads (never mutates).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    session: RuntimeSession
    node_key: str
    agent_role: str
    node_config: dict[str, Any]
    execution_id: str
    trace_id: str
    lease_token: str | None = None
    policy: ExecutionHarnessPolicy = Field(default_factory=ExecutionHarnessPolicy)
    resume: ResumeContext | None = None


# --- typed episode outputs (broad harness outputs, §0.4) ----------------------


class ModelInvocationEvidence(BaseModel):
    """Typed evidence for one model invocation made during the episode.

    Emission-only evidence the harness PROPOSES; the durable record is written by
    control. Defaults to empty so the default harness can populate what it has.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str | None = None
    round_no: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    stop_reason: str | None = None


class ToolCallEvidence(BaseModel):
    """Typed evidence for one tool call observed during the episode."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    tool_name: str
    round_no: int = Field(default=0, ge=0)
    is_error: bool = False


class ProposedTransitionKind(StrEnum):
    """Typed control-transition the harness PROPOSES (control authorizes)."""

    COMPLETE = "complete"
    FAIL = "fail"
    REQUEUE = "requeue"

    def to_apply_intent_kind(self) -> ApplyIntentKind:
        """Map to the control-plane ``ApplyIntentKind`` (authorization stays control)."""

        return {
            ProposedTransitionKind.COMPLETE: ApplyIntentKind.COMPLETE,
            ProposedTransitionKind.FAIL: ApplyIntentKind.FAIL,
            ProposedTransitionKind.REQUEUE: ApplyIntentKind.REQUEUE,
        }[self]


class ProposedStateTransition(BaseModel):
    """A typed transition the harness PROPOSES for ``node_key``.

    Advisory only: control decides whether to apply it (no authorization here).
    """

    model_config = ConfigDict(frozen=True)

    node_key: str
    kind: ProposedTransitionKind
    reason: str = ""


class RecoverySignalKind(StrEnum):
    """Typed in-episode recovery signal the harness emits."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    NEEDS_REPLAN = "needs_replan"


class RecoverySignal(BaseModel):
    """Typed recovery/repair signal proposed by the harness's recovery strategy."""

    model_config = ConfigDict(frozen=True)

    kind: RecoverySignalKind
    failure: FailureCategory = FailureCategory.NONE
    detail: str = ""


class ExecutionEpisodeOutcome(BaseModel):
    """Typed output of one execution episode (broad harness outputs, §0.4).

    ``report`` carries the load-bearing final result + persisted evidence;
    ``failure`` is the typed failure classification. The additive fields surface
    the rest of the broad output contract as TYPED data: ``model_invocations`` /
    ``tool_evidence`` / ``verifier_evidence`` (in-loop verifier results) /
    ``trace_event_refs`` are EMITTED evidence; ``proposed_transitions`` /
    ``recovery_signals`` are ADVISORY proposals (control authorizes + writes).
    All additive fields default to empty and are populated from already-available
    report data, so behavior is unchanged. No untyped ``dict``/``Any`` is used for
    load-bearing data.
    """

    model_config = ConfigDict(frozen=True)

    report: AgentReport
    failure: FailureCategory = FailureCategory.NONE
    final_result: str | None = None
    model_invocations: tuple[ModelInvocationEvidence, ...] = ()
    tool_evidence: tuple[ToolCallEvidence, ...] = ()
    verifier_evidence: tuple[VerifierResult, ...] = ()
    trace_event_refs: tuple[str, ...] = ()
    proposed_transitions: tuple[ProposedStateTransition, ...] = ()
    recovery_signals: tuple[RecoverySignal, ...] = ()


@runtime_checkable
class ExecutionHarness(Protocol):
    """Replaceable BROAD single-agent execution shell around one episode.

    The contract OWNS the multi-turn execution policy (loop + context + memory
    retrieval + recovery strategy + in-loop verification + trace emission +
    subagents + budget); it PROPOSES outcomes and EMITS evidence. Durable state
    writes + transition authorization stay in control/gating (§0.3 litmus).
    """

    name: str

    async def run_episode(
        self, request: ExecutionEpisodeRequest
    ) -> ExecutionEpisodeOutcome: ...


class HydraMindExecutionHarness:
    """Default :class:`ExecutionHarness` composing the existing pieces.

    It adapts an :class:`ExecutionEpisodeRequest` to the existing
    :class:`AgentNodeInvoker` (prompt/context + multi-turn dispatch + tool-loop +
    report + verifier) and wraps the returned report in an
    :class:`ExecutionEpisodeOutcome` with a typed failure classification plus the
    typed broad outputs populated from already-available report data.

    Exceptions from ``node_invoker.invoke`` propagate UNCHANGED so that the
    worker/control error handling is preserved (e.g. the "unresolved tool calls"
    ``RuntimeError`` is not swallowed). Failure classification is derived only
    from a report that completed normally.

    BEHAVIOR-PRESERVING: ``request.policy``/``request.resume`` are accepted but
    the default harness drives the SAME ``AgentNodeInvoker`` path as before; the
    typed broad outputs are derived from the report (no new side effects).
    """

    name = "HydraMindExecutionHarness"

    def __init__(self, *, node_invoker: AgentNodeInvoker) -> None:
        self._node_invoker = node_invoker

    async def run_episode(
        self, request: ExecutionEpisodeRequest
    ) -> ExecutionEpisodeOutcome:
        lease_token = (
            request.resume.lease_token
            if request.resume is not None and request.resume.lease_token is not None
            else request.lease_token
        )
        report = await self._node_invoker.invoke(
            session=request.session,
            node_key=request.node_key,
            agent_role=request.agent_role,
            node_config=request.node_config,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            lease_token=lease_token,
        )
        report = _with_harness_identity(report, self.name)
        failure = _classify_report_failure(report)
        return ExecutionEpisodeOutcome(
            report=report,
            failure=failure,
            final_result=_final_result(report),
            verifier_evidence=tuple(report.verifier_results),
            proposed_transitions=_proposed_transitions(report, failure),
            recovery_signals=_recovery_signals(failure),
        )


def _with_harness_identity(report: AgentReport, harness_id: str) -> AgentReport:
    """Attach report-level harness identity without changing user output."""

    return report.model_copy(update={"harness_id": harness_id})


def _classify_report_failure(report: AgentReport) -> FailureCategory:
    """Derive a best-effort typed failure category from a completed report.

    Only failures representable on the typed ``AgentReport`` are classified.
    A verifier result with ``passed is False`` maps to ``VERIFICATION_FAILED``;
    otherwise the episode is treated as ``NONE`` (success). We intentionally do
    not invent new untyped state: model/tool/timeout errors surface as raised
    exceptions (propagated unchanged), not as a returned report, so they are not
    derivable here.
    """

    if any(not result.passed for result in report.verifier_results):
        return FailureCategory.VERIFICATION_FAILED
    return FailureCategory.NONE


def _final_result(report: AgentReport) -> str | None:
    """Best-effort typed final-result text from already-available report output."""

    text = report.output.get("text")
    return text if isinstance(text, str) else None


def _proposed_transitions(
    report: AgentReport, failure: FailureCategory
) -> tuple[ProposedStateTransition, ...]:
    """Propose (advisory) the transition implied by the completed report.

    Control AUTHORIZES + WRITES; this is emission-only derivation from existing
    data, so it adds no side effects and does not change behavior.
    """

    if failure is FailureCategory.NONE:
        kind = ProposedTransitionKind.COMPLETE
        reason = ""
    else:
        kind = ProposedTransitionKind.FAIL
        reason = failure.value
    return (
        ProposedStateTransition(node_key=report.node_key, kind=kind, reason=reason),
    )


def _recovery_signals(failure: FailureCategory) -> tuple[RecoverySignal, ...]:
    """Derive an advisory recovery signal from the typed failure classification."""

    if failure is FailureCategory.NONE:
        return ()
    return (
        RecoverySignal(
            kind=RecoverySignalKind.NON_RETRYABLE,
            failure=failure,
            detail=failure.value,
        ),
    )


__all__ = [
    "CompactionResult",
    "ContextCompactionRequest",
    "ExecutionConstraints",
    "ExecutionEpisodeOutcome",
    "ExecutionEpisodeRequest",
    "ExecutionHarness",
    "ExecutionHarnessCapabilities",
    "ExecutionHarnessCapabilityError",
    "ExecutionHarnessFeature",
    "ExecutionHarnessPolicy",
    "ExecutionHarnessRuntime",
    "FailureCategory",
    "HydraMindExecutionHarness",
    "ModelInvocationEvidence",
    "ProposedStateTransition",
    "ProposedTransitionKind",
    "ProviderExecutionHarnessRuntime",
    "RecoveryPolicy",
    "RecoverySignal",
    "RecoverySignalKind",
    "ResumeContext",
    "SubagentContext",
    "SubagentHandle",
    "SubagentPolicy",
    "SubagentSpawnRequest",
    "ToolCallEvidence",
]
