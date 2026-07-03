"""Public API snapshot tests for intentional package exports."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).with_name("public_api_snapshot.json")


def test_public_api_exports_match_snapshot() -> None:
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    violations: list[str] = []

    for module_name, expected_exports in snapshot.items():
        module = importlib.import_module(module_name)
        actual_exports = getattr(module, "__all__", None)
        if actual_exports is None:
            violations.append(f"{module_name}: missing __all__")
            continue
        if list(actual_exports) != expected_exports:
            violations.append(
                f"{module_name}: expected {expected_exports!r}, "
                f"got {list(actual_exports)!r}"
            )
        for name in expected_exports:
            if not hasattr(module, name):
                violations.append(f"{module_name}: __all__ includes missing {name!r}")

    assert violations == []
