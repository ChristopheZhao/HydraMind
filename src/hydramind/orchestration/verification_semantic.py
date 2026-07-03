"""Semantic verifier payload normalization and result helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from hydramind.control import (
    AgentReport,
    DecisionAction,
    FeedbackRecord,
    VerifierResult,
)

SEMANTIC_REPAIR_MAX_CHARS = 12_000


def has_failed_deterministic_content_check(report: AgentReport) -> bool:
    for result in report.verifier_results:
        if not result.name.startswith("artifact."):
            continue
        if result.name == "artifact.exists":
            continue
        if result.passed is False:
            return True
    return False


def semantic_degraded_report(report: AgentReport, exc: Exception) -> AgentReport:
    summary = str(exc) or type(exc).__name__
    error_text = f"{type(exc).__name__}: {summary[:200]}"
    result = VerifierResult(
        name="artifact.semantic_rubric",
        passed=False,
        score=None,
        detail={"error": error_text, "degraded": True},
    )
    feedback = FeedbackRecord(
        source="verifier.semantic_rubric",
        message="semantic verifier unavailable",
        target_node_key=report.node_key,
        severity="error",
        suggested_action=None,
        detail={"error": error_text, "degraded": True},
    )
    return report.model_copy(
        update={
            "verifier_results": (*report.verifier_results, result),
            "feedback": (*report.feedback, feedback),
        }
    )


def semantic_results_from_payload(
    *,
    payload: dict[str, Any],
    checks: tuple[Any, ...],
    node_key: str,
    artifact_ref: str,
) -> tuple[list[VerifierResult], list[FeedbackRecord]]:
    requested_names = {
        check.name for check in checks if isinstance(check.name, str) and check.name
    }
    by_name = semantic_items_by_name(
        payload=payload, requested_names=requested_names
    )
    new_results: list[VerifierResult] = []
    new_feedback: list[FeedbackRecord] = []
    for check in checks:
        result_name = f"artifact.semantic.{check.name}"
        item = by_name.get(check.name)
        if item is None:
            detail: dict[str, Any] = {
                "missing_from_response": True,
                "min_score": check.min_score,
                "criterion": check.description,
                "artifact": artifact_ref,
            }
            verifier_result = VerifierResult(
                name=result_name,
                passed=False,
                score=None,
                detail=detail,
            )
            new_results.append(verifier_result)
            new_feedback.append(
                FeedbackRecord(
                    source="verifier.semantic_rubric",
                    message=(
                        f"semantic check {check.name!r} failed: "
                        "missing from judge response"
                    ),
                    target_node_key=node_key,
                    severity="error",
                    suggested_action=DecisionAction.REVISE,
                    detail=detail,
                )
            )
            continue
        score = semantic_coerce_score(item.get("score"))
        comment_raw = item.get("comment")
        comment = comment_raw if isinstance(comment_raw, str) else ""
        passed_raw = item.get("passed")
        score_passes = score is not None and score >= check.min_score
        judge_says_passed = isinstance(passed_raw, bool) and passed_raw
        if score is not None:
            passed = score_passes
            score_judge_mismatch = judge_says_passed != score_passes
        else:
            passed = judge_says_passed
            score_judge_mismatch = False
        detail = {
            "comment": comment,
            "min_score": check.min_score,
            "criterion": check.description,
            "artifact": artifact_ref,
            "judge_passed_raw": passed_raw,
            "score_judge_mismatch": score_judge_mismatch,
        }
        new_results.append(
            VerifierResult(
                name=result_name,
                passed=passed,
                score=score,
                detail=detail,
            )
        )
        if not passed:
            fb_detail = dict(detail)
            fb_detail["score"] = score
            new_feedback.append(
                FeedbackRecord(
                    source="verifier.semantic_rubric",
                    message=(
                        f"semantic check {check.name!r} failed: "
                        f"{comment or 'score below min_score'}"
                    ),
                    target_node_key=node_key,
                    severity="error",
                    suggested_action=DecisionAction.REVISE,
                    detail=fb_detail,
                )
            )
    return new_results, new_feedback


def semantic_items_by_name(
    *,
    payload: dict[str, Any],
    requested_names: set[str],
) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for key in ("checks", "results", "rubric_results"):
        semantic_collect_items(
            payload.get(key),
            by_name=by_name,
            requested_names=requested_names,
        )
    semantic_collect_items(
        payload,
        by_name=by_name,
        requested_names=requested_names,
    )
    return by_name


def semantic_collect_items(
    raw: Any,
    *,
    by_name: dict[str, dict[str, Any]],
    requested_names: set[str],
) -> None:
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name in requested_names:
                by_name.setdefault(name, item)
        return
    if isinstance(raw, dict):
        for name in requested_names:
            item = raw.get(name)
            if isinstance(item, dict):
                normalized = dict(item)
                normalized["name"] = name
                by_name.setdefault(name, normalized)


def semantic_coerce_score(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\s*(.*?)\s*```", re.DOTALL)


def semantic_strip_code_fences(content: str) -> str:
    match = CODE_FENCE_RE.search(content)
    if match is not None:
        return match.group(1)
    return content


def semantic_json_payload(content: str) -> dict[str, Any]:
    try:
        return semantic_strict_json_object(content)
    except ValueError:
        return semantic_strict_json_object(semantic_strip_code_fences(content))


def semantic_strict_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ValueError("semantic verifier returned empty content")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        embedded = semantic_embedded_json_object(text)
        if embedded is not None:
            return embedded
        raise ValueError(
            f"semantic verifier returned non-JSON content: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("semantic verifier response must be a JSON object")
    return payload


def semantic_embedded_json_object(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def truncate_semantic_response(content: str) -> str:
    if len(content) <= SEMANTIC_REPAIR_MAX_CHARS:
        return content
    return content[:SEMANTIC_REPAIR_MAX_CHARS] + "\n...[truncated]"
