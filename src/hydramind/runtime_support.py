"""Support loaders and factories for runtime assembly."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from hydramind.control import (
    InMemorySessionStore,
    SessionStore,
    SqliteSessionStore,
)
from hydramind.control.models import WorkflowBlueprint, WorkflowNodeSpec
from hydramind.gating import GateRegistry
from hydramind.harness import (
    ModelProvider,
    ToolSpec,
    create_model_provider_from_env,
)
from hydramind.memory import (
    InMemoryMemoryStore,
    MemoryStore,
    SqliteMemoryStore,
)
from hydramind.orchestration import ToolProvider
from hydramind.queue import (
    InMemoryQueueAdapter,
    QueueAdapter,
    RedisStreamQueueAdapter,
)

MemoryStoreBuilder = Callable[[str | Path | None], MemoryStore]

_MEMORY_STORE_BUILDERS: dict[str, MemoryStoreBuilder] = {}


def create_session_store(kind: str, path: str | Path | None = None) -> SessionStore:
    normalized = kind.lower().replace("-", "_")
    if normalized in {"memory", "in_memory"}:
        return InMemorySessionStore()
    if normalized == "sqlite":
        if path is None:
            raise ValueError("sqlite session store requires --store-path")
        return SqliteSessionStore(path)
    raise ValueError(f"unknown session store kind {kind!r}")


def create_memory_store(kind: str, path: str | Path | None = None) -> MemoryStore:
    normalized = _normalize_memory_store_kind(kind)
    builder = _MEMORY_STORE_BUILDERS.get(normalized)
    if builder is not None:
        return builder(path)
    raise ValueError(f"unknown memory store kind {kind!r}")


def register_memory_store(
    kind: str,
    builder: MemoryStoreBuilder,
    *,
    replace: bool = False,
) -> None:
    """Register a process-local memory-store builder for runtime assembly."""

    normalized = _normalize_memory_store_kind(kind)
    if normalized in _MEMORY_STORE_BUILDERS and not replace:
        raise ValueError(f"memory store kind {kind!r} is already registered")
    _MEMORY_STORE_BUILDERS[normalized] = builder


def registered_memory_store_kinds() -> tuple[str, ...]:
    """Return registered memory-store kinds in deterministic order."""

    return tuple(sorted(_MEMORY_STORE_BUILDERS))


def reset_memory_store_registry() -> None:
    """Restore the built-in process-local memory-store registry."""

    _MEMORY_STORE_BUILDERS.clear()
    register_memory_store("memory", _build_in_memory_memory_store)
    register_memory_store("in_memory", _build_in_memory_memory_store)
    register_memory_store("sqlite", _build_sqlite_memory_store)


def _normalize_memory_store_kind(kind: str) -> str:
    return kind.lower().replace("-", "_")


def _build_in_memory_memory_store(path: str | Path | None = None) -> MemoryStore:
    if path is not None:
        raise ValueError("in-memory memory store does not accept a path")
    return InMemoryMemoryStore()


def _build_sqlite_memory_store(path: str | Path | None = None) -> MemoryStore:
    if path is None:
        raise ValueError("sqlite memory store requires --memory-store-path")
    return SqliteMemoryStore(path)


def create_queue_adapter(
    kind: str,
    *,
    redis_url: str | None = None,
    stream_key: str = "hydramind:sessions",
    group_name: str = "hydramind-workers",
    consumer_name: str = "hydramind-worker",
    visibility_timeout_seconds: float | None = 60.0,
    max_delivery_attempts: int | None = None,
) -> QueueAdapter:
    normalized = kind.lower().replace("-", "_")
    if normalized in {"memory", "in_memory"}:
        return InMemoryQueueAdapter()
    if normalized in {"redis", "redis_stream"}:
        if redis_url is None:
            raise ValueError("redis queue adapter requires --queue-redis-url")
        return RedisStreamQueueAdapter(
            url=redis_url,
            stream_key=stream_key,
            group_name=group_name,
            consumer_name=consumer_name,
            visibility_timeout_seconds=visibility_timeout_seconds,
            max_delivery_attempts=max_delivery_attempts,
        )
    raise ValueError(f"unknown queue adapter kind {kind!r}")


def create_provider(provider_name: str | None) -> ModelProvider:
    return _create_provider(provider_name)


def _create_provider(provider_name: str | None) -> ModelProvider:
    if provider_name is None or provider_name == "env":
        return create_model_provider_from_env()
    if provider_name == "mock":
        # Explicitly offline replay/testing affordance (NOT production model access).
        # Deterministic replay/test support lives in hydramind.testing with
        # non-agent semantics; the offline examples (S0 replay regressions) use
        # this `--provider mock` path. Imported lazily so the production provider
        # path never pulls in the testing namespace.
        from hydramind.testing import MockProvider

        return MockProvider()
    env = dict(os.environ)
    env["HYDRAMIND_PROVIDER"] = provider_name
    return create_model_provider_from_env(env)


def load_workflow_blueprint(path: str | Path) -> WorkflowBlueprint:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"workflow YAML at {path} must be a mapping")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        raise TypeError(f"workflow YAML at {path} must contain nodes: []")
    return WorkflowBlueprint(
        name=str(raw.get("name") or Path(path).stem),
        version=str(raw.get("version") or "1"),
        nodes=tuple(_node_from_dict(item) for item in nodes),
    )


def load_gate_registry(path: str | Path) -> GateRegistry | None:
    gate_path = Path(path)
    if not gate_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("_hydramind_example_gates", gate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load gate module {gate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "build_gate_registry", None)
    if factory is None:
        return None
    registry = factory()
    if not isinstance(registry, GateRegistry):
        raise TypeError(f"{gate_path} build_gate_registry() must return GateRegistry")
    return registry


def load_env_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _node_from_dict(raw: Any) -> WorkflowNodeSpec:
    if not isinstance(raw, dict):
        raise TypeError("workflow node must be a mapping")
    node_key = str(raw.get("key", "<missing>"))
    requires = raw.get("requires") or ()
    if not isinstance(requires, (list, tuple)):
        raise TypeError(f"node {raw.get('key')!r} requires must be a list")
    config = raw.get("config") or {}
    if not isinstance(config, dict):
        raise TypeError(f"node {raw.get('key')!r} config must be a mapping")
    normalized_config = dict(config)
    declared_tools = _workflow_node_declared_tools(
        raw=raw,
        config=normalized_config,
        node_key=node_key,
    )
    if declared_tools:
        normalized_config["tools"] = list(declared_tools)
    else:
        normalized_config.pop("tools", None)
    return WorkflowNodeSpec(
        key=str(raw["key"]),
        role=str(raw["role"]),
        description=str(raw.get("description") or ""),
        requires=tuple(str(x) for x in requires),
        config=normalized_config,
    )


def _workflow_node_declared_tools(
    *,
    raw: dict[str, Any],
    config: dict[str, Any],
    node_key: str,
) -> tuple[str, ...]:
    names: list[str] = []
    for owner, raw_names in (
        (f"workflow node {node_key!r} tools", raw.get("tools")),
        (f"workflow node {node_key!r} config.tools", config.get("tools")),
    ):
        for name in _normalize_tool_names(raw_names, owner=owner):
            if name not in names:
                names.append(name)
    return tuple(names)

def _normalize_tool_names(raw: Any, *, owner: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise TypeError(f"{owner} must be a list of tool names")
    names: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise TypeError(f"{owner}[{index}] must be a tool name string")
        name = item.strip()
        if not name:
            raise ValueError(f"{owner}[{index}] must not be empty")
        if name not in names:
            names.append(name)
    return tuple(names)


def _tools_from_node_config(config: dict[str, Any], node_key: str) -> tuple[str, ...]:
    return _normalize_tool_names(
        config.get("tools"),
        owner=f"workflow node {node_key!r} config.tools",
    )


class WorkflowToolProvider:
    """Expose only the tool names declared by each workflow YAML node."""

    def __init__(self, blueprint: WorkflowBlueprint, base: ToolProvider) -> None:
        self._base = base
        self._tools_by_node = {
            node.key: _tools_from_node_config(node.config, node.key)
            for node in blueprint.nodes
        }
        self._validate_declared_tools()

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        allowed = self._tools_by_node.get(node_key, ())
        if not allowed:
            return []
        specs_for = getattr(self._base, "specs_for", None)
        if callable(specs_for):
            specs = list(specs_for(allowed))
        else:
            allowed_names = set(allowed)
            specs = [
                spec
                for spec in self._base.tools_for(node_key)
                if spec.name in allowed_names
            ]
        returned_names = {spec.name for spec in specs}
        missing = [name for name in allowed if name not in returned_names]
        if missing:
            raise ValueError(
                f"workflow node {node_key!r} declares unavailable tool(s): "
                f"{', '.join(missing)}"
            )
        return specs

    def _validate_declared_tools(self) -> None:
        for node_key, names in self._tools_by_node.items():
            if names:
                self.tools_for(node_key)


reset_memory_store_registry()
