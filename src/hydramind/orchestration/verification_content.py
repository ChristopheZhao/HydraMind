"""Deterministic artifact and content verification helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hydramind.control import (
    DecisionAction,
    FeedbackRecord,
    TaskContract,
    VerifierResult,
)

URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def contract_from_config(node_config: dict[str, Any]) -> TaskContract | None:
    raw = node_config.get("task_contract")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return TaskContract(
            metadata={
                "_invalid_task_contract": True,
                "error": "task_contract must be an object",
            }
        )
    try:
        return TaskContract.model_validate(raw)
    except ValidationError as exc:
        return TaskContract(
            metadata={
                "_invalid_task_contract": True,
                "error": exc.errors(include_url=False),
            }
        )


def verify_expected_artifacts(
    artifact_root: Path,
    contract: TaskContract,
) -> VerifierResult:
    present: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    for raw_path in contract.expected_artifacts:
        resolved = artifact_path(artifact_root, raw_path)
        if resolved is None:
            invalid.append(raw_path)
        elif resolved.exists():
            present.append(raw_path)
        else:
            missing.append(raw_path)
    passed = not missing and not invalid
    repair_targets = missing + invalid
    return VerifierResult(
        name="artifact.exists",
        passed=passed,
        evidence_refs=tuple(present),
        detail={
            "artifact_root": str(artifact_root),
            "expected_artifacts": list(contract.expected_artifacts),
            "present": present,
            "missing": missing,
            "invalid": invalid,
        },
        repair_instruction=(
            f"Create expected artifact(s): {', '.join(repair_targets)}"
            if repair_targets
            else None
        ),
    )


def artifact_path(artifact_root: Path, raw_path: str) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None
    root = artifact_root.resolve(strict=False)
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def markdown_directory(artifact_root: Path, artifact_ref: str | None) -> Path:
    if not artifact_ref:
        return artifact_root.resolve(strict=False)
    candidate = Path(artifact_ref)
    if candidate.is_absolute():
        return artifact_root.resolve(strict=False)
    root = artifact_root.resolve(strict=False)
    resolved = (root / candidate).resolve(strict=False)
    return resolved.parent if resolved.suffix else resolved


def markdown_relative_artifact_path(
    artifact_root: Path,
    markdown_dir: Path,
    raw_path: str,
) -> Path | None:
    """Resolve a markdown-file-relative asset path under ``artifact_root``."""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None
    root = artifact_root.resolve(strict=False)
    resolved = (markdown_dir / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def artifact_feedback(node_key: str, result: VerifierResult) -> FeedbackRecord:
    missing = result.detail.get("missing")
    invalid = result.detail.get("invalid")
    missing_items = missing if isinstance(missing, list) else []
    invalid_items = invalid if isinstance(invalid, list) else []
    return FeedbackRecord(
        source="verifier.artifact_exists",
        message=feedback_message(missing_items, invalid_items),
        target_node_key=node_key,
        severity="error",
        suggested_action=DecisionAction.REVISE,
        evidence_refs=tuple(str(item) for item in missing_items + invalid_items),
        detail=result.detail,
    )


def feedback_message(missing: list[Any], invalid: list[Any]) -> str:
    parts: list[str] = []
    if missing:
        parts.append(f"missing expected artifact(s): {', '.join(map(str, missing))}")
    if invalid:
        parts.append(f"invalid expected artifact path(s): {', '.join(map(str, invalid))}")
    return "; ".join(parts) or "expected artifact verification failed"


def locate_existing_artifact(
    artifact_root: Path,
    expected_artifacts: tuple[str, ...],
) -> tuple[str | None, Path | None]:
    for raw in expected_artifacts:
        resolved = artifact_path(artifact_root, raw)
        if resolved is None:
            continue
        if resolved.exists():
            return raw, resolved
    return None, None


def content_feedback(
    *,
    node_key: str,
    name: str,
    message: str,
    detail: dict[str, Any],
) -> FeedbackRecord:
    return FeedbackRecord(
        source="verifier.content_quality",
        message=f"{name}: {message}",
        target_node_key=node_key,
        severity="error",
        suggested_action=DecisionAction.REVISE,
        detail=detail,
    )


def extract_image_targets(text: str) -> list[str]:
    return [match.group(1).strip() for match in IMAGE_REF_RE.finditer(text)]


def check_local_assets(
    image_targets: list[str],
    artifact_root: Path,
    artifact_ref: str | None,
    feedback: list[FeedbackRecord],
    node_key: str,
) -> VerifierResult:
    present: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    markdown_dir = markdown_directory(artifact_root, artifact_ref)
    for target in image_targets:
        if URL_RE.match(target):
            continue
        resolved = markdown_relative_artifact_path(
            artifact_root, markdown_dir, target
        )
        if resolved is None:
            invalid.append(target)
        elif resolved.exists():
            present.append(target)
        else:
            missing.append(target)
    passed = not missing and not invalid
    detail: dict[str, Any] = {
        "present": present,
        "missing": missing,
        "invalid": invalid,
        "artifact": artifact_ref or "",
    }
    result = VerifierResult(
        name="artifact.local_assets_contained", passed=passed, detail=detail
    )
    if not passed:
        parts: list[str] = []
        if missing:
            parts.append(f"missing local asset(s): {', '.join(missing)}")
        if invalid:
            parts.append(f"invalid local asset path(s): {', '.join(invalid)}")
        feedback.append(
            content_feedback(
                node_key=node_key,
                name="artifact.local_assets_contained",
                message="; ".join(parts) or "local asset verification failed",
                detail=detail,
            )
        )
    return result
