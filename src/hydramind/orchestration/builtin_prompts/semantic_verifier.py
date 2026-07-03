"""Built-in prompt template for the opt-in semantic rubric verifier.

Architectural invariant: role prompts must NOT be hardcoded outside
``src/hydramind/orchestration/builtin_prompts/``. The semantic verifier
runner imports its prompt from this module instead of inlining strings in
``verification.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

SEMANTIC_VERIFIER_SYSTEM = (
    "You are an opt-in JSON-only rubric judge. "
    "Respond with strict JSON; no commentary outside JSON."
)

SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM = (
    "You repair rubric judge output into strict JSON. "
    "Respond with one JSON object only; no commentary outside JSON."
)


_RESPONSE_SHAPE: dict[str, Any] = {
    "checks": [
        {
            "name": "string (matches a requested check name)",
            "score": "number in [0.0, 1.0]",
            "passed": "boolean",
            "comment": "string (short evidence; <= 200 chars)",
        }
    ],
    "overall_summary": "string",
}


def render_semantic_verifier_prompt(
    *,
    artifact_ref: str,
    artifact_text: str,
    checks: Sequence[dict[str, Any]],
) -> str:
    """Render the user prompt for the semantic verifier.

    ``checks`` is a list of ``{"name", "description", "min_score"}`` dicts
    declared by the task ``SemanticRubric``. ``artifact_text`` is already
    bounded by the runner (with a ``"… [truncated]"`` marker if needed).
    """
    payload: dict[str, Any] = {
        "request": "semantic_rubric_judge",
        "instruction": (
            "Grade the artifact against each rubric check. "
            "Score each in [0.0, 1.0]; mark passed=true iff score >= min_score. "
            "Return strict JSON matching required_response_shape; "
            "include exactly one entry per requested check."
        ),
        "artifact": {
            "ref": artifact_ref,
            "text": artifact_text,
        },
        "rubric_checks": list(checks),
        "required_response_shape": _RESPONSE_SHAPE,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def render_semantic_verifier_json_repair_prompt(
    *,
    invalid_response: str,
    validation_error: str,
    checks: Sequence[dict[str, Any]],
) -> str:
    """Render a strict JSON repair request for malformed judge output."""
    payload: dict[str, Any] = {
        "request": "semantic_rubric_json_repair",
        "instruction": (
            "Convert the invalid semantic rubric judge response into one "
            "valid JSON object matching required_response_shape. Do not add "
            "commentary outside JSON. Include exactly one entry per requested "
            "check. If the invalid response does not contain enough evidence "
            "for a requested check, emit score=0.0, passed=false, and a short "
            "comment explaining the missing assessment."
        ),
        "rubric_checks": list(checks),
        "required_response_shape": _RESPONSE_SHAPE,
        "validation_error": validation_error,
        "invalid_response": invalid_response,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM",
    "SEMANTIC_VERIFIER_SYSTEM",
    "render_semantic_verifier_json_repair_prompt",
    "render_semantic_verifier_prompt",
]
