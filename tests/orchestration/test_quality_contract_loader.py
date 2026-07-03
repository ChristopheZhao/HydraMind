"""Tests for ``load_goal_quality_contract``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydramind.control import GoalArtifactQualityContract, SemanticRubric
from hydramind.orchestration import load_goal_quality_contract


def test_load_quality_contract_returns_defaults_for_empty_object(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text("{}", encoding="utf-8")

    contract = load_goal_quality_contract(contract_path)

    assert isinstance(contract, GoalArtifactQualityContract)
    assert contract == GoalArtifactQualityContract()
    assert contract.semantic_rubric is None


def test_load_quality_contract_parses_full_payload_with_rubric(tmp_path: Path) -> None:
    payload = {
        "min_length": 200,
        "required_sections": ["# Intro", "## Conclusion"],
        "min_reference_urls": 3,
        "min_image_refs": 2,
        "local_asset_refs_under_artifact_root": True,
        "semantic_rubric": {
            "enabled": True,
            "checks": [
                {
                    "name": "technical_depth",
                    "description": "covers system internals",
                    "min_score": 0.7,
                }
            ],
        },
    }
    contract_path = tmp_path / "full.json"
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    contract = load_goal_quality_contract(contract_path)

    assert contract.min_length == 200
    assert contract.required_sections == ("# Intro", "## Conclusion")
    assert contract.min_reference_urls == 3
    assert contract.min_image_refs == 2
    assert contract.local_asset_refs_under_artifact_root is True
    assert isinstance(contract.semantic_rubric, SemanticRubric)
    assert contract.semantic_rubric.enabled is True
    assert len(contract.semantic_rubric.checks) == 1
    check = contract.semantic_rubric.checks[0]
    assert check.name == "technical_depth"
    assert check.min_score == 0.7


def test_load_quality_contract_missing_file_raises_value_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"

    with pytest.raises(ValueError) as exc_info:
        load_goal_quality_contract(missing)

    assert "not found" in str(exc_info.value)


def test_load_quality_contract_rejects_non_object_root(tmp_path: Path) -> None:
    for payload in ("123", "[]", '"oops"', "null"):
        contract_path = tmp_path / "bad.json"
        contract_path.write_text(payload, encoding="utf-8")

        with pytest.raises(ValueError) as exc_info:
            load_goal_quality_contract(contract_path)

        assert "must be a JSON object" in str(exc_info.value)


def test_load_quality_contract_rejects_malformed_json(tmp_path: Path) -> None:
    contract_path = tmp_path / "broken.json"
    contract_path.write_text("{not json}", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_goal_quality_contract(contract_path)

    assert "not valid JSON" in str(exc_info.value)


def test_load_quality_contract_wraps_pydantic_validation_error(tmp_path: Path) -> None:
    contract_path = tmp_path / "invalid.json"
    contract_path.write_text(json.dumps({"min_length": "ten"}), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_goal_quality_contract(contract_path)

    assert "validation failed" in str(exc_info.value)
