"""Runtime verifier stack assembly tests."""

from __future__ import annotations

from hydramind.orchestration import (
    ArtifactContainmentVerifierRunner,
    CompositeVerifierRunner,
    SemanticArtifactVerifierRunner,
    TaskContractVerifierRunner,
)
from hydramind.runtime_verification import build_default_goal_verifier_runner
from hydramind.testing import MockProvider


def test_default_goal_verifier_runner_order_is_safety_then_semantic(tmp_path) -> None:
    runner = build_default_goal_verifier_runner(
        artifact_root=tmp_path,
        provider=MockProvider(),
    )

    assert isinstance(runner, CompositeVerifierRunner)
    assert [type(item) for item in runner.runners] == [
        TaskContractVerifierRunner,
        ArtifactContainmentVerifierRunner,
        SemanticArtifactVerifierRunner,
    ]
    assert all(
        type(item).__name__ != "ContentQualityVerifierRunner"
        for item in runner.runners
    )
