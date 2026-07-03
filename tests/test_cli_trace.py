"""CLI coverage for the read-only `hydramind trace` subcommand."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hydramind import cli
from hydramind.cli_parser import build_parser
from hydramind.control.session_observability import gate_recorded_detail
from hydramind.observability import ObservationEvent, ObservationEventKind


def _write_trace(path: Path) -> Path:
    events = [
        ObservationEvent(
            event_id="e0",
            kind=ObservationEventKind.SESSION_CREATED,
            session_id="s1",
            created_at=datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC),
        ),
        ObservationEvent(
            event_id="e1",
            kind=ObservationEventKind.GATE_RECORDED,
            session_id="s1",
            node_key="code",
            parent_event_id="e0",
            detail=gate_recorded_detail(gate_id="g0", gate_name="g", outcome="pass"),
            created_at=datetime(2026, 6, 13, 12, 0, 1, tzinfo=UTC),
        ),
        ObservationEvent(
            event_id="e2",
            kind=ObservationEventKind.SESSION_COMPLETED,
            session_id="s1",
            created_at=datetime(2026, 6, 13, 12, 0, 2, tzinfo=UTC),
        ),
    ]
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.model_dump_json())
            fh.write("\n")
    return path


def test_parser_registers_trace_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["trace", "some.jsonl", "--mode", "summary"])
    assert args.command == "trace"
    assert args.path == "some.jsonl"
    assert args.mode == "summary"


def test_trace_timeline_renders_and_exits_zero(tmp_path: Path, capsys) -> None:
    path = _write_trace(tmp_path / "t.jsonl")
    rc = cli.main(["trace", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "session s1" in out
    assert "gate_recorded" in out


def test_trace_summary_json(tmp_path: Path, capsys) -> None:
    path = _write_trace(tmp_path / "t.jsonl")
    rc = cli.main(["trace", str(path), "--mode", "summary", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed[0]["session_id"] == "s1"
    assert parsed[0]["terminal_status"] == "session_completed"


def test_trace_kind_filter(tmp_path: Path, capsys) -> None:
    path = _write_trace(tmp_path / "t.jsonl")
    rc = cli.main(["trace", str(path), "--kind", "gate_recorded"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "gate_recorded" in out
    assert "session_completed" not in out


def test_trace_missing_file_exits_two(tmp_path: Path, capsys) -> None:
    rc = cli.main(["trace", str(tmp_path / "nope.jsonl")])
    assert rc == 2
