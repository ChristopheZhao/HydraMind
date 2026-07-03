"""Verification helper module boundary tests."""

from __future__ import annotations

from hydramind.control import SemanticRubricCheck
from hydramind.orchestration.verification_content import (
    check_local_assets,
    extract_image_targets,
)
from hydramind.orchestration.verification_semantic import (
    semantic_json_payload,
    semantic_results_from_payload,
    truncate_semantic_response,
)


def test_content_helpers_resolve_markdown_relative_assets(tmp_path) -> None:
    asset_path = tmp_path / "reports" / "assets" / "chart.png"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"png")
    text = "![chart](assets/chart.png) ![missing](assets/missing.png)"
    feedback = []

    result = check_local_assets(
        extract_image_targets(text),
        tmp_path,
        "reports/report.md",
        feedback,
        "write",
    )

    assert not result.passed
    assert result.detail["present"] == ["assets/chart.png"]
    assert result.detail["missing"] == ["assets/missing.png"]
    assert feedback[0].source == "verifier.content_quality"


def test_semantic_json_payload_accepts_code_fenced_embedded_json() -> None:
    payload = semantic_json_payload('```json\n{"checks": []}\n```')

    assert payload == {"checks": []}


def test_semantic_results_use_score_as_authority() -> None:
    check = SemanticRubricCheck(
        name="grounding",
        description="grounded",
        min_score=0.8,
    )

    results, feedback = semantic_results_from_payload(
        payload={
            "checks": [
                {
                    "name": "grounding",
                    "score": "0.5",
                    "passed": True,
                    "comment": "weak citations",
                }
            ]
        },
        checks=(check,),
        node_key="write",
        artifact_ref="report.md",
    )

    assert not results[0].passed
    assert results[0].detail["score_judge_mismatch"] is True
    assert feedback[0].suggested_action is not None


def test_truncate_semantic_response_keeps_repair_payload_bounded() -> None:
    content = "x" * 13000

    truncated = truncate_semantic_response(content)

    assert len(truncated) < len(content)
    assert truncated.startswith("x" * 12000)
    assert truncated.endswith("\n...[truncated]")


def test_semantic_missing_response_item_flags_feedback() -> None:
    check = SemanticRubricCheck(name="style", description="style", min_score=0.7)

    results, feedback = semantic_results_from_payload(
        payload={"checks": []},
        checks=(check,),
        node_key="write",
        artifact_ref="report.md",
    )

    assert results[0].detail["missing_from_response"] is True
    assert feedback[0].message == (
        "semantic check 'style' failed: missing from judge response"
    )
