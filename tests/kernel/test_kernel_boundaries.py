"""S91 boundary: the kernel package stays pure (no control/harness/runtime/SDK)."""

from __future__ import annotations

import ast
from pathlib import Path

KERNEL_DIR = Path(__file__).resolve().parents[2] / "src" / "hydramind" / "kernel"

FORBIDDEN_PREFIXES = (
    "hydramind.control",
    "hydramind.harness",
    "hydramind.runtime",
    "hydramind.orchestration",
    "hydramind.queue",
    "anthropic",
    "openai",
    "claude_agent_sdk",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_kernel_package_has_no_forbidden_imports() -> None:
    violations: list[str] = []
    for path in sorted(KERNEL_DIR.glob("*.py")):
        for module in _imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name}: imports {module}")
    assert violations == []


def test_kernel_is_wired_into_team_execution() -> None:
    # S94 wires the kernel (DEV-26): native team execution drives turns through
    # the kernel scheduler, so the kernel is no longer dead. The wiring now lives
    # in the orchestration interaction-runtime boundary rather than the executor.
    src = KERNEL_DIR.parent
    interaction_runtime = src / "orchestration" / "collaboration_runtime.py"
    imported = _imported_modules(interaction_runtime)
    assert any(
        module == "hydramind.kernel" or module.startswith("hydramind.kernel.")
        for module in imported
    ), "Native team execution must drive turns through hydramind.kernel (DEV-26)"
