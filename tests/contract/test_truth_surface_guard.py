from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_guard() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_truth_surface.py"
    spec = importlib.util.spec_from_file_location("check_truth_surface", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_minimal_truth_surfaces(root: Path, *, readme: str) -> None:
    (root / "README.md").write_text(readme, encoding="utf-8")
    architecture = root / "docs" / "architecture"
    architecture.mkdir(parents=True)
    (architecture / "10-harness-backend.md").write_text(
        "# 10 - HarnessBackend Tombstone\n\n"
        "`HarnessBackend` was the old boundary and is retired.\n",
        encoding="utf-8",
    )
    (architecture / "10-execution-harness.md").write_text(
        "# 10 - ExecutionHarness\n\n"
        "`ModelProvider` owns provider access. `ExecutionHarness` is replaceable.\n",
        encoding="utf-8",
    )
    (architecture / "90-decisions.md").write_text(
        "# Decisions\n\n"
        "## ADR-0011 - HarnessBackend retired; ExecutionHarness is active\n",
        encoding="utf-8",
    )


def test_truth_surface_guard_allows_current_provider_harness_split(tmp_path: Path) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme=(
            "`ModelProvider` owns provider calls. `ExecutionHarness` owns the "
            "replaceable execution shell. `--provider mock` is Class 3 replay, "
            "not live-agent or live-MAS acceptance. Provider switching is routing, "
            "not harness replacement."
        ),
    )

    assert module.find_violations(tmp_path) == []


def test_truth_surface_guard_rejects_active_harnessbackend_claim(tmp_path: Path) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme="All model calls go through `HarnessBackend`.",
    )

    labels = {violation.label for violation in module.find_violations(tmp_path)}
    assert "active HarnessBackend claim" in labels


def test_truth_surface_guard_rejects_backend_mock_usage(tmp_path: Path) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme="Run `hydramind run workflow.yaml --backend mock` for local acceptance.",
    )

    labels = {violation.label for violation in module.find_violations(tmp_path)}
    assert "stale --backend mock usage" in labels


def test_truth_surface_guard_rejects_mock_replay_as_live_acceptance(tmp_path: Path) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme="Mock replay is live-MAS acceptance for Class 5.",
    )

    labels = {violation.label for violation in module.find_violations(tmp_path)}
    assert "mock/replay described as live acceptance" in labels


def test_truth_surface_guard_rejects_only_qualified_mock_live_claim(tmp_path: Path) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme="Mock replay is live-MAS acceptance only.",
    )

    labels = {violation.label for violation in module.find_violations(tmp_path)}
    assert "mock/replay described as live acceptance" in labels


def test_truth_surface_guard_rejects_provider_switching_as_harness_replacement(
    tmp_path: Path,
) -> None:
    module = _load_guard()
    _write_minimal_truth_surfaces(
        tmp_path,
        readme="Provider switching is harness replacement.",
    )

    labels = {violation.label for violation in module.find_violations(tmp_path)}
    assert "provider switching described as harness replacement" in labels
