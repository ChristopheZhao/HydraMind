"""HydraMind CLI parser construction."""

from __future__ import annotations

import argparse

from hydramind.cli_doctor import register_doctor_commands
from hydramind.cli_trace import register_trace_command

__all__ = [
    "build_parser",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydramind", description="HydraMind MAS runtime")
    parser.add_argument("-V", "--version", action="store_true", help="show version")
    sub = parser.add_subparsers(dest="command")
    goal = sub.add_parser("goal", help="run a goal-driven task")
    goal.add_argument("objective", help="goal objective")
    goal.add_argument(
        "--tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names available to the goal",
    )
    goal.add_argument(
        "--required-tool",
        action="append",
        default=[],
        help=(
            "tool name or comma-separated tool names that must succeed during "
            "goal execution"
        ),
    )
    goal.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="constraint or comma-separated constraints for the goal",
    )
    goal.add_argument(
        "--success-criteria",
        action="append",
        default=[],
        help="success criterion or comma-separated success criteria",
    )
    goal.add_argument(
        "--expected-artifact",
        action="append",
        default=[],
        help="expected artifact path or comma-separated paths under artifact root",
    )
    goal.add_argument(
        "--artifact-root",
        default=None,
        help="artifact root for goal tool execution and verifier checks",
    )
    goal.add_argument(
        "--approved-tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names approved for policy-gated execution",
    )
    goal.add_argument(
        "--allow-process-command",
        action="append",
        default=[],
        help="process.run argv[0] value allowed for this goal run",
    )
    goal.add_argument(
        "--allow-process-argv-prefix",
        action="append",
        default=[],
        help=(
            "process.run argv prefix allowed for this goal run; parsed with "
            "shell-style quoting and may be repeated"
        ),
    )
    goal.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="additional input value; may be repeated",
    )
    goal.add_argument("--provider", default="env", help="provider override")
    goal.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "model"),
        help="goal planner implementation",
    )
    goal.add_argument("--env-file", default=".env", help="dotenv file to load")
    goal.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    goal.add_argument(
        "--enqueue-only",
        action="store_true",
        help="create a queued goal session and exit without running it",
    )
    goal.add_argument(
        "--session-store",
        default="memory",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    goal.add_argument("--store-path", default=None, help="SQLite database path")
    goal.add_argument(
        "--quality-contract",
        default=None,
        help="path to a JSON file declaring a GoalArtifactQualityContract for the goal artifact",
    )
    goal.add_argument(
        "--enable-episodic-memory",
        action="store_true",
        help="enable opt-in projection of a compact episodic-memory summary for this goal run",
    )
    goal.add_argument(
        "--enable-agent-memory",
        action="store_true",
        help="enable opt-in agent-turn memory for this goal run",
    )
    goal.add_argument(
        "--memory-store",
        default=None,
        choices=("memory", "sqlite"),
        help="memory store backend for goal memory context and observers",
    )
    goal.add_argument(
        "--memory-store-path",
        default=None,
        help="SQLite database path when --memory-store=sqlite",
    )
    goal.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help="override per-node tool-call drain budget (default: 4 — raise for complex single-task goals)",
    )
    goal.add_argument(
        "--max-auto-repairs",
        type=int,
        default=None,
        help="override per-session bounded verifier-feedback auto-repair budget (default: 1)",
    )
    goal.add_argument(
        "--trace-path",
        default=None,
        help="path to write JSONL observation trace for this goal run",
    )
    _add_queue_arguments(goal)
    run = sub.add_parser("run", help="run a workflow YAML")
    run.add_argument("workflow", help="path to workflow.yaml")
    run.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="workflow input value; may be repeated",
    )
    run.add_argument(
        "--provider",
        default="env",
        help="provider override: env, openai_compatible, claude (production "
        "providers); mock is the offline replay/testing provider (hydramind.testing, "
        "non-agent), not a representative production provider",
    )
    run.add_argument(
        "--env-file",
        default=".env",
        help="dotenv file to load before provider construction",
    )
    run.add_argument(
        "--live-tools",
        action="store_true",
        help="allow built-in tools to use live network APIs",
    )
    run.add_argument(
        "--session-store",
        default="memory",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    run.add_argument(
        "--store-path",
        default=None,
        help="SQLite database path when --session-store=sqlite",
    )
    run.add_argument(
        "--artifact-root",
        default=None,
        help="directory under which workflow node tools write artifacts "
        "(defaults to <workflow-dir>/artifacts)",
    )
    run.add_argument(
        "--mock-fixture",
        default=None,
        help="JSON fixture path for --provider mock (offline replay/testing only, "
        "non-agent): an input-keyed record/replay corpus "
        "(hydramind.testing.MockProvider.from_fixture) that deterministically "
        "drives team members offline; not live-agent/MAS evidence",
    )
    run.add_argument(
        "--enqueue-only",
        action="store_true",
        help="create a queued session and exit without running the workflow",
    )
    _add_queue_arguments(run)
    worker = sub.add_parser("worker", help="run queued sessions")
    worker_sub = worker.add_subparsers(dest="worker_command", required=True)
    once = worker_sub.add_parser("once", help="run one queued session id")
    once.add_argument("workflow", help="path to workflow.yaml")
    once.add_argument("--session-id", required=True, help="existing RuntimeSession id")
    once.add_argument("--provider", default="env", help="provider override")
    once.add_argument("--env-file", default=".env", help="dotenv file to load")
    once.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    once.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    once.add_argument("--store-path", default=None, help="SQLite database path")
    loop = worker_sub.add_parser(
        "loop",
        help="run a bounded loop over queued workflow sessions",
    )
    loop.add_argument("workflow", help="path to workflow.yaml")
    loop.add_argument(
        "--session-id",
        action="append",
        help="existing RuntimeSession id; may be repeated",
    )
    loop.add_argument("--provider", default="env", help="provider override")
    loop.add_argument("--env-file", default=".env", help="dotenv file to load")
    loop.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    loop.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    loop.add_argument("--store-path", default=None, help="SQLite database path")
    loop.add_argument("--timeout", type=float, default=None, help="dequeue timeout seconds")
    loop.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="maximum polling iterations before the worker loop stops",
    )
    loop.add_argument(
        "--max-idle-cycles",
        type=int,
        default=None,
        help="maximum consecutive idle polls before the worker loop stops",
    )
    loop.add_argument(
        "--no-retry-on-error",
        action="store_true",
        help="nack delivery errors without retrying the transient queue message",
    )
    _add_queue_arguments(loop)
    daemon = worker_sub.add_parser(
        "daemon",
        help="run a foreground Redis-backed workflow worker daemon",
    )
    daemon.add_argument("workflow", help="path to workflow.yaml")
    daemon.add_argument(
        "--session-id",
        action="append",
        help=argparse.SUPPRESS,
    )
    daemon.add_argument("--provider", default="env", help="provider override")
    daemon.add_argument("--env-file", default=".env", help="dotenv file to load")
    daemon.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    daemon.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    daemon.add_argument("--store-path", default=None, help="SQLite database path")
    daemon.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="dequeue timeout seconds used to observe shutdown requests",
    )
    daemon.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="optional maximum polling iterations for controlled daemon runs",
    )
    daemon.add_argument(
        "--no-retry-on-error",
        action="store_true",
        help="nack delivery errors without retrying the broker message",
    )
    _add_queue_arguments(daemon)
    health = worker_sub.add_parser(
        "health",
        help="inspect Redis queue liveness",
    )
    health.add_argument(
        "--worker-id",
        default="queue-health",
        help="worker identity label to include in the health output",
    )
    _add_queue_arguments(health)
    readiness = worker_sub.add_parser(
        "readiness",
        help="preflight worker configuration for distributed launch",
    )
    readiness.add_argument(
        "--worker-id",
        default="worker-readiness",
        help="worker identity label to include in the readiness output",
    )
    readiness.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    readiness.add_argument("--store-path", default=None, help="SQLite database path")
    _add_queue_arguments(readiness)
    dead_letters = worker_sub.add_parser(
        "dead-letters",
        help="inspect or replay Redis dead-lettered queue messages",
    )
    dead_letters_sub = dead_letters.add_subparsers(
        dest="dead_letters_command",
        required=True,
    )
    dead_letters_list = dead_letters_sub.add_parser(
        "list",
        help="list Redis dead-lettered queue messages",
    )
    dead_letters_list.add_argument(
        "--limit",
        type=int,
        default=None,
        help="optional maximum number of dead-lettered messages to return",
    )
    _add_queue_arguments(dead_letters_list)
    dead_letters_replay = dead_letters_sub.add_parser(
        "replay",
        help="replay a bounded batch of Redis dead-lettered queue messages",
    )
    dead_letters_replay.add_argument(
        "--limit",
        type=int,
        default=None,
        help="maximum number of dead-lettered messages to replay",
    )
    dead_letters_replay.add_argument(
        "--preserve-attempt",
        action="store_true",
        help="preserve original queue attempt instead of resetting replay attempts",
    )
    dead_letters_replay.add_argument(
        "--retain-dead-letter",
        action="store_true",
        help="retain replayed entries in the dead-letter stream",
    )
    dead_letters_replay.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="metadata to attach to replayed messages; may be repeated",
    )
    _add_queue_arguments(dead_letters_replay)
    goal_once = worker_sub.add_parser(
        "goal-once",
        help="run one queued goal session id without a workflow YAML",
    )
    goal_once.add_argument("--session-id", required=True, help="existing RuntimeSession id")
    goal_once.add_argument("--provider", default="env", help="provider override")
    goal_once.add_argument("--env-file", default=".env", help="dotenv file to load")
    goal_once.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    goal_once.add_argument(
        "--artifact-root",
        default=None,
        help="artifact root for goal tool execution and verifier checks",
    )
    goal_once.add_argument(
        "--approved-tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names approved for policy-gated execution",
    )
    goal_once.add_argument(
        "--allow-process-command",
        action="append",
        default=[],
        help="process.run argv[0] value allowed for this goal worker run",
    )
    goal_once.add_argument(
        "--allow-process-argv-prefix",
        action="append",
        default=[],
        help=(
            "process.run argv prefix allowed for this goal worker run; parsed "
            "with shell-style quoting and may be repeated"
        ),
    )
    goal_once.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "model"),
        help="goal planner implementation for feedback repair",
    )
    goal_once.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    goal_once.add_argument("--store-path", default=None, help="SQLite database path")
    goal_once.add_argument(
        "--enable-episodic-memory",
        action="store_true",
        help="enable opt-in projection of a compact episodic-memory summary for this worker run",
    )
    goal_once.add_argument(
        "--enable-agent-memory",
        action="store_true",
        help="enable opt-in agent-turn memory for this worker run",
    )
    goal_once.add_argument(
        "--memory-store",
        default=None,
        choices=("memory", "sqlite"),
        help="memory store backend for this goal worker run",
    )
    goal_once.add_argument(
        "--memory-store-path",
        default=None,
        help="SQLite database path when --memory-store=sqlite",
    )
    goal_once.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help=(
            "override per-node tool-call drain budget for the worker run "
            "(default: persisted session value, else 4)"
        ),
    )
    goal_once.add_argument(
        "--max-auto-repairs",
        type=int,
        default=None,
        help=(
            "override per-session verifier-feedback auto-repair budget for the "
            "worker run (default: persisted session value, else 1)"
        ),
    )
    goal_once.add_argument(
        "--trace-path",
        default=None,
        help="path to write JSONL observation trace for this worker run",
    )
    goal_loop = worker_sub.add_parser(
        "goal-loop",
        help="run a bounded loop over queued goal sessions",
    )
    goal_loop.add_argument(
        "--session-id",
        action="append",
        help="existing RuntimeSession id; may be repeated",
    )
    goal_loop.add_argument("--provider", default="env", help="provider override")
    goal_loop.add_argument("--env-file", default=".env", help="dotenv file to load")
    goal_loop.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    goal_loop.add_argument(
        "--artifact-root",
        default=None,
        help="artifact root for goal tool execution and verifier checks",
    )
    goal_loop.add_argument(
        "--approved-tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names approved for policy-gated execution",
    )
    goal_loop.add_argument(
        "--allow-process-command",
        action="append",
        default=[],
        help="process.run argv[0] value allowed for this goal worker run",
    )
    goal_loop.add_argument(
        "--allow-process-argv-prefix",
        action="append",
        default=[],
        help=(
            "process.run argv prefix allowed for this goal worker run; parsed "
            "with shell-style quoting and may be repeated"
        ),
    )
    goal_loop.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "model"),
        help="goal planner implementation for feedback repair",
    )
    goal_loop.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    goal_loop.add_argument("--store-path", default=None, help="SQLite database path")
    goal_loop.add_argument(
        "--enable-episodic-memory",
        action="store_true",
        help="enable opt-in projection of a compact episodic-memory summary for this worker run",
    )
    goal_loop.add_argument(
        "--enable-agent-memory",
        action="store_true",
        help="enable opt-in agent-turn memory for this worker run",
    )
    goal_loop.add_argument(
        "--memory-store",
        default=None,
        choices=("memory", "sqlite"),
        help="memory store backend for this goal worker run",
    )
    goal_loop.add_argument(
        "--memory-store-path",
        default=None,
        help="SQLite database path when --memory-store=sqlite",
    )
    goal_loop.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help=(
            "override per-node tool-call drain budget for the worker run "
            "(default: persisted session value, else 4)"
        ),
    )
    goal_loop.add_argument(
        "--max-auto-repairs",
        type=int,
        default=None,
        help=(
            "override per-session verifier-feedback auto-repair budget for the "
            "worker run (default: persisted session value, else 1)"
        ),
    )
    goal_loop.add_argument(
        "--trace-path",
        default=None,
        help="path to write JSONL observation trace for this worker run",
    )
    goal_loop.add_argument("--timeout", type=float, default=None, help="dequeue timeout seconds")
    goal_loop.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="maximum polling iterations before the worker loop stops",
    )
    goal_loop.add_argument(
        "--max-idle-cycles",
        type=int,
        default=None,
        help="maximum consecutive idle polls before the worker loop stops",
    )
    goal_loop.add_argument(
        "--no-retry-on-error",
        action="store_true",
        help="nack delivery errors without retrying the transient queue message",
    )
    _add_queue_arguments(goal_loop)
    goal_daemon = worker_sub.add_parser(
        "goal-daemon",
        help="run a foreground Redis-backed goal worker daemon",
    )
    goal_daemon.add_argument(
        "--session-id",
        action="append",
        help=argparse.SUPPRESS,
    )
    goal_daemon.add_argument("--provider", default="env", help="provider override")
    goal_daemon.add_argument("--env-file", default=".env", help="dotenv file to load")
    goal_daemon.add_argument("--live-tools", action="store_true", help="allow live tool APIs")
    goal_daemon.add_argument(
        "--artifact-root",
        default=None,
        help="artifact root for goal tool execution and verifier checks",
    )
    goal_daemon.add_argument(
        "--approved-tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names approved for policy-gated execution",
    )
    goal_daemon.add_argument(
        "--allow-process-command",
        action="append",
        default=[],
        help="process.run argv[0] value allowed for this goal worker run",
    )
    goal_daemon.add_argument(
        "--allow-process-argv-prefix",
        action="append",
        default=[],
        help=(
            "process.run argv prefix allowed for this goal worker run; parsed "
            "with shell-style quoting and may be repeated"
        ),
    )
    goal_daemon.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "model"),
        help="goal planner implementation for feedback repair",
    )
    goal_daemon.add_argument(
        "--session-store",
        default="sqlite",
        choices=("memory", "sqlite"),
        help="session store backend",
    )
    goal_daemon.add_argument("--store-path", default=None, help="SQLite database path")
    goal_daemon.add_argument(
        "--enable-episodic-memory",
        action="store_true",
        help="enable opt-in projection of a compact episodic-memory summary for this worker run",
    )
    goal_daemon.add_argument(
        "--enable-agent-memory",
        action="store_true",
        help="enable opt-in agent-turn memory for this worker run",
    )
    goal_daemon.add_argument(
        "--memory-store",
        default=None,
        choices=("memory", "sqlite"),
        help="memory store backend for this goal worker run",
    )
    goal_daemon.add_argument(
        "--memory-store-path",
        default=None,
        help="SQLite database path when --memory-store=sqlite",
    )
    goal_daemon.add_argument(
        "--max-tool-rounds",
        type=int,
        default=None,
        help=(
            "override per-node tool-call drain budget for the worker run "
            "(default: persisted session value, else 4)"
        ),
    )
    goal_daemon.add_argument(
        "--max-auto-repairs",
        type=int,
        default=None,
        help=(
            "override per-session verifier-feedback auto-repair budget for the "
            "worker run (default: persisted session value, else 1)"
        ),
    )
    goal_daemon.add_argument(
        "--trace-path",
        default=None,
        help="path to write JSONL observation trace for this worker run",
    )
    goal_daemon.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="dequeue timeout seconds used to observe shutdown requests",
    )
    goal_daemon.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="optional maximum polling iterations for controlled daemon runs",
    )
    goal_daemon.add_argument(
        "--no-retry-on-error",
        action="store_true",
        help="nack delivery errors without retrying the broker message",
    )
    _add_queue_arguments(goal_daemon)
    register_doctor_commands(sub)
    register_trace_command(sub)
    return parser


def _add_queue_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--queue",
        default=None,
        choices=("redis",),
        help="queue adapter to publish to or poll from",
    )
    parser.add_argument(
        "--queue-redis-url",
        default=None,
        help="Redis URL when --queue=redis",
    )
    parser.add_argument(
        "--queue-stream-key",
        default="hydramind:sessions",
        help="Redis Stream key when --queue=redis",
    )
    parser.add_argument(
        "--queue-group-name",
        default="hydramind-workers",
        help="Redis consumer group when --queue=redis",
    )
    parser.add_argument(
        "--queue-consumer-name",
        default="hydramind-worker",
        help="Redis consumer name when --queue=redis",
    )
    parser.add_argument(
        "--queue-visibility-timeout",
        type=float,
        default=60.0,
        help="Redis visibility timeout in seconds when --queue=redis",
    )
    parser.add_argument(
        "--queue-max-delivery-attempts",
        type=int,
        default=None,
        help="max Redis delivery attempts before dead-lettering",
    )
