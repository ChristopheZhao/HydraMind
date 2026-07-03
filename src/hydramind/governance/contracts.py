"""Minimal governance contracts for release, replay, and evaluation evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChangeClass(StrEnum):
    """Change classes that influence release evidence requirements."""

    PROMPT_CHANGE = "prompt_change"
    NODE_LOGIC_OR_CONTRACT_CHANGE = "node_logic_or_contract_change"
    GATE_RULE_OR_THRESHOLD_CHANGE = "gate_rule_or_threshold_change"
    MEMORY_WRITEBACK_POLICY_CHANGE = "memory_writeback_policy_change"
    ROUTING_OR_CONTROL_POLICY_CHANGE = "routing_or_control_policy_change"


class AssetVersionRef(BaseModel):
    """Versioned governance asset reference."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str | None = None
    path: str | None = None


class ReplayInputPackage(BaseModel):
    """Frozen inputs needed to replay a prior run."""

    model_config = ConfigDict(frozen=True)

    source_trace_id: str = Field(min_length=1)
    release_version: str = Field(min_length=1)
    prompt_bundle_version: str = Field(min_length=1)
    policy_bundle_version: str = Field(min_length=1)
    frozen_input_ref: str = Field(min_length=1)
    expected_output_ref: str = Field(min_length=1)
    asset_refs: tuple[AssetVersionRef, ...] = ()


class ReplayResult(BaseModel):
    """Replay output summary used by release checks."""

    model_config = ConfigDict(frozen=True)

    source_trace_id: str = Field(min_length=1)
    new_output_ref: str = Field(min_length=1)
    diff_summary: str = Field(min_length=1)
    behavior_changed: bool
    gate_diff: str | None = None
    writeback_diff: str | None = None


class EvaluationCase(BaseModel):
    """A labeled or expected-output case for offline quality checks."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    case_type: str = Field(min_length=1)
    inputs_ref: str = Field(min_length=1)
    label_ref: str = Field(min_length=1)
    metric_set: tuple[str, ...] = Field(min_length=1)


class MetricScore(BaseModel):
    """Single metric score with an optional pass threshold."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    score: float
    threshold: float | None = None
    higher_is_better: bool = True

    @property
    def passed(self) -> bool:
        if self.threshold is None:
            return True
        if self.higher_is_better:
            return self.score >= self.threshold
        return self.score <= self.threshold


class EvaluationResult(BaseModel):
    """Evaluation output for one case."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    metric_scores: tuple[MetricScore, ...] = Field(min_length=1)
    notes: str | None = None

    @property
    def pass_fail(self) -> bool:
        return all(score.passed for score in self.metric_scores)


class ReleaseEvidence(BaseModel):
    """Evidence supplied before releasing prompt, node, gate, memory, or control changes."""

    model_config = ConfigDict(frozen=True)

    release_version: str = Field(min_length=1)
    change_classes: tuple[ChangeClass, ...] = Field(min_length=1)
    prompt_bundle_version: str | None = None
    policy_bundle_version: str | None = None
    replay_report_ref: str | None = None
    evaluation_report_ref: str | None = None
    regression_note_ref: str | None = None
    diff_summary_ref: str | None = None
    rollback_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# NOTE: governance owns AUDIT EVIDENCE, not business decisions (ADR-0008). There is
# deliberately NO deterministic auto-approval verdict here — release approval is a
# human/external decision recorded against this evidence, not synthesized in code.
