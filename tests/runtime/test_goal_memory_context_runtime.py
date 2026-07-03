"""Goal-runtime memory-context wiring tests."""

from __future__ import annotations

import json

import pytest

from hydramind.memory import InMemoryMemoryStore, MemoryScope, SqliteMemoryStore
from hydramind.orchestration import (
    GoalSpec,
    MemoryContextPolicy,
    MemoryContextQuery,
)
from hydramind.runtime import (
    build_goal_runtime_bundle,
    create_memory_store,
    register_memory_store,
    registered_memory_store_kinds,
    reset_memory_store_registry,
    run_goal,
)
from hydramind.testing import MockProvider, ScriptedTurn


def _plan_turn() -> ScriptedTurn:
    return ScriptedTurn(
        content=json.dumps(
            {
                "name": "runtime-memory-goal",
                "tasks": [{"key": "write", "role": "writer"}],
            }
        )
    )


def _memory_goal() -> GoalSpec:
    return GoalSpec(
        objective="Use runtime memory context",
        memory_context=MemoryContextPolicy(
            enabled=True,
            queries=(
                MemoryContextQuery(
                    scope=MemoryScope.WORKFLOW,
                    scope_id="runtime-memory-goal",
                    key_prefix="episode.",
                    limit=1,
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_run_goal_wires_memory_store_to_planner_and_executor_prompts() -> None:
    store = InMemoryMemoryStore()
    await store.append(
        MemoryScope.WORKFLOW,
        "runtime-memory-goal",
        "episode.previous",
        {"lesson": "prefer evidence-backed summaries"},
    )
    backend = MockProvider(
        scripted=[
            _plan_turn(),
            ScriptedTurn(content='{"done": true}'),
        ]
    )

    session = await run_goal(
        _memory_goal(),
        provider=backend,
        env_file=None,
        planner_name="model",
        memory_store=store,
    )

    planner_prompt = json.loads(backend.invocations[0]["messages"][0].content)
    planner_entries = planner_prompt["memory_context"]["entries"]
    assert planner_entries[0]["value"] == {
        "lesson": "prefer evidence-backed summaries"
    }
    assert planner_entries[0]["evidence_ref"].startswith(
        "memory://workflow/runtime-memory-goal/"
    )

    executor_prompt = json.loads(backend.invocations[1]["messages"][0].content)
    executor_entries = executor_prompt["context"]["memory"]["entries"]
    assert executor_entries[0]["value"] == {
        "lesson": "prefer evidence-backed summaries"
    }
    assert executor_entries[0]["evidence_ref"].startswith(
        "memory://workflow/runtime-memory-goal/"
    )

    assert "entries" not in session.metadata["goal"]["memory_context"]
    assert "memory_context" not in session.metadata["execution_plan"]["metadata"]
    assert session.nodes["write"].latest_attempt().output == {"done": True}
    stored_entries = await store.scan(
        MemoryScope.WORKFLOW,
        "runtime-memory-goal",
        key_prefix="episode.",
    )
    assert len(stored_entries) == 1


@pytest.mark.asyncio
async def test_run_goal_memory_context_requires_runtime_memory_store() -> None:
    backend = MockProvider(
        scripted=[
            _plan_turn(),
            ScriptedTurn(content='{"done": true}'),
        ]
    )

    await run_goal(
        _memory_goal(),
        provider=backend,
        env_file=None,
        planner_name="model",
    )

    planner_prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert "memory_context" not in planner_prompt

    executor_prompt = json.loads(backend.invocations[1]["messages"][0].content)
    assert "context" not in executor_prompt


def test_build_goal_runtime_bundle_reports_supplied_memory_store() -> None:
    store = InMemoryMemoryStore()

    bundle = build_goal_runtime_bundle(
        provider=MockProvider(),
        env_file=None,
        memory_store=store,
    )

    assert bundle.memory_store is store


def test_create_memory_store_builds_configured_stores(tmp_path) -> None:
    memory_store = create_memory_store("memory")
    in_memory_store = create_memory_store("in-memory")
    sqlite_store = create_memory_store("sqlite", tmp_path / "memory.sqlite")

    assert isinstance(memory_store, InMemoryMemoryStore)
    assert isinstance(in_memory_store, InMemoryMemoryStore)
    assert isinstance(sqlite_store, SqliteMemoryStore)
    assert registered_memory_store_kinds() == ("in_memory", "memory", "sqlite")


def test_create_memory_store_rejects_invalid_config(tmp_path) -> None:
    with pytest.raises(ValueError, match="sqlite memory store requires"):
        create_memory_store("sqlite")
    with pytest.raises(ValueError, match="unknown memory store kind"):
        create_memory_store("unknown", tmp_path / "memory.sqlite")


def test_build_goal_runtime_bundle_reports_configured_memory_store(tmp_path) -> None:
    bundle = build_goal_runtime_bundle(
        provider=MockProvider(),
        env_file=None,
        memory_store_kind="sqlite",
        memory_store_path=tmp_path / "memory.sqlite",
    )

    assert isinstance(bundle.memory_store, SqliteMemoryStore)


def test_build_goal_runtime_bundle_default_memory_store_uses_registry() -> None:
    built: list[object] = []
    store = InMemoryMemoryStore()

    def build_default(path) -> InMemoryMemoryStore:
        built.append(path)
        return store

    try:
        register_memory_store("memory", build_default, replace=True)

        bundle = build_goal_runtime_bundle(
            provider=MockProvider(),
            env_file=None,
            enable_agent_memory=True,
        )
    finally:
        reset_memory_store_registry()

    assert bundle.memory_store is store
    assert built == [None]


def test_memory_store_registry_builds_custom_store(tmp_path) -> None:
    built: list[object] = []
    store = InMemoryMemoryStore()

    def build_custom(path) -> InMemoryMemoryStore:
        built.append(path)
        return store

    try:
        register_memory_store("custom-store", build_custom)

        out = create_memory_store("custom-store", tmp_path / "custom.memory")
    finally:
        reset_memory_store_registry()

    assert out is store
    assert built == [tmp_path / "custom.memory"]


def test_memory_store_registry_rejects_duplicate_without_replace() -> None:
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_memory_store("memory", lambda path: InMemoryMemoryStore())

        register_memory_store(
            "memory",
            lambda path: InMemoryMemoryStore(),
            replace=True,
        )
        assert isinstance(create_memory_store("memory"), InMemoryMemoryStore)
    finally:
        reset_memory_store_registry()


@pytest.mark.asyncio
async def test_run_goal_uses_registered_custom_memory_store_context(tmp_path) -> None:
    custom_store = InMemoryMemoryStore()
    await custom_store.append(
        MemoryScope.WORKFLOW,
        "runtime-memory-goal",
        "episode.previous",
        {"lesson": "registered store context"},
    )
    built: list[object] = []

    def build_custom(path) -> InMemoryMemoryStore:
        built.append(path)
        return custom_store

    backend = MockProvider(
        scripted=[
            _plan_turn(),
            ScriptedTurn(content='{"done": true}'),
        ]
    )

    try:
        register_memory_store("custom", build_custom)
        session = await run_goal(
            _memory_goal(),
            provider=backend,
            env_file=None,
            planner_name="model",
            memory_store_kind="custom",
            memory_store_path=tmp_path / "custom.memory",
        )
    finally:
        reset_memory_store_registry()

    planner_prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert planner_prompt["memory_context"]["entries"][0]["value"] == {
        "lesson": "registered store context"
    }
    executor_prompt = json.loads(backend.invocations[1]["messages"][0].content)
    assert executor_prompt["context"]["memory"]["entries"][0]["value"] == {
        "lesson": "registered store context"
    }
    assert "entries" not in session.metadata["goal"]["memory_context"]
    assert "memory_context" not in session.metadata["execution_plan"]["metadata"]
    assert built == [tmp_path / "custom.memory"]


@pytest.mark.asyncio
async def test_run_goal_uses_sqlite_memory_store_context(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.sqlite")
    await store.append(
        MemoryScope.WORKFLOW,
        "runtime-memory-goal",
        "episode.previous",
        {"lesson": "durable context"},
    )
    backend = MockProvider(
        scripted=[
            _plan_turn(),
            ScriptedTurn(content='{"done": true}'),
        ]
    )

    session = await run_goal(
        _memory_goal(),
        provider=backend,
        env_file=None,
        planner_name="model",
        memory_store=store,
    )

    planner_prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert planner_prompt["memory_context"]["entries"][0]["value"] == {
        "lesson": "durable context"
    }
    executor_prompt = json.loads(backend.invocations[1]["messages"][0].content)
    assert executor_prompt["context"]["memory"]["entries"][0]["value"] == {
        "lesson": "durable context"
    }
    assert "entries" not in session.metadata["goal"]["memory_context"]
    assert "memory_context" not in session.metadata["execution_plan"]["metadata"]


@pytest.mark.asyncio
async def test_run_goal_uses_configured_sqlite_memory_store_context(tmp_path) -> None:
    memory_path = tmp_path / "configured-memory.sqlite"
    store = SqliteMemoryStore(memory_path)
    await store.append(
        MemoryScope.WORKFLOW,
        "runtime-memory-goal",
        "episode.previous",
        {"lesson": "configured durable context"},
    )
    backend = MockProvider(
        scripted=[
            _plan_turn(),
            ScriptedTurn(content='{"done": true}'),
        ]
    )

    session = await run_goal(
        _memory_goal(),
        provider=backend,
        env_file=None,
        planner_name="model",
        memory_store_kind="sqlite",
        memory_store_path=memory_path,
    )

    planner_prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert planner_prompt["memory_context"]["entries"][0]["value"] == {
        "lesson": "configured durable context"
    }
    executor_prompt = json.loads(backend.invocations[1]["messages"][0].content)
    assert executor_prompt["context"]["memory"]["entries"][0]["value"] == {
        "lesson": "configured durable context"
    }
    assert "entries" not in session.metadata["goal"]["memory_context"]
    assert "memory_context" not in session.metadata["execution_plan"]["metadata"]
