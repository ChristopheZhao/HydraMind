"""Collect a redaction-safe evidence package for the MAS production blog scenario.

Usage:

    python examples/scenarios/mas-production-blog/evidence_collector.py \
        --session-store sqlite --store-path var/blog.sqlite \
        --session-id <session-id> \
        --artifact-root artifacts/scenarios/mas-production-blog/<run-id>/ \
        --trace-path artifacts/scenarios/mas-production-blog/<run-id>/trace.jsonl \
        --output-dir artifacts/scenarios/mas-production-blog/<run-id>/evidence/

This script is an *example* helper. It deliberately:

- Lives under ``examples/scenarios/`` (NOT under ``src/hydramind/``); it is not
  framework core.
- Imports only stdlib + ``hydramind.control`` typed models. It must not import
  any provider SDK or ``hydramind.harness`` symbols.
- Reads ``RuntimeSession`` via the standard session-store + service surface; it
  does not mutate session state.
- Writes only ``redaction-safe`` outputs and asserts that no secret-shaped
  field leaked into the produced JSON files.

The collector is also importable so tests under ``tests/scenarios/`` can drive
its building blocks directly without a CLI subprocess.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hydramind.control import (
    InMemorySessionStore,
    NodeAttempt,
    RuntimeSession,
    SessionService,
    SessionStore,
    SqliteSessionStore,
    ToolExecution,
)

# Keys (case-insensitive substring match) that must never appear in evidence JSON.
SECRET_KEY_FRAGMENTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)

# Field names whose VALUES are also stripped to a typed shape rather than copied
# verbatim. ``content`` here means raw tool result content (which can contain
# unredacted provider payloads); we only keep its shape metadata.
RAW_VALUE_KEY_FRAGMENTS: tuple[str, ...] = ("content",)

# Markdown image syntax: ``![alt](path "title")``. Title is optional.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------


def build_session_store(kind: str, store_path: str | None) -> SessionStore:
    """Construct the session store used to read the goal RuntimeSession.

    Only ``memory`` and ``sqlite`` are supported; ``sqlite`` requires a
    ``store_path``. ``memory`` is only useful in unit tests since a separate
    process cannot share an in-memory store.
    """

    normalized = kind.lower().strip()
    if normalized == "memory":
        return InMemorySessionStore()
    if normalized == "sqlite":
        if not store_path:
            raise ValueError("--store-path is required when --session-store=sqlite")
        return SqliteSessionStore(store_path)
    raise ValueError(f"unsupported session store kind: {kind!r}")


async def load_session(store: SessionStore, session_id: str) -> RuntimeSession:
    """Fetch a session via ``SessionService.get_session`` (read-only path)."""

    service = SessionService(store)
    session = await service.get_session(session_id)
    if session is None:
        raise SystemExit(f"session id not found in store: {session_id}")
    return session


# ---------------------------------------------------------------------------
# Redaction guard
# ---------------------------------------------------------------------------


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS)


def _is_raw_value_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in RAW_VALUE_KEY_FRAGMENTS)


def assert_no_secrets(label: str, value: Any) -> None:
    """Walk a JSON-like structure and raise ``SystemExit(2)`` on any secret leak.

    A leak is defined as either:

    - any dict key whose lower-case form contains one of
      ``SECRET_KEY_FRAGMENTS`` and whose value is not the literal redaction
      marker ``"<redacted>"``;
    - any dict key whose lower-case form contains one of
      ``RAW_VALUE_KEY_FRAGMENTS`` and whose value is not the literal
      ``"<redacted>"`` *and* is longer than 0 chars (means raw content was
      copied through).
    """

    def _fail(here: str, kind: str) -> None:
        sys.stderr.write(
            f"redaction guard failed in {label}: {kind} key {here!r} leaked "
            "non-redacted value\n"
        )
        raise SystemExit(2)

    def _scan(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                key_str = str(key)
                here = f"{path}.{key_str}" if path else key_str
                if _is_secret_key(key_str) and item not in (
                    "<redacted>",
                    None,
                    "",
                ):
                    _fail(here, "secret-shaped")
                if (
                    _is_raw_value_key(key_str)
                    and isinstance(item, str)
                    and item not in ("<redacted>", "")
                ):
                    _fail(here, "raw-content")
                _scan(item, here)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _scan(item, f"{path}[{index}]")

    _scan(value, "")


# ---------------------------------------------------------------------------
# Ledger / verifier / planner extractors
# ---------------------------------------------------------------------------


def collect_ledger(session: RuntimeSession) -> list[dict[str, Any]]:
    """Return one shape-only ledger entry per ``ToolExecution`` in the session.

    Only redaction-safe metadata is included: status, tool name, error,
    content_length, is_error, started_at, finished_at, redaction-safe metadata.
    Arguments and result_preview are intentionally dropped (they may still
    contain provider-side payload fragments even after framework redaction).
    """

    entries: list[dict[str, Any]] = []
    for node_key in sorted(session.nodes):
        node = session.nodes[node_key]
        for attempt in node.attempts:
            for tool in attempt.tool_executions:
                entries.append(_ledger_entry(node_key, attempt, tool))
    return entries


def _ledger_entry(
    node_key: str,
    attempt: NodeAttempt,
    tool: ToolExecution,
) -> dict[str, Any]:
    return {
        "node_key": node_key,
        "execution_id": attempt.id,
        "trace_id": tool.trace_id,
        "tool_call_id": tool.tool_call_id,
        "tool_name": tool.tool_name,
        "round_no": tool.round_no,
        "status": tool.status.value,
        "is_error": tool.is_error,
        "content_length": tool.content_length,
        "error": tool.error,
        "started_at": tool.started_at.isoformat() if tool.started_at else None,
        "finished_at": tool.finished_at.isoformat() if tool.finished_at else None,
        "metadata_keys": sorted(str(k) for k in tool.metadata.keys()),
    }


def collect_verifier_results(session: RuntimeSession) -> dict[str, Any]:
    """Walk the session for VerifierResult / FeedbackRecord persisted shapes.

    Authoritative locations (in priority order):

    1. ``node.attempts[*].output["_verifier_results"]`` —
       :meth:`AgentReport.persisted_output` persists verifier results onto the
       node attempt output.
    2. ``node.attempts[*].output["_feedback"]`` — same path for typed
       :class:`FeedbackRecord` entries.
    3. ``node.gates[*].detail["verifier_results"]`` — fallback shape recorded
       by :class:`VerifierFeedbackEvaluator` on failing gates.
    """

    per_node: dict[str, dict[str, Any]] = {}
    for node_key in sorted(session.nodes):
        node = session.nodes[node_key]
        last_attempt: NodeAttempt | None = node.attempts[-1] if node.attempts else None
        attempt_view: dict[str, Any] = {}
        if last_attempt is not None:
            attempt_view = {
                "attempt_id": last_attempt.id,
                "attempt_no": last_attempt.attempt_no,
                "status": last_attempt.status.value,
                "verifier_results": list(
                    last_attempt.output.get("_verifier_results", [])
                ),
                "feedback": list(last_attempt.output.get("_feedback", [])),
            }
        gate_views: list[dict[str, Any]] = []
        for gate in node.gates:
            gate_views.append(
                {
                    "gate_id": gate.id,
                    "name": gate.name,
                    "outcome": gate.outcome.value,
                    "verifier_results": list(
                        gate.detail.get("verifier_results", [])
                    ),
                    "feedback": list(gate.detail.get("feedback", [])),
                }
            )
        per_node[node_key] = {
            "status": node.status.value,
            "latest_attempt": attempt_view,
            "gates": gate_views,
        }
    return {"nodes": per_node}


def collect_planner_diagnostics(session: RuntimeSession) -> dict[str, Any]:
    """Extract ``planner_diagnostics`` + ``last_plan_delta_diagnostics`` shapes."""

    plan = session.metadata.get("execution_plan")
    if not isinstance(plan, dict):
        return {"planner_diagnostics": None, "last_plan_delta_diagnostics": None}
    plan_metadata = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
    diagnostics = (
        plan_metadata.get("planner_diagnostics")
        if isinstance(plan_metadata, dict)
        else None
    )
    delta_diagnostics = (
        plan_metadata.get("last_plan_delta_diagnostics")
        if isinstance(plan_metadata, dict)
        else None
    )
    return {
        "planner_diagnostics": diagnostics,
        "last_plan_delta_diagnostics": delta_diagnostics,
    }


# ---------------------------------------------------------------------------
# Artifact copy + manifest
# ---------------------------------------------------------------------------


def find_markdown_image_paths(markdown: str) -> list[str]:
    """Return image paths referenced in a markdown body, preserving order."""

    return list(_MD_IMAGE_RE.findall(markdown))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def copy_blog_and_assets(
    *,
    artifact_root: Path,
    output_dir: Path,
    blog_relative: str,
) -> dict[str, Any]:
    """Copy the final blog markdown + locally referenced images into ``evidence/``.

    Returns a record summarizing which files were copied. Never reads file
    contents into the manifest output beyond the sha256 digest.
    """

    record: dict[str, Any] = {
        "blog_source": str(artifact_root / blog_relative),
        "blog_copied": False,
        "blog_sha256": None,
        "asset_copies": [],
    }
    blog_source = artifact_root / blog_relative
    if not blog_source.is_file():
        return record
    blog_target = output_dir / "blog.md"
    blog_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(blog_source, blog_target)
    record["blog_copied"] = True
    record["blog_sha256"] = sha256_file(blog_target)

    body = blog_source.read_text(encoding="utf-8", errors="replace")
    references = find_markdown_image_paths(body)
    assets_dir = output_dir / "assets"
    for ref in references:
        if ref.startswith(("http://", "https://", "data:")):
            continue
        candidate = (blog_source.parent / ref).resolve()
        try:
            artifact_real = artifact_root.resolve()
        except OSError:
            artifact_real = artifact_root
        if not str(candidate).startswith(str(artifact_real)):
            record["asset_copies"].append(
                {"ref": ref, "status": "outside_artifact_root"}
            )
            continue
        if not candidate.is_file():
            record["asset_copies"].append({"ref": ref, "status": "missing"})
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        target = assets_dir / candidate.name
        # Avoid clobbering distinct files with the same name by suffixing.
        if target.exists() and sha256_file(target) != sha256_file(candidate):
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while target.exists():
                target = assets_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        shutil.copy2(candidate, target)
        record["asset_copies"].append(
            {
                "ref": ref,
                "status": "copied",
                "target": str(target.relative_to(output_dir)),
                "sha256": sha256_file(target),
            }
        )
    return record


def copy_trace(trace_path: Path | None, output_dir: Path) -> dict[str, Any]:
    if trace_path is None:
        return {"copied": False, "reason": "no_trace_path_provided"}
    if not trace_path.is_file():
        return {"copied": False, "reason": "trace_missing", "source": str(trace_path)}
    target = output_dir / "trace.jsonl"
    shutil.copy2(trace_path, target)
    return {
        "copied": True,
        "source": str(trace_path),
        "target": str(target.relative_to(output_dir)),
        "sha256": sha256_file(target),
    }


def list_evidence_files(output_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for child in sorted(output_dir.rglob("*")):
        if child.is_file():
            files.append(
                {
                    "path": str(child.relative_to(output_dir)),
                    "size": child.stat().st_size,
                    "sha256": sha256_file(child),
                }
            )
    return files


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def collect_evidence(
    *,
    session: RuntimeSession,
    artifact_root: Path,
    output_dir: Path,
    trace_path: Path | None,
    blog_relative: str = "blog/mas-production-blog.md",
) -> dict[str, Any]:
    """Run the full evidence pipeline and return a manifest dict.

    Writes ``ledger.json``, ``verifier_results.json``, ``planner_diagnostics.json``,
    ``manifest.json`` plus copies of the blog/trace/assets under ``output_dir``.

    Re-reads each JSON file from disk and runs ``assert_no_secrets`` on the
    parsed payload; if any leak is detected the process exits with code 2.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = collect_ledger(session)
    verifier = collect_verifier_results(session)
    planner = collect_planner_diagnostics(session)
    write_json(output_dir / "ledger.json", ledger)
    write_json(output_dir / "verifier_results.json", verifier)
    write_json(output_dir / "planner_diagnostics.json", planner)
    blog_record = copy_blog_and_assets(
        artifact_root=artifact_root,
        output_dir=output_dir,
        blog_relative=blog_relative,
    )
    trace_record = copy_trace(trace_path, output_dir)
    files = list_evidence_files(output_dir)
    manifest: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status.value,
        "workflow": session.workflow_name,
        "version": session.version,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "artifact_root": str(artifact_root),
        "trace_path": str(trace_path) if trace_path is not None else None,
        "blog_record": blog_record,
        "trace_record": trace_record,
        "files": files,
    }
    write_json(output_dir / "manifest.json", manifest)
    _verify_all_outputs(output_dir)
    manifest["redaction_check"] = "passed"
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def _verify_all_outputs(output_dir: Path) -> None:
    for name in (
        "ledger.json",
        "verifier_results.json",
        "planner_diagnostics.json",
        "manifest.json",
    ):
        path = output_dir / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert_no_secrets(name, payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence_collector.py",
        description=(
            "Collect a redaction-safe evidence package for the MAS production "
            "blog scenario."
        ),
    )
    parser.add_argument(
        "--session-store",
        choices=("memory", "sqlite"),
        default="sqlite",
        help="session store kind",
    )
    parser.add_argument(
        "--store-path",
        default=None,
        help="SQLite store path when --session-store=sqlite",
    )
    parser.add_argument("--session-id", required=True, help="RuntimeSession id")
    parser.add_argument(
        "--artifact-root",
        required=True,
        help="artifact root used by the goal run",
    )
    parser.add_argument(
        "--trace-path",
        default=None,
        help="JSONL trace path produced by JsonlObserver (optional)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="output directory under which evidence files are written",
    )
    parser.add_argument(
        "--blog-relative",
        default="blog/mas-production-blog.md",
        help="path of the final blog markdown relative to --artifact-root",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """Entry point used by both ``python evidence_collector.py`` and tests."""

    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    store = build_session_store(args.session_store, args.store_path)
    session = asyncio.run(load_session(store, args.session_id))
    artifact_root = Path(args.artifact_root)
    output_dir = Path(args.output_dir)
    trace_path = Path(args.trace_path) if args.trace_path else None
    manifest = collect_evidence(
        session=session,
        artifact_root=artifact_root,
        output_dir=output_dir,
        trace_path=trace_path,
        blog_relative=args.blog_relative,
    )
    sys.stdout.write(json.dumps(manifest, ensure_ascii=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
