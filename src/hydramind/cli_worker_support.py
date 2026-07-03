"""CLI worker loop and daemon support helpers."""

from __future__ import annotations

import argparse
import signal
from collections.abc import Callable
from types import FrameType
from typing import Any

from hydramind.cli_queue import build_cli_queue_adapter
from hydramind.cli_support import split_values
from hydramind.observability import Emitter
from hydramind.runtime import (
    run_queued_goal_session_loop,
    run_queued_session_loop,
)
from hydramind.runtime_worker import WorkerLoopResult

__all__ = [
    "run_goal_worker_daemon",
    "run_goal_worker_loop",
    "run_worker_daemon",
    "run_worker_loop",
    "worker_daemon_bound_error",
    "worker_daemon_source_error",
    "worker_loop_bound_error",
    "worker_loop_source_error",
]


async def run_worker_loop(
    args: argparse.Namespace,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    queue = build_cli_queue_adapter(args)
    try:
        return await run_queued_session_loop(
            args.workflow,
            session_ids=tuple(getattr(args, "session_id", None) or ()),
            queue_adapter=queue,
            provider_name=args.provider,
            env_file=args.env_file,
            live_tools=bool(args.live_tools),
            session_store_kind=args.session_store,
            store_path=args.store_path,
            timeout=args.timeout,
            retry_on_error=not bool(args.no_retry_on_error),
            max_iterations=getattr(args, "max_iterations", None),
            max_idle_cycles=getattr(args, "max_idle_cycles", None),
            stop_requested=stop_requested,
        )
    finally:
        if queue is not None:
            await queue.close()


async def run_worker_daemon(args: argparse.Namespace) -> WorkerLoopResult:
    stopper = _SignalStopper()
    stopper.install()
    try:
        return await run_worker_loop(args, stop_requested=stopper.requested)
    finally:
        stopper.restore()


async def run_goal_worker_loop(
    args: argparse.Namespace,
    *,
    emitter: Emitter | None,
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...],
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    queue = build_cli_queue_adapter(args)
    try:
        return await run_queued_goal_session_loop(
            session_ids=tuple(getattr(args, "session_id", None) or ()),
            queue_adapter=queue,
            provider_name=args.provider,
            env_file=args.env_file,
            live_tools=bool(args.live_tools),
            session_store_kind=args.session_store,
            store_path=args.store_path,
            timeout=args.timeout,
            retry_on_error=not bool(args.no_retry_on_error),
            emitter=emitter,
            planner_name=args.planner,
            artifact_root=args.artifact_root,
            approved_tools=tuple(split_values(args.approved_tool)),
            allowed_process_commands=tuple(split_values(args.allow_process_command)),
            allowed_process_argv_prefixes=allowed_process_argv_prefixes,
            enable_episodic_memory=bool(args.enable_episodic_memory),
            enable_agent_memory=bool(args.enable_agent_memory),
            memory_store_kind=args.memory_store,
            memory_store_path=args.memory_store_path,
            max_tool_rounds=getattr(args, "max_tool_rounds", None),
            max_auto_repairs=getattr(args, "max_auto_repairs", None),
            max_iterations=getattr(args, "max_iterations", None),
            max_idle_cycles=getattr(args, "max_idle_cycles", None),
            stop_requested=stop_requested,
        )
    finally:
        if queue is not None:
            await queue.close()


async def run_goal_worker_daemon(
    args: argparse.Namespace,
    *,
    emitter: Emitter | None,
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...],
) -> WorkerLoopResult:
    stopper = _SignalStopper()
    stopper.install()
    try:
        return await run_goal_worker_loop(
            args,
            emitter=emitter,
            allowed_process_argv_prefixes=allowed_process_argv_prefixes,
            stop_requested=stopper.requested,
        )
    finally:
        stopper.restore()


class _SignalStopper:
    def __init__(self) -> None:
        self._requested = False
        self._signals = (signal.SIGINT, signal.SIGTERM)
        self._previous_handlers: dict[int, Any] = {}

    def requested(self) -> bool:
        return self._requested

    def install(self) -> None:
        for sig in self._signals:
            key = int(sig)
            self._previous_handlers[key] = signal.getsignal(sig)
            signal.signal(sig, self._handle)

    def restore(self) -> None:
        for sig in self._signals:
            key = int(sig)
            if key in self._previous_handlers:
                signal.signal(sig, self._previous_handlers[key])
        self._previous_handlers.clear()

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self._requested = True


def worker_loop_source_error(args: argparse.Namespace) -> dict[str, Any] | None:
    session_ids = tuple(getattr(args, "session_id", None) or ())
    queue_kind = getattr(args, "queue", None)
    if session_ids and queue_kind is not None:
        return {
            "ok": False,
            "reason": "worker_loop_ambiguous_work_source",
            "message": "use either --session-id or --queue, not both",
            "worker_command": args.worker_command,
        }
    if not session_ids and queue_kind is None:
        return {
            "ok": False,
            "reason": "worker_loop_requires_work_source",
            "message": "worker loop commands require --session-id or --queue",
            "worker_command": args.worker_command,
        }
    return None


def worker_daemon_source_error(args: argparse.Namespace) -> dict[str, Any] | None:
    session_ids = tuple(getattr(args, "session_id", None) or ())
    queue_kind = getattr(args, "queue", None)
    if session_ids:
        return {
            "ok": False,
            "reason": "worker_daemon_rejects_session_id",
            "message": "worker daemon commands require a broker queue, not --session-id",
            "worker_command": args.worker_command,
        }
    if queue_kind is None:
        return {
            "ok": False,
            "reason": "worker_daemon_requires_queue",
            "message": "worker daemon commands require --queue redis",
            "worker_command": args.worker_command,
        }
    return None


def worker_daemon_bound_error(args: argparse.Namespace) -> dict[str, Any] | None:
    max_iterations = getattr(args, "max_iterations", None)
    if max_iterations is not None and max_iterations <= 0:
        return {
            "ok": False,
            "reason": "worker_daemon_invalid_stop_bound",
            "field": "max_iterations",
            "value": max_iterations,
            "message": "max_iterations must be positive",
            "worker_command": args.worker_command,
        }
    return None


def worker_loop_bound_error(args: argparse.Namespace) -> dict[str, Any] | None:
    max_iterations = getattr(args, "max_iterations", None)
    max_idle_cycles = getattr(args, "max_idle_cycles", None)
    if max_iterations is None and max_idle_cycles is None:
        return {
            "ok": False,
            "reason": "worker_loop_requires_stop_bound",
            "message": (
                "worker loop commands require --max-iterations or "
                "--max-idle-cycles"
            ),
            "worker_command": args.worker_command,
        }
    for name, value in (
        ("max_iterations", max_iterations),
        ("max_idle_cycles", max_idle_cycles),
    ):
        if value is not None and value <= 0:
            return {
                "ok": False,
                "reason": "worker_loop_invalid_stop_bound",
                "field": name,
                "value": value,
                "message": f"{name} must be positive",
                "worker_command": args.worker_command,
            }
    return None
