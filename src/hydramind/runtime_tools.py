"""Runtime-edge assembly for tool execution dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hydramind.control.models import WorkflowBlueprint
from hydramind.orchestration import ToolProvider
from hydramind.runtime_support import WorkflowToolProvider
from hydramind.tools import (
    ExecutionEnvironment,
    ToolRegistry,
    build_default_tool_registry,
    default_external_tool_hosts,
)


@dataclass(frozen=True)
class GoalToolRuntime:
    """Tool dependencies for a goal-derived runtime bundle."""

    environment: ExecutionEnvironment
    tools: ToolRegistry


@dataclass(frozen=True)
class WorkflowToolRuntime:
    """Tool dependencies for a workflow-file runtime bundle."""

    environment: ExecutionEnvironment
    tools: ToolRegistry
    tool_provider: ToolProvider


def build_goal_tool_runtime(
    *,
    artifact_root: str | Path | None,
    live_tools: bool,
    approved_tools: tuple[str, ...],
    allowed_process_commands: tuple[str, ...],
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...],
) -> GoalToolRuntime:
    """Assemble the default goal tool environment and registry."""

    environment = ExecutionEnvironment(
        artifact_root=(
            Path(artifact_root)
            if artifact_root is not None
            else Path("artifacts") / "goals"
        ),
        dry_run=not live_tools,
        approved_tools=approved_tools,
        network_access=live_tools,
        allowed_network_hosts=default_external_tool_hosts(dict(os.environ)),
        tool_timeout_seconds=120.0,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
    )
    return GoalToolRuntime(
        environment=environment,
        tools=build_default_tool_registry(context=environment.to_tool_context()),
    )


def build_workflow_tool_runtime(
    *,
    blueprint: WorkflowBlueprint,
    workflow_path: str | Path,
    artifact_root: str | Path | None,
    live_tools: bool,
) -> WorkflowToolRuntime:
    """Assemble the default workflow tool environment and scoped provider."""

    path = Path(workflow_path)
    environment = ExecutionEnvironment(
        artifact_root=(
            Path(artifact_root) if artifact_root is not None else path.parent / "artifacts"
        ),
        dry_run=not live_tools,
        network_access=live_tools,
        allowed_network_hosts=default_external_tool_hosts(dict(os.environ)),
        tool_timeout_seconds=120.0,
    )
    tools = build_default_tool_registry(context=environment.to_tool_context())
    return WorkflowToolRuntime(
        environment=environment,
        tools=tools,
        tool_provider=WorkflowToolProvider(blueprint, tools),
    )
