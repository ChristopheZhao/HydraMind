"""Read-only renderer for recorded JSONL execution traces.

Consumes the durable artifact ``JsonlObserver`` writes (one
``ObservationEvent.model_dump_json()`` per line) and renders operator-readable
timeline and summary views. This is the read side of observability: it never
constructs or mutates runtime state and never imports the harness layer. It is
a pure consumer of the existing ``ObservationEvent`` model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from hydramind.observability import ObservationEvent, ObservationEventKind

_K = ObservationEventKind

# Only these session kinds end a session; session_running / session_resuming /
# session_waiting_gate are mid-flight and must not be reported as terminal.
_TERMINAL_SESSION_KINDS = frozenset(
    {_K.SESSION_COMPLETED, _K.SESSION_FAILED, _K.SESSION_CANCELLED}
)


@dataclass(frozen=True)
class TraceLoadResult:
    """Parsed events plus a bounded record of unparseable lines."""

    events: list[ObservationEvent]
    parse_errors: list[tuple[int, str]]


@dataclass(frozen=True)
class SessionTraceSummary:
    """Per-session rollup derived purely from the recorded events."""

    session_id: str
    terminal_status: str | None
    event_count: int
    node_count: int
    gate_record_count: int
    decision_count: int
    model_invocation_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    started_at: str | None
    ended_at: str | None
    wall_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_trace(path: str | Path) -> TraceLoadResult:
    """Parse a JSONL trace file into events, counting unparseable lines.

    Blank lines are skipped silently; lines that are not valid JSON or do not
    validate as an ``ObservationEvent`` are recorded as ``(line_no, reason)``.
    """

    trace_path = Path(path)
    if not trace_path.exists():
        raise FileNotFoundError(str(trace_path))

    events: list[ObservationEvent] = []
    parse_errors: list[tuple[int, str]] = []
    with trace_path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(ObservationEvent.model_validate_json(line))
            except ValueError as exc:
                parse_errors.append((line_no, str(exc).splitlines()[0]))
    return TraceLoadResult(events=events, parse_errors=parse_errors)


def filter_events(
    events: list[ObservationEvent],
    *,
    session: str | None = None,
    kinds: list[str] | None = None,
) -> list[ObservationEvent]:
    """Return the subset matching the session id and/or event kinds."""

    kind_set = set(kinds) if kinds else None
    result = []
    for event in events:
        if session is not None and event.session_id != session:
            continue
        if kind_set is not None and event.kind.value not in kind_set:
            continue
        result.append(event)
    return result


def group_by_session(
    events: list[ObservationEvent],
) -> dict[str, list[ObservationEvent]]:
    """Group events by session id, each list stably ordered by time."""

    grouped: dict[str, list[ObservationEvent]] = {}
    for event in events:
        grouped.setdefault(event.session_id, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda e: (e.created_at, e.event_id))
    return grouped


def summarize_session(
    session_id: str, events: list[ObservationEvent]
) -> SessionTraceSummary:
    """Compute counts, wall span, and token totals for one session."""

    ordered = sorted(events, key=lambda e: (e.created_at, e.event_id))
    terminal_status: str | None = None
    nodes: set[str] = set()
    gate_records = 0
    decisions = 0
    model_invocations = 0
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0

    for event in ordered:
        kind = event.kind
        if kind in _TERMINAL_SESSION_KINDS:
            terminal_status = kind.value
        if event.node_key:
            nodes.add(event.node_key)
        if kind is _K.GATE_RECORDED:
            gate_records += 1
        elif kind is _K.DECISION_APPLIED:
            decisions += 1
        elif kind is _K.MODEL_INVOKE_COMPLETED:
            model_invocations += 1
            usage = event.detail.get("usage")
            if isinstance(usage, dict):
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
        elif kind is _K.TOOL_CALL_COMPLETED:
            tool_calls += 1

    started_at = ordered[0].created_at if ordered else None
    ended_at = ordered[-1].created_at if ordered else None
    wall_seconds = (
        round((ended_at - started_at).total_seconds(), 3)
        if started_at and ended_at
        else 0.0
    )
    return SessionTraceSummary(
        session_id=session_id,
        terminal_status=terminal_status,
        event_count=len(ordered),
        node_count=len(nodes),
        gate_record_count=gate_records,
        decision_count=decisions,
        model_invocation_count=model_invocations,
        tool_call_count=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        started_at=started_at.isoformat() if started_at else None,
        ended_at=ended_at.isoformat() if ended_at else None,
        wall_seconds=wall_seconds,
    )


def _detail_oneline(event: ObservationEvent) -> str:
    """Bounded, per-kind one-line summary of the (already-redacted) detail."""

    detail = event.detail
    kind = event.kind

    def pick(*keys: str) -> str:
        parts = [f"{k}={detail[k]}" for k in keys if k in detail and detail[k] not in ("", None)]
        return " ".join(parts)

    # Field names below mirror the emitter-side detail builders in
    # control/session_observability.py and orchestration/agent_*.py; the unit
    # tests build fixtures from those same builders so drift surfaces.
    if kind is _K.GATE_RECORDED:
        return pick("gate_name", "outcome")
    if kind is _K.DECISION_APPLIED:
        # `actor` is a first-class event field, not part of detail.
        bits = [f"actor={event.actor}"] if event.actor else []
        rest = pick("action", "target_node_status")
        if rest:
            bits.append(rest)
        return " ".join(bits)
    if kind is _K.MODEL_INVOKE_COMPLETED:
        usage = detail.get("usage")
        usage_str = ""
        if isinstance(usage, dict):
            usage_str = (
                f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}"
            )
        model = detail.get("model_id", "")
        return " ".join(p for p in (f"model={model}" if model else "", usage_str) if p)
    if kind is _K.TOOL_CALL_STARTED:
        return pick("tool_name")
    if kind is _K.TOOL_CALL_COMPLETED:
        return pick("is_error", "content_length")
    if kind.value.startswith("node_"):
        # node_* detail varies by builder: error (failures), reason
        # (revisions/aborts), has_output (completions).
        return pick("error", "reason", "has_output")
    if not detail:
        return ""
    keys = ",".join(sorted(detail)[:4])
    return f"detail[{keys}]"


def _format_event_line(event: ObservationEvent, pad: str) -> str:
    head = f"{pad}{event.created_at.isoformat()} {event.kind.value}"
    if event.node_key:
        head += f" [{event.node_key}]"
    summary = _detail_oneline(event)
    if summary:
        head += f" — {summary}"
    return head


def _emit_children(
    parent_id: str | None,
    depth: int,
    children: dict[str | None, list[ObservationEvent]],
    indent: str,
    lines: list[str],
) -> None:
    for event in sorted(
        children.get(parent_id, []), key=lambda e: (e.created_at, e.event_id)
    ):
        lines.append(_format_event_line(event, indent * (depth + 1)))
        _emit_children(event.event_id, depth + 1, children, indent, lines)


def render_timeline(
    events: list[ObservationEvent],
    *,
    indent: str = "  ",
) -> str:
    """Render a per-session timeline, nesting children under parents."""

    grouped = group_by_session(events)
    lines: list[str] = []
    for session_id in sorted(grouped):
        session_events = grouped[session_id]
        lines.append(f"session {session_id}")
        present_ids = {e.event_id for e in session_events}
        children: dict[str | None, list[ObservationEvent]] = {}
        for event in session_events:
            # An event whose parent is absent from this session is a root.
            parent = (
                event.parent_event_id
                if event.parent_event_id in present_ids
                else None
            )
            children.setdefault(parent, []).append(event)
        _emit_children(None, 0, children, indent, lines)
    return "\n".join(lines)


def render_summary(
    events: list[ObservationEvent],
    *,
    as_json: bool = False,
) -> str:
    """Render per-session summaries as text or machine-readable JSON."""

    grouped = group_by_session(events)
    summaries = [
        summarize_session(session_id, grouped[session_id])
        for session_id in sorted(grouped)
    ]
    if as_json:
        return json.dumps([s.to_dict() for s in summaries], ensure_ascii=False)

    lines: list[str] = []
    for summary in summaries:
        lines.append(f"session {summary.session_id}")
        lines.append(f"  status      {summary.terminal_status or '(none)'}")
        lines.append(f"  events      {summary.event_count}")
        lines.append(
            f"  nodes       {summary.node_count}  "
            f"gates {summary.gate_record_count}  "
            f"decisions {summary.decision_count}"
        )
        lines.append(
            f"  invokes     {summary.model_invocation_count}  "
            f"tools {summary.tool_call_count}  "
            f"tokens in={summary.input_tokens} out={summary.output_tokens}"
        )
        lines.append(f"  wall        {summary.wall_seconds}s")
    return "\n".join(lines)


__all__ = [
    "SessionTraceSummary",
    "TraceLoadResult",
    "filter_events",
    "group_by_session",
    "load_trace",
    "render_summary",
    "render_timeline",
    "summarize_session",
]
