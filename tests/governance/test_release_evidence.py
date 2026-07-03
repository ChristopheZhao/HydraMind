"""Release evidence and replay/evaluation governance contracts."""

from __future__ import annotations

from hydramind.governance import (
    AssetVersionRef,
    ChangeClass,
    EvaluationCase,
    EvaluationResult,
    MetricScore,
    ReleaseEvidence,
    ReplayInputPackage,
    ReplayResult,
)


def test_release_evidence_is_an_audit_only_data_model() -> None:
    # S102 (DEV-32): governance carries AUDIT EVIDENCE, not a release DECISION.
    # The deterministic auto-approval (evaluate_release_evidence/ReleaseDecision) was
    # removed; the evidence contract remains a serializable record only.
    evidence = ReleaseEvidence(
        release_version="0.1.0",
        change_classes=(ChangeClass.PROMPT_CHANGE,),
        prompt_bundle_version="prompts-20260518",
        replay_report_ref="artifacts/replay/prompt-change.json",
    )
    dumped = evidence.model_dump()
    assert dumped["release_version"] == "0.1.0"
    assert dumped["replay_report_ref"] == "artifacts/replay/prompt-change.json"


def test_metric_scores_compute_pass_fail_from_thresholds() -> None:
    result = EvaluationResult(
        case_id="case-1",
        metric_scores=(
            MetricScore(name="gate_correctness", score=0.98, threshold=0.95),
            MetricScore(
                name="latency_seconds",
                score=4.0,
                threshold=5.0,
                higher_is_better=False,
            ),
        ),
    )

    assert result.pass_fail is True
    assert [score.passed for score in result.metric_scores] == [True, True]


def test_metric_scores_fail_when_threshold_is_not_met() -> None:
    result = EvaluationResult(
        case_id="case-2",
        metric_scores=(MetricScore(name="explanation_quality", score=0.7, threshold=0.85),),
    )

    assert result.pass_fail is False
    assert result.metric_scores[0].passed is False


def test_replay_and_evaluation_contracts_are_serializable() -> None:
    asset = AssetVersionRef(
        kind="prompt_bundle",
        name="short_video",
        version="2026-05-18",
        digest="sha256:123",
        path="examples/short_video/prompts.yaml",
    )
    replay_input = ReplayInputPackage(
        source_trace_id="trace-1",
        release_version="0.1.0",
        prompt_bundle_version="prompts-1",
        policy_bundle_version="policy-1",
        frozen_input_ref="artifacts/replay/input.json",
        expected_output_ref="artifacts/replay/expected.json",
        asset_refs=(asset,),
    )
    replay_result = ReplayResult(
        source_trace_id="trace-1",
        new_output_ref="artifacts/replay/new.json",
        diff_summary="No semantic behavior change.",
        behavior_changed=False,
    )
    evaluation_case = EvaluationCase(
        case_id="case-1",
        case_type="short_video_mock",
        inputs_ref="artifacts/eval/input.json",
        label_ref="artifacts/eval/label.json",
        metric_set=("gate_correctness", "tool_contract"),
    )

    assert replay_input.model_dump()["asset_refs"][0]["name"] == "short_video"
    assert replay_result.model_dump()["behavior_changed"] is False
    assert evaluation_case.model_dump()["metric_set"] == (
        "gate_correctness",
        "tool_contract",
    )
