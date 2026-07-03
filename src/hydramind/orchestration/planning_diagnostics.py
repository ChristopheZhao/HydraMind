"""Internal diagnostic helpers for model-backed planning."""

from __future__ import annotations

from typing import Any

from hydramind.orchestration.planning_contracts import ExecutionPlan, PlanDelta


def planner_diagnostics(
    *,
    operation: str,
    planner: str,
    max_invoke_retries: int,
    max_json_repairs: int,
) -> dict[str, Any]:
    return {
        "planner": planner,
        "operation": operation,
        "status": "succeeded",
        "invoke_attempts": 0,
        "retry_count": 0,
        "repair_count": 0,
        "max_invoke_retries": max_invoke_retries,
        "max_json_repairs": max_json_repairs,
        "phases": [],
    }


def append_diagnostic_phase(diagnostics: dict[str, Any], phase: str) -> None:
    raw_phases = diagnostics.get("phases")
    phases = raw_phases if isinstance(raw_phases, list) else []
    if phase not in phases:
        phases.append(phase)
    diagnostics["phases"] = phases


def finished_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "planner": str(diagnostics.get("planner") or "ModelGoalPlanner"),
        "operation": str(diagnostics.get("operation") or ""),
        "status": str(diagnostics.get("status") or "succeeded"),
        "invoke_attempts": int(diagnostics.get("invoke_attempts") or 0),
        "retry_count": int(diagnostics.get("retry_count") or 0),
        "repair_count": int(diagnostics.get("repair_count") or 0),
        "max_invoke_retries": int(diagnostics.get("max_invoke_retries") or 0),
        "max_json_repairs": int(diagnostics.get("max_json_repairs") or 0),
        "phases": list(diagnostics.get("phases") or ()),
    }


def with_planner_diagnostics(
    plan: ExecutionPlan,
    diagnostics: dict[str, Any],
) -> ExecutionPlan:
    metadata = {
        **plan.metadata,
        "planner_diagnostics": finished_diagnostics(diagnostics),
    }
    return plan.model_copy(update={"metadata": metadata})


def with_delta_diagnostics(
    delta: PlanDelta,
    diagnostics: dict[str, Any],
) -> PlanDelta:
    metadata = {
        **delta.metadata,
        "planner_diagnostics": finished_diagnostics(diagnostics),
    }
    return delta.model_copy(update={"metadata": metadata})


def planner_error_summary(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {text[:500]}"


__all__ = [
    "append_diagnostic_phase",
    "finished_diagnostics",
    "planner_diagnostics",
    "planner_error_summary",
    "with_delta_diagnostics",
    "with_planner_diagnostics",
]
