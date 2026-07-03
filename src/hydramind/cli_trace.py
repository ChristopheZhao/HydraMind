"""`hydramind trace` subcommand: read-only execution-trace inspection."""

from __future__ import annotations

import argparse
from typing import Any

from hydramind.trace_console import (
    filter_events,
    load_trace,
    render_summary,
    render_timeline,
)


def register_trace_command(subparsers: Any) -> None:
    """Register the read-only `trace` subcommand on the CLI subparsers."""

    trace = subparsers.add_parser(
        "trace", help="inspect a recorded JSONL execution-trace artifact"
    )
    trace.add_argument("path", help="path to a JSONL trace artifact")
    trace.add_argument(
        "--mode",
        choices=("timeline", "summary"),
        default="timeline",
        help="render a per-session event timeline or a per-session rollup",
    )
    trace.add_argument(
        "--session",
        default=None,
        help="only render events for this session id",
    )
    trace.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="EVENT_KIND",
        help="only render events of this kind; may be repeated",
    )
    trace.add_argument(
        "--json",
        action="store_true",
        help="emit the summary as machine-readable JSON (summary mode)",
    )


def run_trace(args: argparse.Namespace) -> int:
    """Load, filter, and render a trace artifact. Returns the exit code."""

    try:
        result = load_trace(args.path)
    except FileNotFoundError:
        print(f"trace file not found: {args.path}")
        return 2

    if not result.events and not result.parse_errors:
        print(f"trace is empty: {args.path}")
        return 2

    events = filter_events(
        result.events,
        session=args.session,
        kinds=list(args.kind) or None,
    )

    if args.mode == "summary":
        print(render_summary(events, as_json=args.json))
    else:
        print(render_timeline(events))

    if result.parse_errors:
        shown = result.parse_errors[:5]
        print(f"\n[{len(result.parse_errors)} unparseable line(s) skipped]")
        for line_no, reason in shown:
            print(f"  line {line_no}: {reason}")

    return 0


__all__ = ["register_trace_command", "run_trace"]
