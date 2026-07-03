"""Runtime bundle assembly for direct workflow and goal execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hydramind.control import (
    ControlPlane,
    GoalArtifactQualityContract,
    SessionService,
    SessionStore,
)
from hydramind.control.models import WorkflowBlueprint
from hydramind.harness import ModelProvider
from hydramind.memory import MemoryStore
from hydramind.observability import Emitter
from hydramind.orchestration import (
    GoalDrivenOrchestratorAgent,
    GoalSpec,
    MemoryContextRetriever,
    ModelGoalPlanner,
    OrchestratorAgent,
    PlannerProvider,
    PromptLibrary,
)
from hydramind.orchestration.agent import ExecutionHarnessFactory
from hydramind.runtime_control import (
    build_goal_control_runtime,
    build_workflow_control_runtime,
)
from hydramind.runtime_memory import build_goal_memory_runtime
from hydramind.runtime_support import (
    _create_provider,
    load_env_file,
    load_workflow_blueprint,
)
from hydramind.runtime_tools import (
    build_goal_tool_runtime,
    build_workflow_tool_runtime,
)
from hydramind.runtime_verification import (
    build_default_goal_verifier_runner as _build_default_goal_verifier_runner,
)

ProviderFactory = Callable[[str | None], ModelProvider]
_provider_factory: ProviderFactory = _create_provider


@dataclass(frozen=True)
class RuntimeBundle:
    """Assembled runtime dependencies for one workflow file."""

    workflow_path: Path
    blueprint: WorkflowBlueprint
    service: SessionService
    control: ControlPlane
    provider: ModelProvider
    emitter: Emitter | None
    agent: OrchestratorAgent


@dataclass(frozen=True)
class GoalRuntimeBundle:
    """Assembled runtime dependencies for goal-derived sessions."""

    service: SessionService
    control: ControlPlane
    provider: ModelProvider
    emitter: Emitter | None
    agent: GoalDrivenOrchestratorAgent
    memory_store: MemoryStore | None = None


def set_provider_factory(factory: ProviderFactory) -> None:
    """Set the provider factory used when callers pass ``provider_name`` only."""

    global _provider_factory
    _provider_factory = factory


def build_goal_runtime_bundle(
    *,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    emitter: Emitter | None = None,
    planner_name: str = "auto",
    artifact_root: str | Path | None = None,
    approved_tools: tuple[str, ...] = (),
    allowed_process_commands: tuple[str, ...] = (),
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...] = (),
    enable_episodic_memory: bool = False,
    enable_agent_memory: bool = False,
    memory_store: MemoryStore | None = None,
    memory_store_kind: str | None = None,
    memory_store_path: str | Path | None = None,
    memory_retriever: MemoryContextRetriever | None = None,
    max_tool_rounds: int | None = None,
    max_auto_repairs: int | None = None,
) -> GoalRuntimeBundle:
    if env_file is not None:
        load_env_file(env_file)
    memory_runtime = build_goal_memory_runtime(
        emitter=emitter,
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store=memory_store,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        memory_retriever=memory_retriever,
    )
    control_runtime = build_goal_control_runtime(
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=memory_runtime.emitter,
    )
    selected_provider = provider or _provider_factory(provider_name)
    tool_runtime = build_goal_tool_runtime(
        artifact_root=artifact_root,
        live_tools=live_tools,
        approved_tools=approved_tools,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
    )
    planner = _create_goal_planner(
        planner_name,
        selected_provider,
        memory_retriever=memory_runtime.memory_retriever,
    )
    verifier_runner = _build_default_goal_verifier_runner(
        artifact_root=tool_runtime.environment.artifact_root,
        provider=selected_provider,
    )
    agent_kwargs: dict[str, Any] = dict(
        provider=selected_provider,
        control=control_runtime.control,
        planner=planner,
        tool_provider=tool_runtime.tools,
        tool_runner=tool_runtime.tools,
        verifier_runner=verifier_runner,
        max_repair_attempts=max_auto_repairs if max_auto_repairs is not None else 1,
        emitter=memory_runtime.emitter,
        memory_retriever=memory_runtime.memory_retriever,
    )
    if max_tool_rounds is not None:
        agent_kwargs["max_tool_rounds"] = max_tool_rounds
    agent = GoalDrivenOrchestratorAgent(**agent_kwargs)
    return GoalRuntimeBundle(
        service=control_runtime.service,
        control=control_runtime.control,
        provider=selected_provider,
        emitter=memory_runtime.emitter,
        agent=agent,
        memory_store=memory_runtime.memory_store,
    )


def build_runtime_bundle(
    workflow_path: str | Path,
    *,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    emitter: Emitter | None = None,
    artifact_root: str | Path | None = None,
    execution_harness_factory: ExecutionHarnessFactory | None = None,
) -> RuntimeBundle:
    path = Path(workflow_path)
    if env_file is not None:
        load_env_file(env_file)
    blueprint = load_workflow_blueprint(path)
    prompts_path = path.with_name("prompts.yaml")
    prompts = PromptLibrary.from_yaml(prompts_path) if prompts_path.exists() else PromptLibrary()
    control_runtime = build_workflow_control_runtime(
        workflow_path=path,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
    )
    selected_provider = provider or _provider_factory(provider_name)
    tool_runtime = build_workflow_tool_runtime(
        blueprint=blueprint,
        workflow_path=path,
        artifact_root=artifact_root,
        live_tools=live_tools,
    )
    # Workflow nodes can declare a ``task_contract`` (expected_artifacts +
    # optional quality_contract) in YAML; wire the same safety/boundary verifier
    # stack the goal path uses so a workflow-declared artifact is verified
    # (TaskContract existence/path-safety + artifact-root containment). The
    # semantic verifier no-ops without a ``semantic_rubric`` so offline mock
    # runs stay deterministic (ADR-0008).
    verifier_runner = _build_default_goal_verifier_runner(
        artifact_root=tool_runtime.environment.artifact_root,
        provider=selected_provider,
    )
    agent = OrchestratorAgent(
        provider=selected_provider,
        control=control_runtime.control,
        workflow=blueprint,
        prompts=prompts,
        tool_provider=tool_runtime.tool_provider,
        tool_runner=tool_runtime.tools,
        verifier_runner=verifier_runner,
        emitter=emitter,
        execution_harness_factory=execution_harness_factory,
    )
    return RuntimeBundle(
        workflow_path=path,
        blueprint=blueprint,
        service=control_runtime.service,
        control=control_runtime.control,
        provider=selected_provider,
        emitter=emitter,
        agent=agent,
    )


def _coerce_goal_spec(
    goal: str | GoalSpec,
    quality_contract: GoalArtifactQualityContract | None,
) -> GoalSpec:
    goal_spec = goal if isinstance(goal, GoalSpec) else GoalSpec(objective=goal)
    if quality_contract is not None and goal_spec.quality_contract is None:
        goal_spec = goal_spec.model_copy(update={"quality_contract": quality_contract})
    return goal_spec


def _create_goal_planner(
    planner_name: str,
    provider: ModelProvider,
    *,
    memory_retriever: MemoryContextRetriever | None = None,
) -> PlannerProvider:
    normalized = planner_name.lower().replace("-", "_")
    if normalized in ("auto", "model"):
        return ModelGoalPlanner(provider, memory_retriever=memory_retriever)
    raise ValueError(f"unknown goal planner {planner_name!r}")
