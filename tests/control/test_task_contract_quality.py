"""TaskContract quality_contract field and round-trip tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hydramind.control import GoalArtifactQualityContract, TaskContract


def test_task_contract_default_quality_contract_is_none() -> None:
    contract = TaskContract()
    assert contract.quality_contract is None


def test_task_contract_is_frozen() -> None:
    contract = TaskContract()
    with pytest.raises(ValidationError):
        contract.quality_contract = GoalArtifactQualityContract(min_length=10)  # type: ignore[misc]


def test_quality_contract_round_trip_is_lossless() -> None:
    original = GoalArtifactQualityContract(
        min_length=2000,
        required_sections=("引言", "参考文献"),
        min_reference_urls=5,
        min_image_refs=3,
    )
    payload = original.model_dump(mode="json")
    restored = GoalArtifactQualityContract.model_validate(payload)
    assert restored == original
    assert restored.min_length == 2000
    assert restored.required_sections == ("引言", "参考文献")
    assert restored.min_reference_urls == 5
    assert restored.min_image_refs == 3
    assert restored.local_asset_refs_under_artifact_root is True


def test_task_contract_round_trips_with_quality_contract() -> None:
    quality = GoalArtifactQualityContract(
        min_length=500,
        required_sections=("## 引言",),
        min_reference_urls=2,
        min_image_refs=1,
        local_asset_refs_under_artifact_root=False,
    )
    original = TaskContract(
        objective="write a blog",
        expected_artifacts=("blog.md",),
        quality_contract=quality,
    )
    payload = original.model_dump(mode="json")
    restored = TaskContract.model_validate(payload)
    assert restored == original
    assert restored.quality_contract == quality
    assert restored.quality_contract is not None
    assert restored.quality_contract.local_asset_refs_under_artifact_root is False
