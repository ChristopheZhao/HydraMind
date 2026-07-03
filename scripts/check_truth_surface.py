#!/usr/bin/env python3
"""Fail if stale harness/acceptance claims reappear on active truth surfaces.

N5 guard (PLAN-20260619-001): active public/operator docs must describe the
current split:

- provider/model access is ``ModelProvider``;
- the replaceable execution shell is ``ExecutionHarness``;
- local mock/replay is Class 1-3 evidence, never live Class 4/5 acceptance;
- provider switching is not harness replacement.

Historical correction docs and ADR bodies may keep old terms as history. Active
truth surfaces may mention the retired boundary only as retired/tombstoned.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

ACTIVE_TARGETS = (
    "README.md",
    "examples",
    "scripts",
    "docs/operations",
    "docs/architecture/00-overview.md",
    "docs/architecture/10-harness-backend.md",
    "docs/architecture/10-execution-harness.md",
    "docs/architecture/40-orchestration.md",
    "docs/architecture/60-queue-adapter.md",
    "docs/architecture/70-production-runtime.md",
)
REQUIRED_FILES = (
    "docs/architecture/10-harness-backend.md",
    "docs/architecture/10-execution-harness.md",
    "docs/architecture/90-decisions.md",
)
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".sh", ".json", ".txt", ".cfg"}

_RETIRED_HARNESS_MARKERS = (
    "retired",
    "tombstone",
    "historical",
    "history",
    "old ",
    "previous",
    "superseded",
    "correction",
    "not active",
    "not an active",
    "no longer",
)
_NEGATION_MARKERS = (
    "not",
    " not ",
    "not-",
    "never",
    "cannot",
    "can never",
    "must not",
    "can't",
    "no live",
    "not live",
    "not-proven",
    "separate",
    "contract / plumbing / replay",
)


@dataclass(frozen=True)
class Violation:
    label: str
    path: Path
    lineno: int
    line: str

    def render(self, root: Path) -> str:
        try:
            rel_path = self.path.relative_to(root)
        except ValueError:
            rel_path = self.path
        return f"[{self.label}] {rel_path}:{self.lineno}: {self.line.strip()}"


def _iter_files(target: Path) -> Iterator[Path]:
    if target.is_file():
        if target.resolve() != SELF:
            yield target
        return
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and path.resolve() != SELF:
            yield path


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lower = f" {text.lower()} "
    return any(marker in lower for marker in markers)


def _mentions_active_harnessbackend(line: str) -> bool:
    if "HarnessBackend" not in line:
        return False
    return not _has_any(line, _RETIRED_HARNESS_MARKERS)


def _mentions_backend_mock(line: str) -> bool:
    return "--backend mock" in line


def _calls_mock_or_replay_live(line: str) -> bool:
    lower = f" {line.lower()} "
    has_offline_source = "mock" in lower or "replay" in lower
    has_live_label = any(
        marker in lower
        for marker in (
            "live-agent",
            "live agent",
            "live-mas",
            "live mas",
            "live acceptance",
            "class 4",
            "class 5",
        )
    )
    return has_offline_source and has_live_label and not _has_any(line, _NEGATION_MARKERS)


def _calls_provider_switching_harness_replacement(line: str) -> bool:
    lower = f" {line.lower()} "
    has_provider_switch = (
        "provider switching" in lower
        or "provider selection" in lower
        or "switching deepseek" in lower
    )
    has_harness_replacement = "harness replacement" in lower
    if not (has_provider_switch and has_harness_replacement):
        return False
    return not (
        "≠" in line
        or "not" in lower
        or "distinct" in lower
        or "routing" in lower
        or "separate" in lower
    )


def _line_violations(path: Path, lineno: int, line: str) -> Iterator[Violation]:
    if _mentions_active_harnessbackend(line):
        yield Violation("active HarnessBackend claim", path, lineno, line)
    if _mentions_backend_mock(line):
        yield Violation("stale --backend mock usage", path, lineno, line)
    if _calls_mock_or_replay_live(line):
        yield Violation("mock/replay described as live acceptance", path, lineno, line)
    if _calls_provider_switching_harness_replacement(line):
        yield Violation("provider switching described as harness replacement", path, lineno, line)


def find_violations(root: Path = ROOT) -> list[Violation]:
    violations: list[Violation] = []
    for rel in REQUIRED_FILES:
        path = root / rel
        if not path.exists():
            violations.append(Violation("missing required truth surface", path, 0, "missing"))

    decisions = root / "docs/architecture/90-decisions.md"
    if decisions.exists():
        text = decisions.read_text(encoding="utf-8")
        if "ADR-0011" not in text or "HarnessBackend retired" not in text:
            violations.append(
                Violation(
                    "missing HarnessBackend retirement ADR",
                    decisions,
                    0,
                    "ADR-0011 must record HarnessBackend retired",
                )
            )

    for rel in ACTIVE_TARGETS:
        base = root / rel
        if not base.exists():
            continue
        for path in _iter_files(base):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                violations.extend(_line_violations(path, lineno, line))
    return violations


def main() -> int:
    violations = find_violations(ROOT)
    if violations:
        print("N5 truth-surface guard FAILED — stale claims on active surfaces:")
        for item in violations:
            print(f"  {item.render(ROOT)}")
        return 1
    print("N5 truth-surface guard OK — active surfaces match provider/harness split.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
