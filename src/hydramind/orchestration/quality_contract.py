"""JSON loader for ``GoalArtifactQualityContract``.

Used by the CLI (``hydramind goal --quality-contract <path.json>``) and by
runtime helpers that want to validate a contract file before forwarding it
to :func:`hydramind.runtime.run_goal` / ``create_queued_goal_session``.

The loader keeps a narrow surface: read the file as UTF-8, parse strict JSON
into an object, and validate via :class:`GoalArtifactQualityContract`. Every
failure path raises :class:`ValueError` with a concrete message so CLI code
can print a clean ``reason=quality_contract_invalid`` payload without leaking
pydantic exception types.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hydramind.control import GoalArtifactQualityContract


def load_goal_quality_contract(path: str | Path) -> GoalArtifactQualityContract:
    """Load and validate a goal artifact quality contract from JSON.

    Args:
        path: Filesystem path to a UTF-8 JSON file describing a contract using
            the same shape as ``GoalArtifactQualityContract.model_dump(mode="json")``.

    Returns:
        The validated :class:`GoalArtifactQualityContract` instance.

    Raises:
        ValueError: When the file is missing, the JSON is malformed, the
            top-level value is not a JSON object, or the payload fails
            pydantic validation. The exception message is human-readable and
            safe to surface in CLI output.
    """

    contract_path = Path(path)
    try:
        raw = contract_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(
            f"quality contract file not found: {contract_path}"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"quality contract file not readable: {contract_path}: {exc}"
        ) from exc
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"quality contract file is not valid JSON: {contract_path}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "quality contract must be a JSON object at the top level"
        )
    try:
        return GoalArtifactQualityContract.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"quality contract validation failed: {exc.errors(include_url=False)}"
        ) from exc
