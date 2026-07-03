"""Runtime-backed CLI command handlers."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from hydramind.cli_queue import (
    build_cli_queue_adapter,
    enqueue_queue_error,
    queue_config_error,
    queue_publish_payload,
)
from hydramind.cli_support import (
    required_tool_evidence,
    split_values,
    tool_execution_ledger,
)
from hydramind.cli_worker_ops import handle_worker_operator
from hydramind.cli_worker_support import (
    run_goal_worker_daemon,
    run_goal_worker_loop,
    run_worker_daemon,
    run_worker_loop,
    worker_daemon_bound_error,
    worker_daemon_source_error,
    worker_loop_bound_error,
    worker_loop_source_error,
)
from hydramind.harness import ModelProvider
from hydramind.observability import Emitter, JsonlObserver
from hydramind.orchestration import (
    GoalSpec,
    load_goal_quality_contract,
)
from hydramind.runtime import (
    create_queued_goal_session,
    create_queued_session,
    run_goal,
    run_queued_goal_session_once,
    run_queued_session_once,
    run_workflow_file,
)
from hydramind.testing import MockProvider


def handle_goal(args: argparse.Namespace) -> int:
    available_tools = tuple(split_values(args.tool))
    required_tools = tuple(split_values(args.required_tool))
    expected_artifacts = tuple(split_values(args.expected_artifact))
    approved_tools = tuple(split_values(args.approved_tool))
    allowed_process_commands = tuple(split_values(args.allow_process_command))
    allowed_process_argv_prefixes = _parse_process_argv_prefixes(
        args.allow_process_argv_prefix
    )
    missing_required = sorted(set(required_tools) - set(available_tools))
    if missing_required:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "required_tool_not_available",
                    "missing_required_tools": missing_required,
                    "tools": available_tools,
                    "required_tools": required_tools,
                    "queued_only": bool(args.enqueue_only),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return 1
    quality_contract = None
    if args.quality_contract is not None:
        try:
            quality_contract = load_goal_quality_contract(args.quality_contract)
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": "quality_contract_invalid",
                        "quality_contract_path": str(args.quality_contract),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return 1
    if quality_contract is not None and not expected_artifacts:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "quality_contract_requires_expected_artifact",
                    "message": (
                        "--quality-contract requires at least one "
                        "--expected-artifact so the content verifier can locate "
                        "the artifact to validate."
                    ),
                    "quality_contract_path": str(args.quality_contract),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return 1
    enable_episodic_memory = bool(args.enable_episodic_memory)
    enable_agent_memory = bool(args.enable_agent_memory)
    trace_path_value = getattr(args, "trace_path", None)
    emitter: Emitter | None = None
    if trace_path_value:
        trace_path = Path(trace_path_value)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        emitter = Emitter([JsonlObserver(trace_path)])
    goal = GoalSpec(
        objective=str(args.objective),
        constraints=tuple(split_values(args.constraint)),
        success_criteria=tuple(split_values(args.success_criteria)),
        available_tools=available_tools,
        required_tools=required_tools,
        expected_artifacts=expected_artifacts,
        quality_contract=quality_contract,
    )
    max_tool_rounds_override = getattr(args, "max_tool_rounds", None)
    max_auto_repairs_override = getattr(args, "max_auto_repairs", None)
    queue_error = enqueue_queue_error(args)
    if queue_error is not None:
        print(json.dumps(queue_error, ensure_ascii=False, default=str))
        return 1
    queue_publish: dict[str, Any] | None = None
    if args.enqueue_only:
        session = asyncio.run(
            create_queued_goal_session(
                goal,
                input_payload=_parse_inputs(args.input),
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
                emitter=emitter,
                planner_name=args.planner,
                artifact_root=args.artifact_root,
                approved_tools=approved_tools,
                allowed_process_commands=allowed_process_commands,
                allowed_process_argv_prefixes=allowed_process_argv_prefixes,
                quality_contract=quality_contract,
                enable_episodic_memory=enable_episodic_memory,
                enable_agent_memory=enable_agent_memory,
                memory_store_kind=args.memory_store,
                memory_store_path=args.memory_store_path,
                max_tool_rounds=max_tool_rounds_override,
                max_auto_repairs=max_auto_repairs_override,
            )
        )
        queue_publish = asyncio.run(_enqueue_session_to_cli_queue(args, session.id))
    else:
        session = asyncio.run(
            run_goal(
                goal,
                input_payload=_parse_inputs(args.input),
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
                emitter=emitter,
                planner_name=args.planner,
                artifact_root=args.artifact_root,
                approved_tools=approved_tools,
                allowed_process_commands=allowed_process_commands,
                allowed_process_argv_prefixes=allowed_process_argv_prefixes,
                quality_contract=quality_contract,
                enable_episodic_memory=enable_episodic_memory,
                enable_agent_memory=enable_agent_memory,
                memory_store_kind=args.memory_store,
                memory_store_path=args.memory_store_path,
                max_tool_rounds=max_tool_rounds_override,
                max_auto_repairs=max_auto_repairs_override,
            )
        )
    plan = session.metadata.get("execution_plan")
    plan_tasks = []
    if isinstance(plan, dict):
        raw_tasks = plan.get("tasks")
        if isinstance(raw_tasks, list):
            plan_tasks = [
                task.get("key")
                for task in raw_tasks
                if isinstance(task, dict) and isinstance(task.get("key"), str)
            ]
    payload: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status.value,
        "workflow": session.workflow_name,
        "goal": goal.objective,
        "plan_tasks": plan_tasks,
        "summary_output": session.summary_output,
        "queued_only": bool(args.enqueue_only),
        "required_tools": list(required_tools),
        "expected_artifacts": list(expected_artifacts),
        "approved_tools": list(approved_tools),
    }
    if args.artifact_root is not None:
        payload["artifact_root"] = str(args.artifact_root)
    if allowed_process_commands:
        payload["allowed_process_commands"] = list(allowed_process_commands)
    if allowed_process_argv_prefixes:
        payload["allowed_process_argv_prefixes"] = [
            list(prefix) for prefix in allowed_process_argv_prefixes
        ]
    if quality_contract is not None:
        payload["quality_contract"] = quality_contract.model_dump(mode="json")
    if enable_episodic_memory:
        payload["enable_episodic_memory"] = True
    if enable_agent_memory:
        payload["enable_agent_memory"] = True
    if args.memory_store is not None:
        payload["memory_store"] = args.memory_store
    if args.memory_store_path is not None:
        payload["memory_store_path"] = str(args.memory_store_path)
    if queue_publish is not None:
        payload["queue"] = queue_publish
    if required_tools and not args.enqueue_only:
        ledger = tool_execution_ledger(session)
        payload["required_tool_evidence"] = [
            required_tool_evidence(tool_name, ledger)
            for tool_name in required_tools
        ]
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


def handle_run(args: argparse.Namespace) -> int:
    inputs = _parse_inputs(args.input)
    mock_fixture = getattr(args, "mock_fixture", None)
    fixture_provider: ModelProvider | None = None
    if mock_fixture is not None:
        if args.provider != "mock":
            raise SystemExit("--mock-fixture requires --provider mock")
        fixture_provider = MockProvider.from_fixture(mock_fixture)
    queue_error = enqueue_queue_error(args)
    if queue_error is not None:
        print(json.dumps(queue_error, ensure_ascii=False, default=str))
        return 1
    queue_publish: dict[str, Any] | None = None
    if args.enqueue_only:
        session = asyncio.run(
            create_queued_session(
                args.workflow,
                input_payload=inputs,
                provider=fixture_provider,
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
            )
        )
        queue_publish = asyncio.run(_enqueue_session_to_cli_queue(args, session.id))
    else:
        session = asyncio.run(
            run_workflow_file(
                args.workflow,
                input_payload=inputs,
                provider=fixture_provider,
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
                artifact_root=getattr(args, "artifact_root", None),
            )
        )
    payload: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status.value,
        "workflow": session.workflow_name,
        "summary_output": session.summary_output,
        "queued_only": bool(args.enqueue_only),
    }
    artifact_root = getattr(args, "artifact_root", None)
    if artifact_root is not None:
        payload["artifact_root"] = str(artifact_root)
    if queue_publish is not None:
        payload["queue"] = queue_publish
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


def handle_worker(args: argparse.Namespace) -> int:
    if args.worker_command == "once":
        result = asyncio.run(
            run_queued_session_once(
                args.workflow,
                session_id=args.session_id,
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
            )
        )
        print(result.model_dump_json())
        return 1 if result.status == "error" else 0
    if args.worker_command == "loop":
        bound_error = worker_loop_bound_error(args)
        if bound_error is not None:
            print(json.dumps(bound_error, ensure_ascii=False, default=str))
            return 1
        source_error = worker_loop_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        loop_result = asyncio.run(run_worker_loop(args))
        print(loop_result.model_dump_json())
        return loop_result.exit_code
    if args.worker_command == "daemon":
        source_error = worker_daemon_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        bound_error = worker_daemon_bound_error(args)
        if bound_error is not None:
            print(json.dumps(bound_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        daemon_result = asyncio.run(run_worker_daemon(args))
        print(daemon_result.model_dump_json())
        return daemon_result.exit_code
    operator_result = handle_worker_operator(args)
    if operator_result is not None:
        return operator_result
    if args.worker_command == "goal-once":
        worker_trace_path = getattr(args, "trace_path", None)
        worker_emitter: Emitter | None = None
        if worker_trace_path:
            trace_target = Path(worker_trace_path)
            trace_target.parent.mkdir(parents=True, exist_ok=True)
            worker_emitter = Emitter([JsonlObserver(trace_target)])
        result = asyncio.run(
            run_queued_goal_session_once(
                session_id=args.session_id,
                provider_name=args.provider,
                env_file=args.env_file,
                live_tools=bool(args.live_tools),
                session_store_kind=args.session_store,
                store_path=args.store_path,
                emitter=worker_emitter,
                planner_name=args.planner,
                artifact_root=args.artifact_root,
                approved_tools=tuple(split_values(args.approved_tool)),
                allowed_process_commands=tuple(
                    split_values(args.allow_process_command)
                ),
                allowed_process_argv_prefixes=_parse_process_argv_prefixes(
                    args.allow_process_argv_prefix
                ),
                enable_episodic_memory=bool(args.enable_episodic_memory),
                enable_agent_memory=bool(args.enable_agent_memory),
                memory_store_kind=args.memory_store,
                memory_store_path=args.memory_store_path,
                max_tool_rounds=getattr(args, "max_tool_rounds", None),
                max_auto_repairs=getattr(args, "max_auto_repairs", None),
            )
        )
        print(result.model_dump_json())
        return 1 if result.status == "error" else 0
    if args.worker_command == "goal-loop":
        bound_error = worker_loop_bound_error(args)
        if bound_error is not None:
            print(json.dumps(bound_error, ensure_ascii=False, default=str))
            return 1
        source_error = worker_loop_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        goal_loop_trace_path = getattr(args, "trace_path", None)
        goal_loop_emitter: Emitter | None = None
        if goal_loop_trace_path:
            trace_target = Path(goal_loop_trace_path)
            trace_target.parent.mkdir(parents=True, exist_ok=True)
            goal_loop_emitter = Emitter([JsonlObserver(trace_target)])
        goal_loop_result = asyncio.run(
            run_goal_worker_loop(
                args,
                emitter=goal_loop_emitter,
                allowed_process_argv_prefixes=_parse_process_argv_prefixes(
                    args.allow_process_argv_prefix
                ),
            )
        )
        print(goal_loop_result.model_dump_json())
        return goal_loop_result.exit_code
    if args.worker_command == "goal-daemon":
        source_error = worker_daemon_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        bound_error = worker_daemon_bound_error(args)
        if bound_error is not None:
            print(json.dumps(bound_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        goal_daemon_trace_path = getattr(args, "trace_path", None)
        goal_daemon_emitter: Emitter | None = None
        if goal_daemon_trace_path:
            trace_target = Path(goal_daemon_trace_path)
            trace_target.parent.mkdir(parents=True, exist_ok=True)
            goal_daemon_emitter = Emitter([JsonlObserver(trace_target)])
        goal_daemon_result = asyncio.run(
            run_goal_worker_daemon(
                args,
                emitter=goal_daemon_emitter,
                allowed_process_argv_prefixes=_parse_process_argv_prefixes(
                    args.allow_process_argv_prefix
                ),
            )
        )
        print(goal_daemon_result.model_dump_json())
        return goal_daemon_result.exit_code
    return 2


def _parse_inputs(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--input expects KEY=VALUE, got {item!r}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit("--input key must not be empty")
        try:
            parsed[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed[key] = raw_value
    return parsed


def _parse_process_argv_prefixes(items: list[str]) -> tuple[tuple[str, ...], ...]:
    prefixes: list[tuple[str, ...]] = []
    for raw in items:
        try:
            parts = tuple(shlex.split(raw))
        except ValueError as exc:
            raise SystemExit(
                f"invalid --allow-process-argv-prefix {raw!r}: {exc}"
            ) from exc
        if not parts:
            raise SystemExit("--allow-process-argv-prefix must not be empty")
        prefixes.append(parts)
    return tuple(prefixes)


async def _enqueue_session_to_cli_queue(
    args: argparse.Namespace,
    session_id: str,
) -> dict[str, Any] | None:
    queue = build_cli_queue_adapter(args)
    if queue is None:
        return None
    try:
        message = await queue.enqueue(session_id)
        return queue_publish_payload(args, queue, message)
    finally:
        await queue.close()
