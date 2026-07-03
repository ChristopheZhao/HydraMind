"""Runtime assembly helpers used by the CLI and examples."""

from __future__ import annotations

from functools import wraps
from typing import Any

from hydramind import runtime_bundle as _runtime_bundle
from hydramind import runtime_execution as _runtime_execution
from hydramind.control import RuntimeSession
from hydramind.queue import QueueMessage
from hydramind.runtime_bundle import (
    GoalRuntimeBundle,
    RuntimeBundle,
)
from hydramind.runtime_support import (
    _create_provider,
    create_memory_store,
    create_provider,
    create_queue_adapter,
    create_session_store,
    load_env_file,
    load_gate_registry,
    load_workflow_blueprint,
    register_memory_store,
    registered_memory_store_kinds,
    reset_memory_store_registry,
)
from hydramind.runtime_worker import (
    WorkerHealthSnapshot,
    WorkerLoopExitContract,
    WorkerLoopResult,
    WorkerReadinessSnapshot,
    WorkerRunResult,
    worker_loop_exit_contract,
)

__all__ = [
    "GoalRuntimeBundle",
    "RuntimeBundle",
    "WorkerLoopExitContract",
    "WorkerReadinessSnapshot",
    "build_goal_runtime_bundle",
    "build_runtime_bundle",
    "create_memory_store",
    "create_provider",
    "create_queue_adapter",
    "create_queued_goal_session",
    "create_queued_session",
    "create_session_store",
    "load_env_file",
    "load_gate_registry",
    "load_workflow_blueprint",
    "queue_dead_letters",
    "queue_health",
    "register_memory_store",
    "registered_memory_store_kinds",
    "replay_queue_dead_letters",
    "reset_memory_store_registry",
    "run_goal",
    "run_queued_goal_session_loop",
    "run_queued_goal_session_once",
    "run_queued_session_loop",
    "run_queued_session_once",
    "run_workflow_file",
    "worker_loop_exit_contract",
    "worker_readiness",
]


@wraps(_runtime_bundle.build_goal_runtime_bundle)
def build_goal_runtime_bundle(*args: Any, **kwargs: Any) -> GoalRuntimeBundle:
    _runtime_bundle.set_provider_factory(_create_provider)
    return _runtime_bundle.build_goal_runtime_bundle(*args, **kwargs)


@wraps(_runtime_bundle.build_runtime_bundle)
def build_runtime_bundle(*args: Any, **kwargs: Any) -> RuntimeBundle:
    _runtime_bundle.set_provider_factory(_create_provider)
    return _runtime_bundle.build_runtime_bundle(*args, **kwargs)


def _sync_execution_dependencies() -> None:
    _runtime_bundle.set_provider_factory(_create_provider)
    _runtime_execution.set_runtime_bundle_builders(
        goal=build_goal_runtime_bundle,
        workflow=build_runtime_bundle,
    )


@wraps(_runtime_execution.run_workflow_file)
async def run_workflow_file(*args: Any, **kwargs: Any) -> RuntimeSession:
    _sync_execution_dependencies()
    return await _runtime_execution.run_workflow_file(*args, **kwargs)


@wraps(_runtime_execution.run_goal)
async def run_goal(*args: Any, **kwargs: Any) -> RuntimeSession:
    _sync_execution_dependencies()
    return await _runtime_execution.run_goal(*args, **kwargs)


@wraps(_runtime_execution.create_queued_goal_session)
async def create_queued_goal_session(*args: Any, **kwargs: Any) -> RuntimeSession:
    _sync_execution_dependencies()
    return await _runtime_execution.create_queued_goal_session(*args, **kwargs)


@wraps(_runtime_execution.run_queued_goal_session_once)
async def run_queued_goal_session_once(*args: Any, **kwargs: Any) -> WorkerRunResult:
    _sync_execution_dependencies()
    return await _runtime_execution.run_queued_goal_session_once(*args, **kwargs)


@wraps(_runtime_execution.run_queued_goal_session_loop)
async def run_queued_goal_session_loop(*args: Any, **kwargs: Any) -> WorkerLoopResult:
    _sync_execution_dependencies()
    return await _runtime_execution.run_queued_goal_session_loop(*args, **kwargs)


@wraps(_runtime_execution.create_queued_session)
async def create_queued_session(*args: Any, **kwargs: Any) -> RuntimeSession:
    _sync_execution_dependencies()
    return await _runtime_execution.create_queued_session(*args, **kwargs)


@wraps(_runtime_execution.run_queued_session_once)
async def run_queued_session_once(*args: Any, **kwargs: Any) -> WorkerRunResult:
    _sync_execution_dependencies()
    return await _runtime_execution.run_queued_session_once(*args, **kwargs)


@wraps(_runtime_execution.run_queued_session_loop)
async def run_queued_session_loop(*args: Any, **kwargs: Any) -> WorkerLoopResult:
    _sync_execution_dependencies()
    return await _runtime_execution.run_queued_session_loop(*args, **kwargs)


@wraps(_runtime_execution.queue_health)
async def queue_health(*args: Any, **kwargs: Any) -> WorkerHealthSnapshot:
    _sync_execution_dependencies()
    return await _runtime_execution.queue_health(*args, **kwargs)


@wraps(_runtime_execution.worker_readiness)
def worker_readiness(*args: Any, **kwargs: Any) -> WorkerReadinessSnapshot:
    _sync_execution_dependencies()
    return _runtime_execution.worker_readiness(*args, **kwargs)


@wraps(_runtime_execution.queue_dead_letters)
async def queue_dead_letters(*args: Any, **kwargs: Any) -> tuple[QueueMessage, ...]:
    _sync_execution_dependencies()
    return await _runtime_execution.queue_dead_letters(*args, **kwargs)


@wraps(_runtime_execution.replay_queue_dead_letters)
async def replay_queue_dead_letters(*args: Any, **kwargs: Any) -> tuple[QueueMessage, ...]:
    _sync_execution_dependencies()
    return await _runtime_execution.replay_queue_dead_letters(*args, **kwargs)
