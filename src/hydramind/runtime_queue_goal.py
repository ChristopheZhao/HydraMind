"""Queued goal-session runtime reconstruction helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from hydramind.control import RuntimeDecision, RuntimeSession, SessionService, SessionStore
from hydramind.control.session_service import SessionNotFoundError
from hydramind.harness import ModelProvider
from hydramind.orchestration import GoalSpec
from hydramind.runtime_support import create_session_store, load_env_file

_logger = logging.getLogger("hydramind.runtime.queue_goal")


class GoalAgent(Protocol):
    async def start_goal(
        self,
        goal: GoalSpec,
        *,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, object] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> RuntimeSession: ...

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision: ...


class GoalRuntimeBundle(Protocol):
    @property
    def agent(self) -> GoalAgent: ...


GoalRuntimeBundleBuilder = Callable[..., GoalRuntimeBundle]


def build_goal_session_orchestrator(
    *,
    build_goal_runtime_bundle: GoalRuntimeBundleBuilder,
    provider: ModelProvider | None,
    provider_name: str | None,
    env_file: str | Path | None,
    live_tools: bool,
    session_store: SessionStore | None,
    session_store_kind: str,
    store_path: str | Path | None,
    emitter: Any | None,
    planner_name: str,
    artifact_root: str | Path | None,
    approved_tools: tuple[str, ...],
    allowed_process_commands: tuple[str, ...],
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...],
    enable_episodic_memory: bool,
    enable_agent_memory: bool,
    memory_store_kind: str | None,
    memory_store_path: str | Path | None,
    max_tool_rounds: int | None,
    max_auto_repairs: int | None,
) -> QueuedGoalSessionOrchestrator:
    if env_file is not None:
        load_env_file(env_file)
    store = session_store or create_session_store(session_store_kind, store_path)
    return QueuedGoalSessionOrchestrator(
        build_goal_runtime_bundle=build_goal_runtime_bundle,
        session_store=store,
        provider=provider,
        provider_name=provider_name,
        live_tools=live_tools,
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


class QueuedGoalSessionOrchestrator:
    def __init__(
        self,
        *,
        build_goal_runtime_bundle: GoalRuntimeBundleBuilder,
        session_store: SessionStore,
        provider: ModelProvider | None,
        provider_name: str | None,
        live_tools: bool,
        session_store_kind: str,
        store_path: str | Path | None,
        emitter: Any | None,
        planner_name: str,
        artifact_root: str | Path | None,
        approved_tools: tuple[str, ...],
        allowed_process_commands: tuple[str, ...],
        allowed_process_argv_prefixes: tuple[tuple[str, ...], ...],
        enable_episodic_memory: bool,
        enable_agent_memory: bool,
        memory_store_kind: str | None,
        memory_store_path: str | Path | None,
        max_tool_rounds: int | None,
        max_auto_repairs: int | None,
    ) -> None:
        self._build_goal_runtime_bundle = build_goal_runtime_bundle
        self._session_store = session_store
        self._provider = provider
        self._provider_name = provider_name
        self._live_tools = live_tools
        self._session_store_kind = session_store_kind
        self._store_path = store_path
        self._emitter = emitter
        self._planner_name = planner_name
        self._artifact_root = artifact_root
        self._approved_tools = approved_tools
        self._allowed_process_commands = allowed_process_commands
        self._allowed_process_argv_prefixes = allowed_process_argv_prefixes
        self._enable_episodic_memory = enable_episodic_memory
        self._enable_agent_memory = enable_agent_memory
        self._memory_store_kind = memory_store_kind
        self._memory_store_path = memory_store_path
        self._max_tool_rounds = max_tool_rounds
        self._max_auto_repairs = max_auto_repairs

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision:
        persisted_overrides = await _persisted_runtime_overrides(
            self._session_store,
            session_id,
        )
        effective_enable_episodic = self._enable_episodic_memory or bool(
            persisted_overrides.get("enable_episodic_memory", False)
        )
        effective_enable_agent = self._enable_agent_memory or bool(
            persisted_overrides.get("enable_agent_memory", False)
        )
        effective_memory_store_kind = (
            self._memory_store_kind
            if self._memory_store_kind is not None
            else coerce_str(persisted_overrides.get("memory_store_kind"))
        )
        effective_memory_store_path = (
            Path(self._memory_store_path)
            if self._memory_store_path is not None
            else coerce_path(persisted_overrides.get("memory_store_path"))
        )
        effective_max_tool_rounds = (
            self._max_tool_rounds
            if self._max_tool_rounds is not None
            else coerce_int(persisted_overrides.get("max_tool_rounds"))
        )
        effective_max_auto_repairs = (
            self._max_auto_repairs
            if self._max_auto_repairs is not None
            else coerce_int(persisted_overrides.get("max_auto_repairs"))
        )
        bundle = self._build_goal_runtime_bundle(
            provider=self._provider,
            provider_name=self._provider_name,
            env_file=None,
            live_tools=self._live_tools,
            session_store=self._session_store,
            session_store_kind=self._session_store_kind,
            store_path=self._store_path,
            emitter=self._emitter,
            planner_name=self._planner_name,
            artifact_root=self._artifact_root,
            approved_tools=self._approved_tools,
            allowed_process_commands=self._allowed_process_commands,
            allowed_process_argv_prefixes=self._allowed_process_argv_prefixes,
            enable_episodic_memory=effective_enable_episodic,
            enable_agent_memory=effective_enable_agent,
            memory_store_kind=effective_memory_store_kind,
            memory_store_path=effective_memory_store_path,
            max_tool_rounds=effective_max_tool_rounds,
            max_auto_repairs=effective_max_auto_repairs,
        )
        return await bundle.agent.run_session(
            session_id,
            execution_owner=execution_owner,
            lease_ttl_seconds=lease_ttl_seconds,
            lease_heartbeat_interval_seconds=lease_heartbeat_interval_seconds,
        )


def runtime_overrides_from_args(
    *,
    enable_episodic_memory: bool,
    enable_agent_memory: bool,
    memory_store_kind: str | None,
    memory_store_path: str | Path | None,
    max_tool_rounds: int | None,
    max_auto_repairs: int | None,
) -> dict[str, Any] | None:
    """Capture only non-default args so persisted overrides stay minimal."""

    overrides: dict[str, Any] = {}
    if enable_episodic_memory:
        overrides["enable_episodic_memory"] = True
    if enable_agent_memory:
        overrides["enable_agent_memory"] = True
    if memory_store_kind is not None:
        overrides["memory_store_kind"] = memory_store_kind
    if memory_store_path is not None:
        overrides["memory_store_path"] = str(memory_store_path)
    if max_tool_rounds is not None:
        overrides["max_tool_rounds"] = max_tool_rounds
    if max_auto_repairs is not None:
        overrides["max_auto_repairs"] = max_auto_repairs
    return overrides or None


def coerce_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def coerce_path(value: Any) -> Path | None:
    if isinstance(value, str) and value:
        return Path(value)
    return None


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


async def _persisted_runtime_overrides(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Load persisted ``runtime_overrides`` for a queued goal session.

    A genuinely missing session degrades to defaults with a warning (a queued
    worker may race session creation). Any other store failure propagates so
    the queue delivery is retried instead of silently running the goal with
    default runtime configuration.
    """
    try:
        session = await SessionService(store).get_session(session_id)
    except SessionNotFoundError:
        _logger.warning(
            "queued goal session %s not found while loading runtime overrides; "
            "falling back to defaults",
            session_id,
        )
        return {}
    raw = session.metadata.get("runtime_overrides")
    return raw if isinstance(raw, dict) else {}


__all__ = [
    "GoalRuntimeBundleBuilder",
    "QueuedGoalSessionOrchestrator",
    "build_goal_session_orchestrator",
    "runtime_overrides_from_args",
]
