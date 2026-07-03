"""Queued runtime helpers behind the public ``hydramind.runtime`` API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from hydramind.control import (
    RuntimeDecision,
    RuntimeSession,
    SessionStore,
)
from hydramind.harness import ModelProvider
from hydramind.orchestration import GoalSpec
from hydramind.queue import (
    InMemoryQueueAdapter,
    QueueAdapter,
    QueueCapabilityError,
    QueueMessage,
)
from hydramind.runtime_queue_goal import (
    GoalRuntimeBundleBuilder,
    build_goal_session_orchestrator,
)
from hydramind.runtime_worker import (
    QueueExecutionHost,
    WorkerHealthSnapshot,
    WorkerLoopResult,
    WorkerReadinessSnapshot,
    WorkerRunResult,
)
from hydramind.runtime_worker import (
    worker_readiness as worker_readiness_snapshot,
)


class _WorkflowAgent(Protocol):
    async def start_session(
        self,
        *,
        input_payload: dict[str, Any] | None = None,
    ) -> RuntimeSession: ...


class _HealthOnlyOrchestrator:
    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision:
        del session_id
        del execution_owner
        del lease_ttl_seconds
        del lease_heartbeat_interval_seconds
        raise RuntimeError("queue health does not run sessions")


class _RuntimeBundle(Protocol):
    @property
    def agent(self) -> _WorkflowAgent: ...


RuntimeBundleBuilder = Callable[..., _RuntimeBundle]


async def create_queued_goal_session(
    goal: GoalSpec,
    *,
    build_goal_runtime_bundle: GoalRuntimeBundleBuilder,
    runtime_overrides: dict[str, Any] | None,
    input_payload: dict[str, Any] | None = None,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    emitter: Any | None = None,
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
) -> RuntimeSession:
    bundle = build_goal_runtime_bundle(
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
    return await bundle.agent.start_goal(
        goal,
        input_payload=input_payload or {},
        runtime_overrides=runtime_overrides,
    )


async def run_queued_goal_session_once(
    *,
    session_id: str,
    build_goal_runtime_bundle: GoalRuntimeBundleBuilder,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    emitter: Any | None = None,
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
    orchestrator = build_goal_session_orchestrator(
        build_goal_runtime_bundle=build_goal_runtime_bundle,
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
    return await run_once_with_transient_queue(
        session_id,
        orchestrator=orchestrator,
        timeout=timeout,
    )


async def run_queued_goal_session_loop(
    *,
    session_ids: tuple[str, ...],
    build_goal_runtime_bundle: GoalRuntimeBundleBuilder,
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
    emitter: Any | None = None,
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
    orchestrator = build_goal_session_orchestrator(
        build_goal_runtime_bundle=build_goal_runtime_bundle,
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
    if queue_adapter is not None:
        return await run_loop_with_queue_adapter(
            queue_adapter,
            orchestrator=orchestrator,
            timeout=timeout,
            retry_on_error=retry_on_error,
            max_iterations=max_iterations,
            max_idle_cycles=max_idle_cycles,
            stop_requested=stop_requested,
        )
    return await run_loop_with_transient_queue(
        session_ids,
        orchestrator=orchestrator,
        timeout=timeout,
        retry_on_error=retry_on_error,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def create_queued_session(
    workflow_path: str | Path,
    *,
    build_runtime_bundle: RuntimeBundleBuilder,
    input_payload: dict[str, Any] | None = None,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    emitter: Any | None = None,
) -> RuntimeSession:
    bundle = build_runtime_bundle(
        workflow_path,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
    )
    return await bundle.agent.start_session(input_payload=input_payload or {})


async def run_queued_session_once(
    workflow_path: str | Path,
    *,
    session_id: str,
    build_runtime_bundle: RuntimeBundleBuilder,
    provider: ModelProvider | None = None,
    provider_name: str | None = None,
    env_file: str | Path | None = ".env",
    live_tools: bool = False,
    session_store: SessionStore | None = None,
    session_store_kind: str = "memory",
    store_path: str | Path | None = None,
    timeout: float | None = None,
    emitter: Any | None = None,
) -> WorkerRunResult:
    bundle = build_runtime_bundle(
        workflow_path,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
    )
    return await run_once_with_transient_queue(
        session_id,
        orchestrator=bundle.agent,
        timeout=timeout,
    )


async def run_queued_session_loop(
    workflow_path: str | Path,
    *,
    session_ids: tuple[str, ...],
    build_runtime_bundle: RuntimeBundleBuilder,
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
    emitter: Any | None = None,
    max_iterations: int | None = None,
    max_idle_cycles: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    bundle = build_runtime_bundle(
        workflow_path,
        provider=provider,
        provider_name=provider_name,
        env_file=env_file,
        live_tools=live_tools,
        session_store=session_store,
        session_store_kind=session_store_kind,
        store_path=store_path,
        emitter=emitter,
    )
    if queue_adapter is not None:
        return await run_loop_with_queue_adapter(
            queue_adapter,
            orchestrator=bundle.agent,
            timeout=timeout,
            retry_on_error=retry_on_error,
            max_iterations=max_iterations,
            max_idle_cycles=max_idle_cycles,
            stop_requested=stop_requested,
        )
    return await run_loop_with_transient_queue(
        session_ids,
        orchestrator=bundle.agent,
        timeout=timeout,
        retry_on_error=retry_on_error,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def run_once_with_transient_queue(
    session_id: str,
    *,
    orchestrator: Any,
    timeout: float | None,
    retry_on_error: bool = True,
) -> WorkerRunResult:
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session_id)
    host = QueueExecutionHost(queue=queue, orchestrator=orchestrator)
    return await host.run_once(timeout=timeout, retry_on_error=retry_on_error)


async def run_loop_with_transient_queue(
    session_ids: tuple[str, ...],
    *,
    orchestrator: Any,
    timeout: float | None,
    retry_on_error: bool = True,
    max_iterations: int | None = None,
    max_idle_cycles: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    queue = InMemoryQueueAdapter()
    for session_id in session_ids:
        await queue.enqueue(session_id)
    host = QueueExecutionHost(queue=queue, orchestrator=orchestrator)
    return await host.run_loop(
        timeout=timeout,
        retry_on_error=retry_on_error,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def run_loop_with_queue_adapter(
    queue: QueueAdapter,
    *,
    orchestrator: Any,
    timeout: float | None,
    retry_on_error: bool = True,
    max_iterations: int | None = None,
    max_idle_cycles: int | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> WorkerLoopResult:
    host = QueueExecutionHost(queue=queue, orchestrator=orchestrator)
    return await host.run_loop(
        timeout=timeout,
        retry_on_error=retry_on_error,
        max_iterations=max_iterations,
        max_idle_cycles=max_idle_cycles,
        stop_requested=stop_requested,
    )


async def queue_health(
    queue: QueueAdapter,
    *,
    worker_id: str = "queue-health",
) -> WorkerHealthSnapshot:
    host = QueueExecutionHost(
        queue=queue,
        orchestrator=_HealthOnlyOrchestrator(),
        worker_id=worker_id,
    )
    return await host.health()


def worker_readiness(
    queue: QueueAdapter,
    *,
    worker_id: str = "worker-readiness",
    session_store_kind: str = "sqlite",
    store_path: str | Path | None = None,
) -> WorkerReadinessSnapshot:
    return worker_readiness_snapshot(
        queue,
        worker_id=worker_id,
        session_store_kind=session_store_kind,
        store_path=store_path,
    )


async def queue_dead_letters(
    queue: QueueAdapter,
    *,
    limit: int | None = None,
) -> tuple[QueueMessage, ...]:
    method = _required_queue_method(queue, "dead_letters")
    if limit is None:
        return tuple(await method())
    return tuple(await method(limit=limit))


async def replay_queue_dead_letters(
    queue: QueueAdapter,
    *,
    limit: int,
    reset_attempt: bool = True,
    remove_from_dead_letters: bool = True,
    metadata: dict[str, Any] | None = None,
) -> tuple[QueueMessage, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    method = _required_queue_method(queue, "replay_dead_letters")
    return tuple(
        await method(
            limit=limit,
            reset_attempt=reset_attempt,
            remove_from_dead_letters=remove_from_dead_letters,
            metadata=metadata,
        )
    )


def _required_queue_method(queue: QueueAdapter, method_name: str) -> Any:
    method = getattr(queue, method_name, None)
    if callable(method):
        return method
    raise QueueCapabilityError(
        f"{queue.name!r} queue does not support {method_name}; "
        "provide a queue adapter with dead-letter recovery methods."
    )
