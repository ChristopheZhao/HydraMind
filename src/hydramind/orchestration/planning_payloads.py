"""Model-planner JSON parsing and payload normalization helpers."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from hydramind.control import TaskContract
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.orchestration.planning_contracts import (
    ExecutionPlan,
    GoalSpec,
    PlanDelta,
    PlanTaskSpec,
    validate_requires,
    with_goal_expected_artifacts,
    with_goal_quality_contract,
)

PLANNER_REPAIR_MAX_CHARS = 12000


def strict_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ValueError("planner returned non-JSON content: empty response")
    last_error: json.JSONDecodeError | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        last_error = exc
    else:
        if not isinstance(payload, dict):
            raise ValueError("planner response must be a JSON object")
        return payload
    extracted = embedded_json_object(text)
    if extracted is not None:
        return extracted
    reason = last_error.msg if last_error is not None else "Expecting value"
    raise ValueError(f"planner returned non-JSON content: {reason}")


def embedded_json_object(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def truncate_planner_response(content: str) -> str:
    if len(content) <= PLANNER_REPAIR_MAX_CHARS:
        return content
    return content[:PLANNER_REPAIR_MAX_CHARS] + "\n...[truncated]"


def json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def plan_name(objective: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", objective.lower())
    slug = "-".join(words[:6]) or "goal"
    return f"goal-{slug}-{uuid.uuid4().hex[:8]}"


def execution_plan_from_payload(
    payload: dict[str, Any],
    goal: GoalSpec,
) -> ExecutionPlan:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("planner response must include a non-empty tasks list")
    tasks = tuple(task_from_payload(item, goal=goal) for item in raw_tasks)
    validate_unique_keys(tasks)
    validate_requires(tasks)
    return with_goal_quality_contract(
        with_goal_expected_artifacts(
            ExecutionPlan(
                name=str(payload.get("name") or plan_name(goal.objective)),
                goal=goal,
                tasks=tasks,
                rationale=str(payload.get("rationale") or "model planner"),
                metadata={"planner": "model"},
            )
        )
    )


def plan_delta_from_payload(
    payload: dict[str, Any],
    goal: GoalSpec,
    feedback: tuple[str, ...],
) -> PlanDelta:
    add_tasks = tuple(task_list_from_payload(payload.get("add_tasks"), goal=goal))
    update_tasks = tuple(
        task_list_from_payload(payload.get("update_tasks"), goal=goal)
    )
    remove_task_keys = string_tuple(payload.get("remove_task_keys"))
    feedback_refs = string_tuple(payload.get("feedback_refs")) or feedback
    return PlanDelta(
        add_tasks=add_tasks,
        update_tasks=update_tasks,
        remove_task_keys=remove_task_keys,
        rationale=str(payload.get("rationale") or "model planner delta"),
        feedback_refs=feedback_refs,
    )


def task_list_from_payload(
    raw: Any,
    *,
    goal: GoalSpec,
) -> tuple[PlanTaskSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("planner task list fields must be arrays")
    return tuple(task_from_payload(item, goal=goal) for item in raw)


def task_from_payload(raw: Any, *, goal: GoalSpec) -> PlanTaskSpec:
    if not isinstance(raw, dict):
        raise ValueError("planner tasks must be JSON objects")
    key = str(raw.get("key") or "").strip()
    if not key:
        raise ValueError("planner task key must not be empty")
    tools = string_tuple(raw.get("tools"))
    validate_tool_scope(tools, goal.available_tools, task_key=key)
    raw_contract = raw.get("contract")
    contract_payload: dict[str, Any] = (
        dict(raw_contract) if isinstance(raw_contract, dict) else {}
    )
    raw_contract_metadata = contract_payload.get("metadata")
    contract = TaskContract(
        objective=str(
            contract_payload.get("objective")
            or raw.get("objective")
            or raw.get("description")
            or goal.objective
        ),
        acceptance_criteria=string_tuple(
            contract_payload.get("acceptance_criteria")
            or raw.get("acceptance_criteria")
        ),
        expected_artifacts=string_tuple(
            contract_payload.get("expected_artifacts")
            or raw.get("expected_artifacts")
        ),
        negative_cases=string_tuple(
            contract_payload.get("negative_cases")
            or raw.get("negative_cases")
        ),
        verifier_refs=string_tuple(
            contract_payload.get("verifier_refs")
            or raw.get("verifier_refs")
        ),
        metadata=dict(raw_contract_metadata)
        if isinstance(raw_contract_metadata, dict)
        else {},
    )
    config = raw.get("config")
    agent = agent_from_payload(raw.get("agent"), task_key=key)
    team = team_from_payload(raw.get("team"), task_key=key)
    return PlanTaskSpec(
        key=key,
        role=str(raw.get("role") or "executor"),
        description=str(raw.get("description") or contract.objective),
        agent=agent,
        team=team,
        requires=string_tuple(raw.get("requires")),
        contract=contract,
        tools=tools,
        execution_mode=str(raw.get("execution_mode") or "direct"),
        config=dict(config) if isinstance(config, dict) else {},
    )


def validate_unique_keys(tasks: tuple[PlanTaskSpec, ...]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.key in seen:
            raise ValueError(f"planner produced duplicate task {task.key!r}")
        seen.add(task.key)


def validate_tool_scope(
    tools: tuple[str, ...],
    available_tools: tuple[str, ...],
    *,
    task_key: str,
) -> None:
    allowed = set(available_tools)
    unknown = [tool for tool in tools if tool not in allowed]
    if unknown:
        raise ValueError(
            f"task {task_key!r} uses undeclared tool(s): {unknown}"
        )


def agent_from_payload(raw: Any, *, task_key: str) -> AgentSpec | None:
    if raw is None:
        return None
    try:
        return AgentSpec.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"task {task_key!r} has invalid agent spec: {exc}") from exc


def team_from_payload(raw: Any, *, task_key: str) -> TeamSpec | None:
    if raw is None:
        return None
    try:
        return TeamSpec.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"task {task_key!r} has invalid team spec: {exc}") from exc


def string_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw)
    raise ValueError("planner list fields must be strings or arrays")
