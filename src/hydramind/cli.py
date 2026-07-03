"""HydraMind CLI entry point."""

from __future__ import annotations

import sys

from hydramind import __version__
from hydramind.cli_doctor import run_doctor
from hydramind.cli_parser import build_parser
from hydramind.cli_runtime import handle_goal, handle_run, handle_worker
from hydramind.cli_trace import run_trace
from hydramind.runtime import create_provider


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args:
        parser.print_help()
        return 0
    if args[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    if args[0] in {"-V", "--version"}:
        print(f"hydramind {__version__}")
        return 0
    parsed = parser.parse_args(args)
    if parsed.command == "goal":
        return handle_goal(parsed)
    if parsed.command == "run":
        return handle_run(parsed)
    if parsed.command == "worker":
        return handle_worker(parsed)
    if parsed.command == "doctor":
        return run_doctor(parsed, provider_factory=create_provider)
    if parsed.command == "trace":
        return run_trace(parsed)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
