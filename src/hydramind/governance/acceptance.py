"""Typed acceptance taxonomy + report (S7a).

Implements the five-class acceptance taxonomy from
``docs/architecture/95-execution-harness-correction.md`` §9 and the acceptance
report fields required by ``docs/plans/active/PLAN-20260618-001.md`` §6:
acceptance must report by ``task + model/provider + harness + evaluator``.

Determinism note: like the rest of ``hydramind.governance`` (see
``contracts.py``), this module never calls ``datetime.now()``. ``created_at`` is
caller-supplied (optional string), so reports built in tests/library paths stay
deterministic.

Import-cycle note: ``orchestration.execution_harness`` defines the runtime
``FailureCategory``. Importing orchestration into governance would invert the
dependency direction (governance is a leaf evidence layer), so this module
defines a governance-local mirror ``AcceptanceFailureCategory`` with the same
member values plus a ``from_runtime`` adapter that maps any
orchestration ``FailureCategory`` (or string) without importing it at module
load time.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceClass(StrEnum):
    """The five acceptance evidence classes (95 §9).

    Class 1-3 are deterministic and run offline (no live model decides). Class
    4-5 require live provider/tool credentials and a model making decisions.
    """

    CONTRACT = "contract"  # Class 1 — schemas, type contracts, invariants.
    PLUMBING = "plumbing"  # Class 2 — control/queue/tool/state wiring.
    REPLAY = "replay"  # Class 3 — replay a known trace/fixture for regression.
    LIVE_AGENT = "live_agent"  # Class 4 — fixed task, live model, single agent.
    LIVE_MAS = "live_mas"  # Class 5 — live multi-agent collaboration.

    @property
    def is_live(self) -> bool:
        """True for the credential-gated, model-driven classes (4 and 5)."""

        return self in (AcceptanceClass.LIVE_AGENT, AcceptanceClass.LIVE_MAS)


class AcceptanceFailureCategory(StrEnum):
    """Governance-local mirror of orchestration ``FailureCategory``.

    Kept in sync with ``hydramind.orchestration.execution_harness.FailureCategory``
    by value, but defined here to avoid governance→orchestration import cycle.
    Use ``from_runtime`` to map a runtime category/string into this enum.
    """

    NONE = "none"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    UNRESOLVED_TOOL_CALLS = "unresolved_tool_calls"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"

    @classmethod
    def from_runtime(cls, value: object) -> AcceptanceFailureCategory:
        """Map an orchestration ``FailureCategory`` (or its string) to this enum.

        Accepts a ``StrEnum`` member, a plain string, or ``None`` (→ ``NONE``).
        Unknown values map to ``UNKNOWN`` rather than raising, so the evidence
        layer never crashes on a new runtime category it has not yet mirrored.
        """

        if value is None:
            return cls.NONE
        raw = value.value if isinstance(value, StrEnum) else str(value)
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class RecoveryBehavior(StrEnum):
    """Coarse recovery outcome observed during an acceptance run."""

    NOT_APPLICABLE = "not_applicable"
    NO_FAILURE = "no_failure"
    RECOVERED = "recovered"
    GATED = "gated"  # surfaced as a gate/contract failure instead of recovering.
    NOT_RECOVERED = "not_recovered"


class TaskRef(BaseModel):
    """The fixed task an acceptance run is reported against."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ModelProviderRef(BaseModel):
    """The model/provider an acceptance run used."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ExecutionHarnessRef(BaseModel):
    """The execution harness an acceptance run used."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    harness_id: str | None = None


class AcceptanceCost(BaseModel):
    """Optional cost summary for a run."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usd: float | None = None


class AcceptanceReport(BaseModel):
    """Typed acceptance result reported by ``task + model/provider + harness + evaluator``.

    Carries the §6 acceptance-profile fields. ``acceptance_class`` records which
    of the five evidence classes the run belongs to; the
    ``require_offline_class`` helper guards against labeling a replay/mock/local
    run as ``LIVE_AGENT``/``LIVE_MAS``.
    """

    model_config = ConfigDict(frozen=True)

    task: TaskRef
    model_provider: ModelProviderRef
    execution_harness: ExecutionHarnessRef
    tools_environment: str = Field(min_length=1)
    evaluator_profile: str = Field(min_length=1)
    acceptance_class: AcceptanceClass
    success: bool
    failure_category: AcceptanceFailureCategory = AcceptanceFailureCategory.NONE
    recovery_behavior: RecoveryBehavior = RecoveryBehavior.NOT_APPLICABLE
    cost: AcceptanceCost | None = None
    latency_ms: float | None = None
    evidence_refs: tuple[str, ...] = ()
    notes: str | None = None
    # Deterministic by default: caller-supplies a timestamp string if it wants
    # one. No ``datetime.now()`` here (see module docstring + governance pattern).
    created_at: str | None = None

    @model_validator(mode="after")
    def _validate_failure_consistency(self) -> AcceptanceReport:
        if self.success and self.failure_category is not AcceptanceFailureCategory.NONE:
            raise ValueError(
                "a successful AcceptanceReport must have failure_category=NONE"
            )
        return self

    def as_payload(self) -> dict[str, Any]:
        """Serialize to a plain JSON-able dict (enum values as strings)."""

        return self.model_dump(mode="json")


class AcceptanceLabelError(ValueError):
    """Raised when a report is mislabeled (e.g. a local run claimed as live)."""


def require_offline_class(report: AcceptanceReport) -> AcceptanceReport:
    """Guard: a replay/local/mock run must NOT be labeled live-agent/live-MAS.

    Returns the report unchanged when it is correctly labeled as one of the
    offline classes (CONTRACT/PLUMBING/REPLAY). Raises ``AcceptanceLabelError``
    if it carries a live acceptance class — the core negative-case from §7
    ("do not report replay/mock as live-agent or live-MAS acceptance").
    """

    if report.acceptance_class.is_live:
        raise AcceptanceLabelError(
            "offline/replay/local acceptance run must not be labeled "
            f"{report.acceptance_class.value!r}; live classes "
            "(live_agent/live_mas) require a live model-driven run"
        )
    return report


__all__ = [
    "AcceptanceClass",
    "AcceptanceCost",
    "AcceptanceFailureCategory",
    "AcceptanceLabelError",
    "AcceptanceReport",
    "ExecutionHarnessRef",
    "ModelProviderRef",
    "RecoveryBehavior",
    "TaskRef",
    "require_offline_class",
]
