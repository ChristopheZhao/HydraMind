"""Goal-scenario diagnostics for ``hydramind.cli_doctor``."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hydramind.cli_doctor_tools import missing_live_tool_env
from hydramind.cli_support import (
    required_tool_evidence,
    split_values,
    tool_execution_ledger,
)
from hydramind.control import RuntimeSession
from hydramind.observability import (
    Emitter,
    JsonlObserver,
    ListObserver,
    ObservationEvent,
    ObservationEventKind,
)
from hydramind.orchestration import GoalSpec
from hydramind.runtime import load_env_file, run_goal


async def run_doctor_goal_scenario(args: Any) -> int:
    trace_path = (
        Path(args.trace_path)
        if args.trace_path
        else Path(args.artifact_root) / "goal-scenario-trace.jsonl"
    )
    try:
        load_env_file(args.env_file)
        tool_names = tuple(split_values(args.tool))
        required_tools = tuple(split_values(args.required_tool)) or tool_names
        if not tool_names:
            payload = {
                "ok": False,
                "reason": "no_tools",
                "trace_path": str(trace_path),
            }
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 1
        missing_required = sorted(set(required_tools) - set(tool_names))
        if missing_required:
            payload = {
                "ok": False,
                "reason": "required_tool_not_available",
                "missing_required_tools": missing_required,
                "tools": tool_names,
                "trace_path": str(trace_path),
            }
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 1
        missing_env = missing_live_tool_env(tool_names) if args.live_tools else []
        if missing_env:
            payload = {
                "ok": False,
                "reason": "missing_env",
                "missing_env": missing_env,
                "tools": tool_names,
                "required_tools": required_tools,
                "trace_path": str(trace_path),
            }
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 1
        if trace_path.exists():
            trace_path.unlink()
        observer = ListObserver()
        emitter = Emitter([JsonlObserver(trace_path), observer])
        objective = _goal_scenario_objective(args.objective, required_tools)
        run = run_goal(
            GoalSpec(
                objective=objective,
                constraints=(
                    "Use required tools instead of fabricating their results.",
                    "After tool results are available, reply with compact JSON.",
                ),
                success_criteria=(
                    "Every required tool has a successful ToolExecution record.",
                    "The session completes after tool results are returned.",
                ),
                available_tools=tool_names,
                required_tools=required_tools,
            ),
            provider_name=args.provider,
            env_file=args.env_file,
            live_tools=bool(args.live_tools),
            emitter=emitter,
            planner_name=args.planner,
            artifact_root=args.artifact_root,
        )
        session = (
            await asyncio.wait_for(run, timeout=args.timeout_seconds)
            if args.timeout_seconds is not None
            else await run
        )
        evidence = _goal_scenario_evidence(
            session=session,
            events=observer.events,
            required_tools=required_tools,
            selected_tools=tool_names,
            trace_path=trace_path,
            provider_name=str(args.provider),
            planner_name=str(args.planner),
            live_tools=bool(args.live_tools),
        )
        print(json.dumps(evidence, ensure_ascii=False, default=str))
        return 0 if evidence["ok"] else 1
    except Exception as exc:
        payload = {
            "ok": False,
            "reason": "exception",
            "provider": str(args.provider),
            "planner": str(args.planner),
            "trace_path": str(trace_path),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 1


def _goal_scenario_objective(
    objective: str | None,
    required_tools: tuple[str, ...],
) -> str:
    if objective:
        return objective
    return (
        "Run a HydraMind goal scenario. Call each required tool exactly once "
        f"({', '.join(required_tools)}), wait for the tool results, then return "
        "a compact JSON object with scenario_complete=true."
    )


def _goal_scenario_evidence(
    *,
    session: RuntimeSession,
    events: list[ObservationEvent],
    required_tools: tuple[str, ...],
    selected_tools: tuple[str, ...],
    trace_path: Path,
    provider_name: str,
    planner_name: str,
    live_tools: bool,
) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.kind.value] = event_counts.get(event.kind.value, 0) + 1
    ledger = tool_execution_ledger(session)
    required = [
        required_tool_evidence(tool_name, ledger)
        for tool_name in required_tools
    ]
    model_completed = event_counts.get(
        ObservationEventKind.MODEL_INVOKE_COMPLETED.value,
        0,
    )
    tool_completed = event_counts.get(ObservationEventKind.TOOL_CALL_COMPLETED.value, 0)
    plan = session.metadata.get("execution_plan")
    planner_metadata: dict[str, Any] = {}
    if isinstance(plan, dict):
        raw_metadata = plan.get("metadata")
        if isinstance(raw_metadata, dict):
            planner_metadata = dict(raw_metadata)

    reason = "passed"
    if not trace_path.exists():
        reason = "trace_missing"
    elif any(not item["succeeded"] for item in required):
        reason = "missing_required_tool"
    elif session.status.value != "completed":
        reason = "session_not_completed"
    elif tool_completed < len(required_tools):
        reason = "tool_result_missing"
    elif model_completed < 2:
        reason = "final_model_missing"
    return {
        "ok": reason == "passed",
        "reason": reason,
        "provider": provider_name,
        "planner": planner_name,
        "planner_metadata": planner_metadata,
        "live_tools": live_tools,
        "session_id": session.id,
        "session_status": session.status.value,
        "workflow": session.workflow_name,
        "tools": selected_tools,
        "required_tools": required,
        "tool_executions": ledger,
        "trace_path": str(trace_path),
        "event_counts": dict(sorted(event_counts.items())),
        "model_invoke_completed": model_completed,
        "tool_call_started": event_counts.get(
            ObservationEventKind.TOOL_CALL_STARTED.value,
            0,
        ),
        "tool_call_completed": tool_completed,
    }


__all__ = ["run_doctor_goal_scenario"]
