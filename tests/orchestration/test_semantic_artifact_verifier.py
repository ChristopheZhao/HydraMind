"""SemanticArtifactVerifierRunner — opt-in LLM-as-judge rubric runner."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from hydramind.control import (
    AgentReport,
    GoalArtifactQualityContract,
    RuntimeSession,
    SemanticRubric,
    SemanticRubricCheck,
    TaskContract,
    VerifierResult,
)
from hydramind.control.states import DecisionAction
from hydramind.harness.base import (
    InvocationResult,
    Message,
    ModelHint,
    StopReason,
    ToolSpec,
    Usage,
)
from hydramind.harness.provider import ModelProvider
from hydramind.orchestration import (
    ArtifactContainmentVerifierRunner,
    CompositeVerifierRunner,
    SemanticArtifactVerifierRunner,
    TaskContractVerifierRunner,
)

# --- test helpers -----------------------------------------------------------


class RecordingMockHarness(ModelProvider):
    """Minimal ModelProvider used to script ``complete`` returns + raises.

    Each entry in ``scripted`` is either a string (returned as
    ``InvocationResult.content``) or an exception (raised from ``complete``).
    """

    name = "recording-mock"

    def __init__(self, scripted: Iterable[Any] | None = None) -> None:
        self._scripted: deque[Any] = deque(scripted or ())
        self.invocations: list[dict[str, Any]] = []

    def queue(self, *items: Any) -> None:
        self._scripted.extend(items)

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
    ) -> InvocationResult:
        self.invocations.append(
            {
                "messages": list(messages),
                "tools": list(tools or ()),
                "system": system,
                "role": role,
                "model_hint": model_hint,
                "max_tokens": max_tokens,
            }
        )
        if not self._scripted:
            raise RuntimeError("RecordingMockHarness: no scripted response left")
        item = self._scripted.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return InvocationResult(
                content=item,
                stop_reason=StopReason.END_TURN,
                usage=Usage(),
                model_id="recording-mock",
            )
        if isinstance(item, InvocationResult):
            return item
        raise TypeError(f"unsupported scripted entry: {type(item)!r}")

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        del role, model_hint
        return "recording-mock"

    def context_limit(self, role: str | None = None) -> int:
        del role
        return 200_000


def _session() -> RuntimeSession:
    return RuntimeSession(workflow_name="semantic-judge")


def _report() -> AgentReport:
    return AgentReport(node_key="write", agent_id="writer")


def _node_config(contract: TaskContract) -> dict[str, object]:
    return {"task_contract": contract.model_dump(mode="json")}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _two_check_rubric() -> SemanticRubric:
    return SemanticRubric(
        checks=(
            SemanticRubricCheck(
                name="technical_depth",
                description="post explains its core idea with depth",
                min_score=0.6,
            ),
            SemanticRubricCheck(
                name="source_grounding",
                description="claims are backed by citations",
                min_score=0.5,
            ),
        )
    )


def _passing_judge_response() -> str:
    return json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "score": 0.82,
                    "passed": True,
                    "comment": "deep dive on architecture",
                },
                {
                    "name": "source_grounding",
                    "score": 0.71,
                    "passed": True,
                    "comment": "multiple citations",
                },
            ],
            "overall_summary": "solid post",
        }
    )


# --- tests ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_quality_contract_absent(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "hello")
    harness = RecordingMockHarness()
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(expected_artifacts=("blog.md",))

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()
    assert harness.invocations == []


@pytest.mark.asyncio
async def test_skips_when_semantic_rubric_is_none(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "hello")
    harness = RecordingMockHarness()
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(min_length=1),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()
    assert harness.invocations == []


@pytest.mark.asyncio
async def test_skips_when_rubric_disabled(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "hello")
    harness = RecordingMockHarness()
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    rubric = SemanticRubric(
        checks=(SemanticRubricCheck(name="depth"),),
        enabled=False,
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(semantic_rubric=rubric),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()
    assert harness.invocations == []


@pytest.mark.asyncio
async def test_skips_when_rubric_has_no_checks(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "hello")
    harness = RecordingMockHarness()
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    rubric = SemanticRubric(checks=(), enabled=True)
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(semantic_rubric=rubric),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()
    assert harness.invocations == []


@pytest.mark.asyncio
async def test_skips_when_artifact_missing(tmp_path: Path) -> None:
    harness = RecordingMockHarness()
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    rubric = SemanticRubric(checks=(SemanticRubricCheck(name="depth"),))
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(semantic_rubric=rubric),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()
    assert harness.invocations == []


@pytest.mark.asyncio
async def test_pass_case_emits_results_no_feedback(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "a clear and well-cited blog body")
    harness = RecordingMockHarness(scripted=[_passing_judge_response()])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    rubric = _two_check_rubric()
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(semantic_rubric=rubric),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    names = [r.name for r in verified.verifier_results]
    assert names == [
        "artifact.semantic.technical_depth",
        "artifact.semantic.source_grounding",
    ]
    assert all(r.passed for r in verified.verifier_results)
    assert verified.verifier_results[0].score == 0.82
    assert verified.verifier_results[1].score == 0.71
    assert verified.feedback == ()
    assert len(harness.invocations) == 1
    assert harness.invocations[0]["role"] == "verifier"
    assert harness.invocations[0]["system"].startswith("You are an opt-in JSON-only")


@pytest.mark.asyncio
async def test_fail_case_emits_revise_feedback(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "thin body")
    response = json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "score": 0.85,
                    "passed": True,
                    "comment": "ok",
                },
                {
                    "name": "source_grounding",
                    "score": 0.30,
                    "passed": False,
                    "comment": "no citations",
                },
            ],
            "overall_summary": "weak grounding",
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {r.name: r for r in verified.verifier_results}
    assert by_name["artifact.semantic.technical_depth"].passed is True
    failed = by_name["artifact.semantic.source_grounding"]
    assert failed.passed is False
    assert failed.score == 0.30
    assert failed.detail["min_score"] == 0.5
    assert failed.detail["comment"] == "no citations"

    assert len(verified.feedback) == 1
    fb = verified.feedback[0]
    assert fb.source == "verifier.semantic_rubric"
    assert fb.severity == "error"
    assert fb.suggested_action is DecisionAction.REVISE
    assert fb.target_node_key == "write"
    assert "source_grounding" in fb.message


@pytest.mark.asyncio
async def test_missing_check_in_response_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "ok body")
    response = json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "score": 0.9,
                    "passed": True,
                    "comment": "ok",
                }
            ],
            "overall_summary": "partial",
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {r.name: r for r in verified.verifier_results}
    missing = by_name["artifact.semantic.source_grounding"]
    assert missing.passed is False
    assert missing.detail["missing_from_response"] is True
    assert by_name["artifact.semantic.technical_depth"].passed is True
    assert any(
        fb.detail.get("missing_from_response") is True
        for fb in verified.feedback
    )


@pytest.mark.asyncio
async def test_check_mapping_response_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "ok body")
    response = json.dumps(
        {
            "checks": {
                "technical_depth": {
                    "score": 0.9,
                    "passed": True,
                    "comment": "deep",
                },
                "source_grounding": {
                    "score": 0.8,
                    "passed": True,
                    "comment": "grounded",
                },
            },
            "overall_summary": "solid",
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert all(result.passed for result in verified.verifier_results)
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_top_level_check_mapping_response_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "ok body")
    response = json.dumps(
        {
            "technical_depth": {
                "score": 0.91,
                "passed": True,
                "comment": "deep",
            },
            "source_grounding": {
                "score": 0.82,
                "passed": True,
                "comment": "grounded",
            },
            "overall_summary": "solid",
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {result.name: result for result in verified.verifier_results}
    assert by_name["artifact.semantic.technical_depth"].score == 0.91
    assert by_name["artifact.semantic.source_grounding"].score == 0.82
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_harness_error_degrades_gracefully(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "body")
    harness = RecordingMockHarness(scripted=[RuntimeError("boom")])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    results_by_name = {r.name: r for r in verified.verifier_results}
    degraded = results_by_name["artifact.semantic_rubric"]
    assert degraded.passed is False
    assert degraded.detail["degraded"] is True
    assert "RuntimeError" in degraded.detail["error"]
    assert "boom" in degraded.detail["error"]
    # No per-check results — we never parsed a response.
    assert "artifact.semantic.technical_depth" not in results_by_name
    assert len(verified.feedback) == 1
    fb = verified.feedback[0]
    assert fb.source == "verifier.semantic_rubric"
    assert fb.message == "semantic verifier unavailable"
    assert fb.suggested_action is None


@pytest.mark.asyncio
async def test_code_fence_wrapped_json_parses(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "body")
    wrapped = (
        "Here is the judgment:\n\n"
        "```json\n"
        + _passing_judge_response()
        + "\n```\n"
    )
    harness = RecordingMockHarness(scripted=[wrapped])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    names = [r.name for r in verified.verifier_results]
    assert names == [
        "artifact.semantic.technical_depth",
        "artifact.semantic.source_grounding",
    ]
    assert all(r.passed for r in verified.verifier_results)
    assert verified.feedback == ()
    # One bounded repair — only one harness call.
    assert len(harness.invocations) == 1


@pytest.mark.asyncio
async def test_non_json_response_uses_one_json_repair(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "body")
    harness = RecordingMockHarness(
        scripted=[
            "technical_depth looks strong; source_grounding is cited.",
            _passing_judge_response(),
        ]
    )
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert len(harness.invocations) == 2
    assert harness.invocations[1]["system"].startswith(
        "You repair rubric judge output"
    )
    assert harness.invocations[1]["max_tokens"] == 512
    assert all(result.passed for result in verified.verifier_results)
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_json_repair_failure_degrades_gracefully(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "body")
    harness = RecordingMockHarness(scripted=["not json", "still not json"])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert len(harness.invocations) == 2
    result = verified.verifier_results[0]
    assert result.name == "artifact.semantic_rubric"
    assert result.passed is False
    assert result.detail["degraded"] is True
    assert "semantic JSON repair failed" in result.detail["error"]


@pytest.mark.asyncio
async def test_deterministic_failure_short_circuits_semantic(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "body")
    harness = RecordingMockHarness()  # MUST NOT be invoked
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            min_length=10_000,
            semantic_rubric=_two_check_rubric(),
        ),
    )
    failed_length = VerifierResult(
        name="artifact.length",
        passed=False,
        detail={"length": 4, "min_length": 10_000, "artifact": "blog.md"},
    )
    seeded = AgentReport(
        node_key="write",
        agent_id="writer",
        verifier_results=(failed_length,),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=seeded,
    )

    assert harness.invocations == []
    skipped = verified.verifier_results[-1]
    assert skipped.name == "artifact.semantic_rubric"
    assert skipped.passed is False
    assert skipped.detail["skipped"] is True
    assert skipped.detail["reason"] == "deterministic_failed"
    # Skipped path emits no extra feedback (deterministic feedback owns repair).
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_composite_ordering_respects_short_circuit(tmp_path: Path) -> None:
    # Deterministic-first contract is now enforced by the surviving boundary
    # safety verifier (artifact-root containment), not a rule quality runner:
    # the agent semantic judge skips when a safety check has already failed.
    quality = GoalArtifactQualityContract(semantic_rubric=_two_check_rubric())
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=quality,
    )

    # 1) Artifact missing → no semantic call (no harness response queued).
    harness_missing = RecordingMockHarness()
    composite_missing = CompositeVerifierRunner(
        runners=(
            TaskContractVerifierRunner(tmp_path),
            ArtifactContainmentVerifierRunner(tmp_path),
            SemanticArtifactVerifierRunner(
                provider=harness_missing, artifact_root=tmp_path
            ),
        )
    )
    out_missing = await composite_missing.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )
    assert harness_missing.invocations == []
    assert all(
        not r.name.startswith("artifact.semantic")
        for r in out_missing.verifier_results
    )

    # 2) Artifact present but a containment safety failure (path-escaping local
    #    asset) → no semantic call; the judge is skipped deterministically.
    _write(tmp_path / "blog.md", "![escape](../../outside.png)")
    harness_det_fail = RecordingMockHarness()
    composite_det_fail = CompositeVerifierRunner(
        runners=(
            TaskContractVerifierRunner(tmp_path),
            ArtifactContainmentVerifierRunner(tmp_path),
            SemanticArtifactVerifierRunner(
                provider=harness_det_fail, artifact_root=tmp_path
            ),
        )
    )
    out_det_fail = await composite_det_fail.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )
    assert harness_det_fail.invocations == []
    skipped = [
        r for r in out_det_fail.verifier_results
        if r.name == "artifact.semantic_rubric"
    ]
    assert len(skipped) == 1
    assert skipped[0].detail["skipped"] is True

    # 3) Empty rubric → no semantic call even with everything else green.
    _write(tmp_path / "blog.md", "a clean body with no local image refs")
    harness_no_rubric = RecordingMockHarness()
    contract_empty_rubric = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=SemanticRubric(checks=()),
        ),
    )
    composite_empty = CompositeVerifierRunner(
        runners=(
            TaskContractVerifierRunner(tmp_path),
            ArtifactContainmentVerifierRunner(tmp_path),
            SemanticArtifactVerifierRunner(
                provider=harness_no_rubric, artifact_root=tmp_path
            ),
        )
    )
    out_empty = await composite_empty.verify(
        session=_session(),
        node_config=_node_config(contract_empty_rubric),
        report=_report(),
    )
    assert harness_no_rubric.invocations == []
    assert all(
        not r.name.startswith("artifact.semantic")
        for r in out_empty.verifier_results
    )

    # 4) Everything green → semantic runs exactly once.
    _write(tmp_path / "blog.md", "a clean body with no local image refs")
    harness_ok = RecordingMockHarness(scripted=[_passing_judge_response()])
    composite_ok = CompositeVerifierRunner(
        runners=(
            TaskContractVerifierRunner(tmp_path),
            ArtifactContainmentVerifierRunner(tmp_path),
            SemanticArtifactVerifierRunner(
                provider=harness_ok, artifact_root=tmp_path
            ),
        )
    )
    out_ok = await composite_ok.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )
    assert len(harness_ok.invocations) == 1
    names = {r.name for r in out_ok.verifier_results}
    assert "artifact.semantic.technical_depth" in names
    assert "artifact.semantic.source_grounding" in names


@pytest.mark.asyncio
async def test_score_below_min_overrides_judge_passed_true(tmp_path: Path) -> None:
    """Score authority: judge says passed=True but score < min_score -> fail."""

    _write(tmp_path / "blog.md", "body")
    response = json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "score": 0.1,
                    "passed": True,
                    "comment": "judge says yes but score very low",
                },
                {
                    "name": "source_grounding",
                    "score": 0.9,
                    "passed": True,
                    "comment": "all good",
                },
            ]
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {r.name: r for r in verified.verifier_results}
    failed = by_name["artifact.semantic.technical_depth"]
    assert failed.passed is False
    assert failed.score == 0.1
    assert failed.detail["score_judge_mismatch"] is True
    assert failed.detail["judge_passed_raw"] is True
    # FeedbackRecord must be emitted with severity=error.
    fbs = [fb for fb in verified.feedback if "technical_depth" in fb.message]
    assert len(fbs) == 1
    assert fbs[0].severity == "error"
    assert fbs[0].suggested_action is DecisionAction.REVISE
    # The other check passes (score >= min) and records no mismatch.
    ok = by_name["artifact.semantic.source_grounding"]
    assert ok.passed is True
    assert ok.detail["score_judge_mismatch"] is False


@pytest.mark.asyncio
async def test_score_above_min_overrides_judge_passed_false(tmp_path: Path) -> None:
    """Score authority: judge says passed=False but score >= min_score -> pass."""

    _write(tmp_path / "blog.md", "body")
    response = json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "score": 0.9,
                    "passed": False,
                    "comment": "judge says no but score very high",
                },
                {
                    "name": "source_grounding",
                    "score": 0.8,
                    "passed": True,
                    "comment": "all good",
                },
            ]
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {r.name: r for r in verified.verifier_results}
    passed = by_name["artifact.semantic.technical_depth"]
    assert passed.passed is True
    assert passed.score == 0.9
    assert passed.detail["score_judge_mismatch"] is True
    assert passed.detail["judge_passed_raw"] is False
    # No feedback record because score authority passed the check.
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_no_score_falls_back_to_judge_bool(tmp_path: Path) -> None:
    """When the judge omits ``score``, fall back to the ``passed`` bool."""

    _write(tmp_path / "blog.md", "body")
    response = json.dumps(
        {
            "checks": [
                {
                    "name": "technical_depth",
                    "passed": True,
                    "comment": "no score but judge says ok",
                },
                {
                    "name": "source_grounding",
                    "passed": True,
                    "comment": "ok",
                },
            ]
        }
    )
    harness = RecordingMockHarness(scripted=[response])
    runner = SemanticArtifactVerifierRunner(
        provider=harness, artifact_root=tmp_path
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    by_name = {r.name: r for r in verified.verifier_results}
    item = by_name["artifact.semantic.technical_depth"]
    assert item.passed is True
    assert item.score is None
    assert item.detail["score_judge_mismatch"] is False
    assert item.detail["judge_passed_raw"] is True


@pytest.mark.asyncio
async def test_long_artifact_is_truncated(tmp_path: Path) -> None:
    big_text = "X" * 50_000
    _write(tmp_path / "blog.md", big_text)
    harness = RecordingMockHarness(scripted=[_passing_judge_response()])
    runner = SemanticArtifactVerifierRunner(
        provider=harness,
        artifact_root=tmp_path,
        max_chars_per_artifact=1_000,
    )
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            semantic_rubric=_two_check_rubric()
        ),
    )

    await runner.verify(
        session=_session(),
        node_config=_node_config(contract),
        report=_report(),
    )

    assert len(harness.invocations) == 1
    prompt = harness.invocations[0]["messages"][0].content
    assert "… [truncated]" in prompt
    # Prompt body must reflect the truncated artifact, not the full 50k chars.
    assert prompt.count("X") <= 1_500
