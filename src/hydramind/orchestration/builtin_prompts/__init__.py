"""Built-in prompt fallbacks for orchestration demos and tests."""

from hydramind.orchestration.builtin_prompts.defaults import render_default_node_prompt
from hydramind.orchestration.builtin_prompts.planner import (
    PLANNER_SYSTEM,
    render_initial_plan_prompt,
    render_planner_json_repair_prompt,
    render_revise_plan_prompt,
)
from hydramind.orchestration.builtin_prompts.semantic_verifier import (
    SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM,
    SEMANTIC_VERIFIER_SYSTEM,
    render_semantic_verifier_json_repair_prompt,
    render_semantic_verifier_prompt,
)

__all__ = [
    "PLANNER_SYSTEM",
    "SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM",
    "SEMANTIC_VERIFIER_SYSTEM",
    "render_default_node_prompt",
    "render_initial_plan_prompt",
    "render_planner_json_repair_prompt",
    "render_revise_plan_prompt",
    "render_semantic_verifier_json_repair_prompt",
    "render_semantic_verifier_prompt",
]
