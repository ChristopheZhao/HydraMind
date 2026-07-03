"""Built-in prompt templates for model-backed goal planning."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from hydramind.mas.capability import (
    EXECUTED_AGGREGATIONS,
    EXECUTED_ARBITRATIONS,
    EXECUTED_MODE_TOPOLOGY_PAIRS,
    EXECUTED_MODES,
    EXECUTED_TOPOLOGIES,
)
from hydramind.orchestration.memory_context import MemoryContext
from hydramind.orchestration.planning_contracts import (
    ExecutionPlan,
    GoalSpec,
    NodeExecutionMode,
)
from hydramind.orchestration.planning_payloads import (
    truncate_planner_response as _truncate_planner_response,
)


def _advertised(values: Iterable[StrEnum]) -> str:
    """Render an executed-capability set as a ``a|b`` choice string for the prompt.

    Derives the advertised collaboration envelope from the single
    ``hydramind.mas.capability.EXECUTED_*`` source of truth so the planner prompt
    can never advertise a value the runtime does not actually execute (DEV-27).
    """

    return "|".join(sorted(value.value for value in values))


def _advertised_protocol_pairs() -> str:
    """Render executed mode/topology pairs as ``mode/topology`` choices."""

    return "|".join(
        sorted(
            f"{mode.value}/{topology.value}"
            for mode, topology in EXECUTED_MODE_TOPOLOGY_PAIRS
        )
    )


_SUPPORTED_PROTOCOL_PAIRS = _advertised_protocol_pairs()

PLANNER_SYSTEM = (
    "Plan HydraMind goal execution. Return only a JSON object. "
    "Use only tools listed by the user. Use task keys that are stable, lowercase, "
    "and dependency-safe. Leave expected_artifacts empty unless the goal explicitly "
    "requires a file or artifact output. Treat required_tools as mandatory: assign "
    "each required tool to executable task work, and when feedback names a missing "
    "required tool, add or update task work for that tool without repeating completed "
    "required tools unless feedback asks for a retry. When emitting a team protocol, "
    "vote mode requires broadcast topology, vote aggregation, and majority "
    "arbitration; vote aggregation is only valid for vote mode; coordinator_summary "
    "requires coordinator topology and coordinator_id; delegation requires coordinator "
    "topology and coordinator_id. "
    f"Supported mode/topology pairs: {_SUPPORTED_PROTOCOL_PAIRS}."
)

_INITIAL_PLAN_RESPONSE_SHAPE: dict[str, Any] = {
    "name": "string",
    "rationale": "string",
    "tasks": [
        {
            "key": "string",
            "role": "planner|researcher|executor|reviewer|writer",
            "description": "string",
            "agent": {
                "id": "stable_agent_id",
                "role": "agent_role",
                "description": "string",
                "tools": ["tool.name"],
            },
            "team": {
                "id": "stable_team_id",
                "members": [
                    {
                        "id": "stable_member_id",
                        "role": "member_role",
                        "description": "string",
                        "tools": ["tool.name"],
                    }
                ],
                "protocol": {
                    "mode": _advertised(EXECUTED_MODES),
                    "topology": _advertised(EXECUTED_TOPOLOGIES),
                    "aggregation": _advertised(EXECUTED_AGGREGATIONS),
                    "arbitration": _advertised(EXECUTED_ARBITRATIONS),
                    "coordinator_id": "stable_member_id",
                },
            },
            "requires": ["prior_task_key"],
            "tools": ["tool.name"],
            "execution_mode": _advertised(NodeExecutionMode),
            "acceptance_criteria": ["criterion"],
            "expected_artifacts": ["artifact/path"],
            "negative_cases": ["case"],
            "verifier_refs": ["verifier"],
            "config": {},
        }
    ],
}

_PLAN_DELTA_RESPONSE_SHAPE: dict[str, Any] = {
    "rationale": "string",
    "feedback_refs": ["feedback item or evidence ref"],
    "add_tasks": [],
    "update_tasks": [],
    "remove_task_keys": [],
}


def render_initial_plan_prompt(
    goal: GoalSpec,
    *,
    memory_context: MemoryContext | None = None,
) -> str:
    payload: dict[str, Any] = {
        "request": "initial_plan",
        "goal": goal.model_dump(mode="json"),
        "required_response_shape": _INITIAL_PLAN_RESPONSE_SHAPE,
    }
    if memory_context is not None:
        payload["memory_context"] = memory_context.as_prompt_payload()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def render_revise_plan_prompt(
    goal: GoalSpec,
    current_plan: ExecutionPlan,
    feedback: tuple[str, ...],
    *,
    memory_context: MemoryContext | None = None,
) -> str:
    payload: dict[str, Any] = {
        "request": "revise_plan",
        "goal": goal.model_dump(mode="json"),
        "current_plan": current_plan.model_dump(mode="json"),
        "feedback": list(feedback),
        "required_response_shape": _PLAN_DELTA_RESPONSE_SHAPE,
    }
    if memory_context is not None:
        payload["memory_context"] = memory_context.as_prompt_payload()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def render_planner_json_repair_prompt(
    original_prompt: str,
    invalid_response: str,
    reason: str | None = None,
) -> str:
    return json.dumps(
        {
            "request": "planner_json_repair",
            "instruction": (
                "Convert the invalid planner response into one valid JSON "
                "object that satisfies original_request.required_response_shape. "
                "Return only the JSON object."
            ),
            "original_request": json.loads(original_prompt),
            "invalid_response": _truncate_planner_response(invalid_response),
            "validation_error": reason,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "PLANNER_SYSTEM",
    "render_initial_plan_prompt",
    "render_planner_json_repair_prompt",
    "render_revise_plan_prompt",
]
