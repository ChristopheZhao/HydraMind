"""Run and queue execution helpers behind the public runtime facade."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hydramind import runtime_queue, runtime_queue_goal
from hydramind.control import (
    GoalArtifactQualityContract,
    RuntimeSession,
    SessionStore,
)
from hydramind.harness import ModelProvider
from hydramind.memory import MemoryStore
from hydramind.observability import Emitter
from hydramind.orchestration import GoalSpec
from hydramind.orchestration.agent import ExecutionHarnessFactory
from hydramind.queue import QueueAdapter, QueueMessage
from hydramind.runtime_bundle import (
    GoalRuntimeBundle,
    RuntimeBundle,
    _coerce_goal_spec,
)
from hydramind.runtime_bundle import (
    build_goal_runtime_bundle as _default_goal_runtime_bundle_builder,
)
from hydramind.runtime_bundle import (
    build_runtime_bundle as _default_runtime_bundle_builder,
)
from hydramind.runtime_worker import (
    WorkerHealthSnapshot,
    WorkerLoopResult,
    WorkerReadinessSnapshot,
    WorkerRunResult,
)

BuildGoalRuntimeBundle = Callable[..., GoalRuntimeBundle]
BuildRuntimeBundle = Callable[..., RuntimeBundle]

_goal_runtime_bundle_builder: BuildGoalRuntimeBundle = (
    _default_goal_runtime_bundle_builder
)
_runtime_bundle_builder: BuildRuntimeBundle = _default_runtime_bundle_builder


def set_runtime_bundle_builders(
    *,
    goal: BuildGoalRuntimeBundle,
    workflow: BuildRuntimeBundle,
) -> None:
    """Set bundle builders used by execution facade functions."""

    global _goal_runtime_bundle_builder, _runtime_bundle_builder
    _goal_runtime_bundle_builder = goal
    _runtime_bundle_builder = workflow


async def run_workflow_file(
    workflow_path: str | Path,
    *,
    input_payload: dict[str, object] | None = None,
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
) -> RuntimeSession:
    bundle = _runtime_bundle_builder(
        workflow_path,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
        artifact_root=artifact_root,
        execution_harness_factory=execution_harness_factory,
    )
    session = await bundle.agent.start_session(input_payload=input_payload or {})
    await bundle.agent.run_session(session.id)
    return await bundle.service.get_session(session.id)


async def run_goal(
    goal: str | GoalSpec,
    *,
    input_payload: dict[str, object] | None = None,
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
    quality_contract: GoalArtifactQualityContract | None = None,
    enable_episodic_memory: bool = False,
    enable_agent_memory: bool = False,
    memory_store: MemoryStore | None = None,
    memory_store_kind: str | None = None,
    memory_store_path: str | Path | None = None,
    max_tool_rounds: int | None = None,
    max_auto_repairs: int | None = None,
) -> RuntimeSession:
    goal_spec = _coerce_goal_spec(goal, quality_contract)
    bundle = _goal_runtime_bundle_builder(
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
        planner_name=planner_name,
        artifact_root=artifact_root,
        approved_tools=approved_tools,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store=memory_store,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        max_tool_rounds=max_tool_rounds,
        max_auto_repairs=max_auto_repairs,
    )
    runtime_overrides = runtime_queue_goal.runtime_overrides_from_args(
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        max_tool_rounds=max_tool_rounds,
        max_auto_repairs=max_auto_repairs,
    )
    return await bundle.agent.run_goal(
        goal_spec,
        input_payload=input_payload or {},
        runtime_overrides=runtime_overrides,
    )


async def create_queued_goal_session(
    goal: str | GoalSpec,
    *,
    input_payload: dict[str, object] | None = None,
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
    quality_contract: GoalArtifactQualityContract | None = None,
    enable_episodic_memory: bool = False,
    enable_agent_memory: bool = False,
    memory_store_kind: str | None = None,
    memory_store_path: str | Path | None = None,
    max_tool_rounds: int | None = None,
    max_auto_repairs: int | None = None,
) -> RuntimeSession:
    goal_spec = _coerce_goal_spec(goal, quality_contract)
    return await runtime_queue.create_queued_goal_session(
        goal_spec,
        build_goal_runtime_bundle=_goal_runtime_bundle_builder,
        runtime_overrides=runtime_queue_goal.runtime_overrides_from_args(
            enable_episodic_memory=enable_episodic_memory,
            enable_agent_memory=enable_agent_memory,
            memory_store_kind=memory_store_kind,
            memory_store_path=memory_store_path,
            max_tool_rounds=max_tool_rounds,
            max_auto_repairs=max_auto_repairs,
        ),
        input_payload=input_payload,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
        planner_name=planner_name,
        artifact_root=artifact_root,
        approved_tools=approved_tools,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        max_tool_rounds=max_tool_rounds,
        max_auto_repairs=max_auto_repairs,
    )


async def run_queued_goal_session_once(
    *,
    session_id: str,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    emitter: Emitter | None = None,
    planner_name: str = "auto",
    artifact_root: str | Path | None = None,
    approved_tools: tuple[str, ...] = (),
    allowed_process_commands: tuple[str, ...] = (),
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...] = (),
    enable_episodic_memory: bool = False,
    enable_agent_memory: bool = False,
    memory_store_kind: str | None = None,
    memory_store_path: str | Path | None = None,
    max_tool_rounds: int | None = None,
    max_auto_repairs: int | None = None,
) -> WorkerRunResult:
    return await runtime_queue.run_queued_goal_session_once(
        session_id=session_id,
        build_goal_runtime_bundle=_goal_runtime_bundle_builder,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        timeout=timeout,
        emitter=emitter,
        planner_name=planner_name,
        artifact_root=artifact_root,
        approved_tools=approved_tools,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        max_tool_rounds=max_tool_rounds,
        max_auto_repairs=max_auto_repairs,
    )


async def run_queued_goal_session_loop(
    *,
    session_ids: tuple[str, ...],
    queue_adapter: QueueAdapter | None = None,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    retry_on_error: bool = True,
    emitter: Emitter | None = None,
    planner_name: str = "auto",
    artifact_root: str | Path | None = None,
    approved_tools: tuple[str, ...] = (),
    allowed_process_commands: tuple[str, ...] = (),
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...] = (),
    enable_episodic_memory: bool = False,
    enable_agent_memory: bool = False,
    memory_store_kind: str | None = None,
    memory_store_path: str | Path | None = None,
    max_tool_rounds: int | None = None,
    max_auto_repairs: int | None = None,
    max_iterations: int | None = None,
    max_idle_cycles: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    return await runtime_queue.run_queued_goal_session_loop(
        session_ids=session_ids,
        build_goal_runtime_bundle=_goal_runtime_bundle_builder,
        queue_adapter=queue_adapter,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        timeout=timeout,
        retry_on_error=retry_on_error,
        emitter=emitter,
        planner_name=planner_name,
        artifact_root=artifact_root,
        approved_tools=approved_tools,
        allowed_process_commands=allowed_process_commands,
        allowed_process_argv_prefixes=allowed_process_argv_prefixes,
        enable_episodic_memory=enable_episodic_memory,
        enable_agent_memory=enable_agent_memory,
        memory_store_kind=memory_store_kind,
        memory_store_path=memory_store_path,
        max_tool_rounds=max_tool_rounds,
        max_auto_repairs=max_auto_repairs,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def create_queued_session(
    workflow_path: str | Path,
    *,
    input_payload: dict[str, object] | None = None,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    emitter: Emitter | None = None,
) -> RuntimeSession:
    return await runtime_queue.create_queued_session(
        workflow_path,
        build_runtime_bundle=_runtime_bundle_builder,
        input_payload=input_payload,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
    )


async def run_queued_session_once(
    workflow_path: str | Path,
    *,
    session_id: str,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    emitter: Emitter | None = None,
) -> WorkerRunResult:
    return await runtime_queue.run_queued_session_once(
        workflow_path,
        session_id=session_id,
        build_runtime_bundle=_runtime_bundle_builder,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        timeout=timeout,
        emitter=emitter,
    )


async def run_queued_session_loop(
    workflow_path: str | Path,
    *,
    session_ids: tuple[str, ...],
    queue_adapter: QueueAdapter | None = None,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    retry_on_error: bool = True,
    emitter: Emitter | None = None,
    max_iterations: int | None = None,
    max_idle_cycles: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    return await runtime_queue.run_queued_session_loop(
        workflow_path,
        session_ids=session_ids,
        build_runtime_bundle=_runtime_bundle_builder,
        queue_adapter=queue_adapter,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        timeout=timeout,
        retry_on_error=retry_on_error,
        emitter=emitter,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def queue_health(
    *,
    queue_adapter: QueueAdapter,
    worker_id: str = "queue-health",
) -> WorkerHealthSnapshot:
    return await runtime_queue.queue_health(
        queue_adapter,
        worker_id=worker_id,
    )


def worker_readiness(
    *,
    queue_adapter: QueueAdapter,
    worker_id: str = "worker-readiness",
    session_store_kind: str = "sqlite",
    store_path: str | Path | None = None,
) -> WorkerReadinessSnapshot:
    return runtime_queue.worker_readiness(
        queue_adapter,
        worker_id=worker_id,
        session_store_kind=session_store_kind,
        store_path=store_path,
    )


async def queue_dead_letters(
    *,
    queue_adapter: QueueAdapter,
    limit: int | None = None,
) -> tuple[QueueMessage, ...]:
    return await runtime_queue.queue_dead_letters(
        queue_adapter,
        limit=limit,
    )


async def replay_queue_dead_letters(
    *,
    queue_adapter: QueueAdapter,
    limit: int,
    reset_attempt: bool = True,
    remove_from_dead_letters: bool = True,
    metadata: dict[str, object] | None = None,
) -> tuple[QueueMessage, ...]:
    return await runtime_queue.replay_queue_dead_letters(
        queue_adapter,
        limit=limit,
        reset_attempt=reset_attempt,
        remove_from_dead_letters=remove_from_dead_letters,
        metadata=metadata,
    )
