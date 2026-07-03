"""Pure feedback-extraction mechanism for agent-driven repair.

These helpers are FORMATTING/MECHANISM only — they turn a verifier-failure
gate's recorded detail into feedback strings and report whether the gate is a
pure required-tool-routing case. They make NO repair *decision*: whether and
how to repair is the planner agent's ``revise_plan`` output (ADR-0008). The
deleted rule repair-policy matrix is intentionally NOT reconstructed here.
"""

from __future__ import annotations

from typing import Any

from hydramind.control import Gate, GateDecisionInput
from hydramind.orchestration.planning import PlanDelta


def feedback_for_replan(
    gate: Gate,
    payload: dict[str, Any],
) -> tuple[str, ...]:
    feedback: list[str] = []
    _extend_feedback(feedback, payload.get("feedback"))
    _extend_feedback(feedback, payload.get("feedback_refs"))
    detail = gate.detail
    _extend_feedback_records(feedback, detail.get("feedback"))
    _extend_failed_verifiers(feedback, detail.get("failed_verifiers"))
    if not feedback:
        feedback.append(f"gate {gate.name} requested replan")
    return _dedupe(feedback)


def should_approve_current_after_replan(
    decision: GateDecisionInput,
    gate: Gate,
    delta: PlanDelta,
) -> bool:
    if decision.payload.get("approve_current_after_replan") is not True:
        return False
    if not gate_has_only_required_tool_feedback(gate):
        return False
    if not (delta.add_tasks or delta.update_tasks or delta.remove_task_keys):
        return False
    if gate.node_key in delta.remove_task_keys:
        return False
    return all(task.key != gate.node_key for task in delta.update_tasks)


def gate_has_failed_verifiers(gate: Gate) -> bool:
    failed = gate.detail.get("failed_verifiers")
    return isinstance(failed, list) and bool(failed)


def gate_has_only_required_tool_feedback(gate: Gate) -> bool:
    failed = gate.detail.get("failed_verifiers")
    if not isinstance(failed, list) or not failed:
        return False
    return all(
        isinstance(item, dict)
        and item.get("name") == "required_tools.completed"
        for item in failed
    )


def _extend_feedback(items: list[str], raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, str):
        items.append(raw)
        return
    if isinstance(raw, list | tuple):
        for item in raw:
            if isinstance(item, str):
                items.append(item)


def _extend_feedback_records(items: list[str], raw: Any) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if isinstance(message, str) and message:
            source = item.get("source")
            prefix = f"{source}: " if isinstance(source, str) and source else ""
            items.append(f"{prefix}{message}")


def _extend_failed_verifiers(items: list[str], raw: Any) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        repair = item.get("repair_instruction")
        if isinstance(repair, str) and repair:
            items.append(repair)


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return tuple(out)
