"""S97 artifact-root containment safety verifier + default stack assembly.

These tests prove the surviving boundary safety carve-out after the rule-based
content-quality verifier was deleted (ADR-0008):

  1. ``ArtifactContainmentVerifierRunner`` STILL rejects a local image-asset
     path that escapes ``artifact_root`` (the containment carve-out).
  2. It does NOT enforce any quality threshold (length / sections /
     reference-url / image counts) — only containment.
  3. The default goal verifier stack is safety-first then the agent semantic
     judge: ``TaskContractVerifierRunner`` + ``ArtifactContainmentVerifierRunner``
     + ``SemanticArtifactVerifierRunner``, with NO rule content-quality runner.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hydramind.control import (
    AgentReport,
    GoalArtifactQualityContract,
    RuntimeSession,
    TaskContract,
)
from hydramind.orchestration import (
    ArtifactContainmentVerifierRunner,
    CompositeVerifierRunner,
    SemanticArtifactVerifierRunner,
    TaskContractVerifierRunner,
)
from hydramind.runtime_verification import build_default_goal_verifier_runner
from hydramind.testing import MockProvider


def _session() -> RuntimeSession:
    return RuntimeSession(workflow_name="containment")


def _report() -> AgentReport:
    return AgentReport(node_key="write", agent_id="writer")


def _node_config(contract: TaskContract) -> dict[str, object]:
    return {"task_contract": contract.model_dump(mode="json")}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_containment_rejects_path_escaping_local_asset(tmp_path: Path) -> None:
    # A local asset path that escapes artifact_root must STILL be rejected by
    # the surviving safety verifier (boundary carve-out preserved).
    (tmp_path / "blog").mkdir()
    _write(tmp_path / "blog" / "post.md", "![escape](../../outside.png)")
    contract = TaskContract(
        expected_artifacts=("blog/post.md",),
        quality_contract=GoalArtifactQualityContract(),
    )
    runner = ArtifactContainmentVerifierRunner(tmp_path)

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    contained = next(
        r
        for r in verified.verifier_results
        if r.name == "artifact.local_assets_contained"
    )
    assert contained.passed is False
    assert contained.detail["invalid"] == ["../../outside.png"]
    assert verified.feedback[0].source == "verifier.content_quality"


def test_containment_rejects_absolute_local_asset(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "![passwd](/etc/passwd)")
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(),
    )
    runner = ArtifactContainmentVerifierRunner(tmp_path)

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    contained = next(
        r
        for r in verified.verifier_results
        if r.name == "artifact.local_assets_contained"
    )
    assert contained.passed is False
    assert contained.detail["invalid"] == ["/etc/passwd"]


def test_containment_passes_for_contained_local_asset(tmp_path: Path) -> None:
    _write(tmp_path / "pic.png", "binary")
    _write(tmp_path / "blog.md", "intro ![alt](pic.png) end")
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(),
    )
    runner = ArtifactContainmentVerifierRunner(tmp_path)

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    contained = next(
        r
        for r in verified.verifier_results
        if r.name == "artifact.local_assets_contained"
    )
    assert contained.passed is True
    assert contained.detail["present"] == ["pic.png"]
    assert verified.feedback == ()


def test_containment_does_not_enforce_quality_thresholds(tmp_path: Path) -> None:
    # A short artifact with no images and many required-section/length contract
    # fields must produce NO quality results — containment runs only on local
    # image refs.
    _write(tmp_path / "blog.md", "too short, no headings, no links")
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            min_length=10_000,
            required_sections=("# Intro", "# Summary"),
            min_reference_urls=5,
            min_image_refs=3,
        ),
    )
    runner = ArtifactContainmentVerifierRunner(tmp_path)

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    # No image references in the artifact -> containment is a no-op.
    assert verified.verifier_results == ()
    assert verified.feedback == ()


def test_containment_disabled_when_flag_off(tmp_path: Path) -> None:
    _write(tmp_path / "blog.md", "![escape](../../outside.png)")
    contract = TaskContract(
        expected_artifacts=("blog.md",),
        quality_contract=GoalArtifactQualityContract(
            local_asset_refs_under_artifact_root=False,
        ),
    )
    runner = ArtifactContainmentVerifierRunner(tmp_path)

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    assert verified.verifier_results == ()
    assert verified.feedback == ()


def test_default_goal_verifier_stack_is_safety_then_semantic(tmp_path: Path) -> None:
    runner = build_default_goal_verifier_runner(
        artifact_root=tmp_path,
        provider=MockProvider(),
    )

    assert isinstance(runner, CompositeVerifierRunner)
    types = [type(r) for r in runner.runners]
    assert types == [
        TaskContractVerifierRunner,
        ArtifactContainmentVerifierRunner,
        SemanticArtifactVerifierRunner,
    ]
    # No rule content-quality runner re-accumulated in the default stack.
    assert all(
        type(r).__name__ != "ContentQualityVerifierRunner" for r in runner.runners
    )


def test_default_stack_containment_rejects_escape_end_to_end(tmp_path: Path) -> None:
    # The whole assembled default stack must still reject a path-escaping asset.
    _write(tmp_path / "report.md", "![escape](../../secret.png)")
    contract = TaskContract(
        expected_artifacts=("report.md",),
        quality_contract=GoalArtifactQualityContract(),
    )
    runner = build_default_goal_verifier_runner(
        artifact_root=tmp_path,
        provider=MockProvider(),
    )

    verified = asyncio.run(
        runner.verify(
            session=_session(),
            node_config=_node_config(contract),
            report=_report(),
        )
    )

    contained = next(
        r
        for r in verified.verifier_results
        if r.name == "artifact.local_assets_contained"
    )
    assert contained.passed is False
    assert contained.detail["invalid"] == ["../../secret.png"]
