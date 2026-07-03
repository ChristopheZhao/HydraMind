"""Render HydraMind trace events as a self-contained HTML run console."""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hydramind.observability import ObservationEvent


def build_run_state(events: Iterable[ObservationEvent | dict[str, Any]]) -> dict[str, Any]:
    normalized = [_event_dict(event) for event in events]
    counts = Counter(event["kind"] for event in normalized)
    nodes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"executions": set(), "events": 0, "last_event": None}
    )
    for event in normalized:
        node_key = event.get("node_key")
        if not node_key:
            continue
        item = nodes[node_key]
        item["events"] += 1
        item["last_event"] = event["kind"]
        execution_id = event.get("execution_id") or event.get("detail", {}).get("execution_id")
        if execution_id:
            item["executions"].add(execution_id)

    rendered_nodes = []
    for key, item in sorted(nodes.items()):
        rendered_nodes.append(
            {
                "key": key,
                "events": item["events"],
                "last_event": item["last_event"],
                "executions": sorted(item["executions"]),
            }
        )

    terminal = next(
        (
            event["kind"]
            for event in reversed(normalized)
            if event["kind"] in {"session_completed", "session_failed", "session_cancelled"}
        ),
        "running",
    )
    return {
        "session_id": normalized[-1]["session_id"] if normalized else "",
        "status": terminal.replace("session_", ""),
        "event_counts": dict(sorted(counts.items())),
        "nodes": rendered_nodes,
        "events": normalized,
    }


def render_html(run_state: dict[str, Any]) -> str:
    payload = json.dumps(run_state, ensure_ascii=False, sort_keys=True)
    rows = "\n".join(_event_row(event) for event in run_state.get("events", []))
    nodes = "\n".join(_node_row(node) for node in run_state.get("nodes", []))
    counts = "\n".join(
        f"<li><span>{html.escape(kind)}</span><strong>{count}</strong></li>"
        for kind, count in run_state.get("event_counts", {}).items()
    )
    title = html.escape(str(run_state.get("session_id") or "HydraMind run"))
    status = html.escape(str(run_state.get("status") or "unknown"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HydraMind Run Console</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18212f;
      --muted: #5e6978;
      --line: #d7dde5;
      --surface: #f6f8fa;
      --accent: #0f766e;
      --warn: #9a3412;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #ffffff;
    }}
    header {{
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 650;
    }}
    .status {{
      min-width: 96px;
      padding: 5px 10px;
      border-radius: 999px;
      text-align: center;
      color: white;
      background: var(--accent);
      font-size: 13px;
      font-weight: 650;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
      min-height: calc(100vh - 62px);
    }}
    aside {{
      padding: 18px;
      border-right: 1px solid var(--line);
      background: var(--surface);
    }}
    section {{
      padding: 18px 22px;
      min-width: 0;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 14px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    ul {{
      list-style: none;
      padding: 0;
      margin: 0 0 20px;
    }}
    li {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 13px;
    }}
    th, td {{
      padding: 9px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{
      color: var(--muted);
      font-weight: 650;
      background: #f9fafb;
    }}
    .nodes {{
      margin-bottom: 22px;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    @media (max-width: 760px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      header {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="status">{status}</div>
  </header>
  <main>
    <aside>
      <h2>Event Counts</h2>
      <ul>{counts}</ul>
    </aside>
    <section>
      <h2>Node Executions</h2>
      <table class="nodes">
        <thead><tr><th>Node</th><th>Executions</th><th>Events</th><th>Last Event</th></tr></thead>
        <tbody>{nodes}</tbody>
      </table>
      <h2>Trajectory</h2>
      <table>
        <thead><tr><th>Time</th><th>Kind</th><th>Node</th><th>Execution</th><th>Detail</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </main>
  <script type="application/json" id="hydramind-run-state">{html.escape(payload)}</script>
</body>
</html>
"""


def write_console(
    events: Iterable[ObservationEvent | dict[str, Any]],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(build_run_state(events)), encoding="utf-8")
    return path


def _event_dict(event: ObservationEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, ObservationEvent):
        return event.model_dump(mode="json")
    return dict(event)


def _event_row(event: dict[str, Any]) -> str:
    detail = json.dumps(event.get("detail") or {}, ensure_ascii=False, sort_keys=True)
    return (
        "<tr>"
        f"<td><code>{html.escape(str(event.get('created_at') or ''))}</code></td>"
        f"<td>{html.escape(str(event.get('kind') or ''))}</td>"
        f"<td>{html.escape(str(event.get('node_key') or ''))}</td>"
        f"<td><code>{html.escape(str(event.get('execution_id') or ''))}</code></td>"
        f"<td><code>{html.escape(detail)}</code></td>"
        "</tr>"
    )


def _node_row(node: dict[str, Any]) -> str:
    executions = ", ".join(node.get("executions") or [])
    return (
        "<tr>"
        f"<td>{html.escape(str(node.get('key') or ''))}</td>"
        f"<td><code>{html.escape(executions)}</code></td>"
        f"<td>{html.escape(str(node.get('events') or 0))}</td>"
        f"<td>{html.escape(str(node.get('last_event') or ''))}</td>"
        "</tr>"
    )
