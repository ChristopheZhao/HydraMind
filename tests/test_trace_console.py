"""Unit coverage for the read-only trace console renderer.

Event ``detail`` payloads are built via the REAL emitter detail builders in
``hydramind.control.session_observability`` (and the real top-level ``actor``
field) so that any future drift between what the runtime emits and what the
renderer reads surfaces here instead of being masked by hand-typed keys.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hydramind.control.session_observability import (
    gate_decision_detail,
    gate_recorded_detail,
    node_error_detail,
)
from hydramind.observability import ObservationEvent, ObservationEventKind
from hydramind.trace_console import (
    SessionTraceSummary,
    TraceLoadResult,
    filter_events,
    group_by_session,
    load_trace,
    render_summary,
    render_timeline,
    summarize_session,
)


def _evt(
    kind: ObservationEventKind,
    *,
    session_id: str = "sess-1",
    event_id: str | None = None,
    parent_event_id: str | None = None,
    node_key: str | None = None,
    second: int = 0,
    detail: dict | None = None,
    actor: str | None = None,
) -> ObservationEvent:
    return ObservationEvent(
        event_id=event_id or f"evt-{kind.value}-{second}",
        kind=kind,
        session_id=session_id,
        node_key=node_key,
        parent_event_id=parent_event_id,
        actor=actor,
        detail=detail or {},
        created_at=datetime(2026, 6, 13, 12, 0, second, tzinfo=UTC),
    )


def _write(path: Path, events: list[ObservationEvent]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.model_dump_json())
            fh.write("\n")
    return path


def _sample_session() -> list[ObservationEvent]:
    return [
        _evt(ObservationEventKind.SESSION_CREATED, event_id="e-root", second=0),
        _evt(
            ObservationEventKind.NODE_STARTED,
            event_id="e-node",
            parent_event_id="e-root",
            node_key="code",
            second=1,
        ),
        _evt(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            event_id="e-model",
            parent_event_id="e-node",
            node_key="code",
            second=2,
            # model_invoke_completed detail is built inline by the emitter
            # (no named builder); these keys mirror agent_execution.py.
            detail={
                "model_id": "deepseek-x",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        _evt(
            ObservationEventKind.GATE_RECORDED,
            event_id="e-gate",
            parent_event_id="e-node",
            node_key="code",
            second=3,
            detail=gate_recorded_detail(
                gate_id="g-policy", gate_name="ccc_policy", outcome="violation"
            ),
        ),
        _evt(
            ObservationEventKind.DECISION_APPLIED,
            event_id="e-dec",
            second=4,
            actor="simulated-human",
            detail=gate_decision_detail(
                gate_id="g-policy", action="reject", target_node_status="failed"
            ),
        ),
        _evt(ObservationEventKind.SESSION_FAILED, event_id="e-end", second=5),
    ]


# ---- load -------------------------------------------------------------------


def test_load_trace_parses_events_and_counts_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    events = _sample_session()
    _write(path, events)
    # inject malformed lines: blank, non-json, schema-invalid
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"kind": "nonsense-kind", "session_id": "x"}) + "\n")

    result = load_trace(path)
    assert isinstance(result, TraceLoadResult)
    assert len(result.events) == len(events)
    # blank line is skipped silently; the two genuinely-bad lines are reported
    assert len(result.parse_errors) == 2
    assert all(isinstance(lineno, int) for lineno, _ in result.parse_errors)


def test_load_trace_missing_file_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        load_trace(tmp_path / "nope.jsonl")


# ---- grouping / ordering ----------------------------------------------------


def test_group_by_session_sorts_by_time() -> None:
    events = list(reversed(_sample_session()))  # feed out of order
    grouped = group_by_session(events)
    assert set(grouped) == {"sess-1"}
    ordered = [e.event_id for e in grouped["sess-1"]]
    assert ordered == ["e-root", "e-node", "e-model", "e-gate", "e-dec", "e-end"]


def test_group_by_session_splits_sessions() -> None:
    a = _evt(ObservationEventKind.SESSION_CREATED, session_id="A", second=0)
    b = _evt(ObservationEventKind.SESSION_CREATED, session_id="B", second=1)
    grouped = group_by_session([b, a])
    assert set(grouped) == {"A", "B"}


# ---- summary ----------------------------------------------------------------


def test_summarize_session_computes_counts_span_and_tokens() -> None:
    events = _sample_session()
    summary = summarize_session("sess-1", events)
    assert isinstance(summary, SessionTraceSummary)
    assert summary.session_id == "sess-1"
    assert summary.terminal_status == "session_failed"
    assert summary.event_count == 6
    assert summary.gate_record_count == 1
    assert summary.decision_count == 1
    assert summary.model_invocation_count == 1
    assert summary.input_tokens == 10
    assert summary.output_tokens == 20
    assert summary.wall_seconds == 5.0


def test_non_terminal_session_kind_is_not_reported_terminal() -> None:
    # A trace that ends paused at a gate is NOT terminal; the summary must not
    # report session_waiting_gate as a terminal status.
    events = [
        _evt(ObservationEventKind.SESSION_CREATED, second=0),
        _evt(ObservationEventKind.SESSION_RUNNING, second=1),
        _evt(ObservationEventKind.SESSION_WAITING_GATE, second=2),
    ]
    summary = summarize_session("sess-1", events)
    assert summary.terminal_status is None


def test_missing_usage_does_not_crash_token_extraction() -> None:
    events = [
        _evt(ObservationEventKind.SESSION_CREATED, second=0),
        _evt(ObservationEventKind.MODEL_INVOKE_COMPLETED, second=1, detail={"model_id": "m"}),
        _evt(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            second=2,
            detail={"model_id": "m", "usage": "not-a-dict"},
        ),
    ]
    summary = summarize_session("sess-1", events)
    assert summary.model_invocation_count == 2
    assert summary.input_tokens == 0
    assert summary.output_tokens == 0


def test_summary_is_json_serializable() -> None:
    summary = summarize_session("sess-1", _sample_session())
    blob = json.dumps(summary.to_dict())
    parsed = json.loads(blob)
    assert parsed["session_id"] == "sess-1"
    assert parsed["terminal_status"] == "session_failed"


# ---- filters ----------------------------------------------------------------


def test_filter_events_by_session_and_kind() -> None:
    events = [
        *_sample_session(),
        _evt(ObservationEventKind.SESSION_CREATED, session_id="other", second=9),
    ]
    only_sess1 = filter_events(events, session="sess-1")
    assert {e.session_id for e in only_sess1} == {"sess-1"}

    only_gates = filter_events(events, kinds=["gate_recorded"])
    assert [e.kind for e in only_gates] == [ObservationEventKind.GATE_RECORDED]


def test_filter_events_session_and_kind_intersection() -> None:
    events = [
        *_sample_session(),
        _evt(ObservationEventKind.GATE_RECORDED, session_id="other", second=9),
    ]
    selected = filter_events(events, session="sess-1", kinds=["gate_recorded"])
    assert len(selected) == 1
    assert selected[0].session_id == "sess-1"
    assert selected[0].kind is ObservationEventKind.GATE_RECORDED


# ---- rendering: detail summaries must match the real emitter contract -------


def test_render_timeline_nests_children_under_parents() -> None:
    text = render_timeline(_sample_session())
    lines = text.splitlines()
    node_line = next(line for line in lines if "node_started" in line)
    model_line = next(line for line in lines if "model_invoke_completed" in line)
    node_indent = len(node_line) - len(node_line.lstrip())
    model_indent = len(model_line) - len(model_line.lstrip())
    assert model_indent > node_indent


def test_gate_recorded_summary_shows_name_and_outcome() -> None:
    lines = render_timeline(_sample_session()).splitlines()
    gate_line = next(line for line in lines if "gate_recorded" in line)
    assert "ccc_policy" in gate_line
    assert "violation" in gate_line  # the emitter field is `outcome`, value violation


def test_decision_summary_shows_actor_from_event_field() -> None:
    lines = render_timeline(_sample_session()).splitlines()
    dec_line = next(line for line in lines if "decision_applied" in line)
    # actor is a first-class event field, NOT in detail
    assert "simulated-human" in dec_line
    assert "reject" in dec_line


def test_node_failure_summary_shows_error() -> None:
    events = [
        _evt(ObservationEventKind.SESSION_CREATED, second=0),
        _evt(
            ObservationEventKind.NODE_FAILED,
            node_key="code",
            second=1,
            detail=node_error_detail(error="boom: assertion failed"),
        ),
    ]
    line = next(
        line for line in render_timeline(events).splitlines() if "node_failed" in line
    )
    assert "boom: assertion failed" in line


def test_tool_call_completed_summary_shows_error_flag_and_length() -> None:
    events = [
        _evt(ObservationEventKind.SESSION_CREATED, second=0),
        _evt(
            ObservationEventKind.TOOL_CALL_COMPLETED,
            node_key="code",
            second=1,
            # inline-built by the emitter (agent_tools.py); no named builder
            detail={"round": 0, "tool_call_id": "t1", "is_error": True, "content_length": 42},
        ),
    ]
    line = next(
        line
        for line in render_timeline(events).splitlines()
        if "tool_call_completed" in line
    )
    assert "is_error=True" in line
    assert "content_length=42" in line


def test_render_summary_text_and_json() -> None:
    events = _sample_session()
    text = render_summary(events)
    assert "sess-1" in text
    assert "session_failed" in text

    blob = render_summary(events, as_json=True)
    parsed = json.loads(blob)
    assert isinstance(parsed, list)
    assert parsed[0]["session_id"] == "sess-1"
