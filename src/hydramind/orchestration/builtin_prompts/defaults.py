"""Default prompt templates allowed by the prompts-as-config invariant."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_SYSTEM_TEMPLATE = (
    "You are the {agent_role} for node {node_key!r} in workflow {workflow_name!r}."
)


def render_default_node_prompt(
    *,
    agent_role: str,
    node_key: str,
    workflow_name: str,
    input_payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Render the minimal built-in prompt used when no YAML prompt is registered."""
    system = DEFAULT_SYSTEM_TEMPLATE.format(
        agent_role=agent_role,
        node_key=node_key,
        workflow_name=workflow_name,
    )
    if context:
        user = json.dumps(
            {"input": input_payload, "context": context},
            ensure_ascii=False,
            sort_keys=True,
        )
    else:
        user = json.dumps(input_payload) if input_payload else "go"
    return system, user
