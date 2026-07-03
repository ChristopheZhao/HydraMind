"""InMemoryMemoryStore + AgentMemory + EpisodicMemory tests."""

from __future__ import annotations

import pytest

from hydramind.memory import (
    AgentMemory,
    EpisodicMemory,
    InMemoryMemoryStore,
    MemoryEntry,
    MemoryScope,
    SqliteMemoryStore,
)


@pytest.fixture
def store() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


@pytest.mark.asyncio
async def test_put_then_get_round_trip(store: InMemoryMemoryStore) -> None:
    entry = MemoryEntry(
        scope=MemoryScope.SESSION,
        scope_id="s1",
        key="k",
        value={"x": 1},
    )
    await store.put(entry)
    out = await store.get(MemoryScope.SESSION, "s1", "k")
    assert out is not None
    assert out.value == {"x": 1}


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: InMemoryMemoryStore) -> None:
    assert await store.get(MemoryScope.SESSION, "s1", "missing") is None


@pytest.mark.asyncio
async def test_append_generates_ordered_keys(store: InMemoryMemoryStore) -> None:
    e0 = await store.append(MemoryScope.SESSION, "s1", "msg", "first")
    e1 = await store.append(MemoryScope.SESSION, "s1", "msg", "second")
    e2 = await store.append(MemoryScope.SESSION, "s1", "msg", "third")
    assert e0.key == "msg.0"
    assert e1.key == "msg.1"
    assert e2.key == "msg.2"


@pytest.mark.asyncio
async def test_scan_returns_entries_in_creation_order(
    store: InMemoryMemoryStore,
) -> None:
    await store.append(MemoryScope.SESSION, "s1", "msg", "a")
    await store.append(MemoryScope.SESSION, "s1", "other", "x")
    await store.append(MemoryScope.SESSION, "s1", "msg", "b")
    all_entries = await store.scan(MemoryScope.SESSION, "s1")
    assert [e.value for e in all_entries] == ["a", "x", "b"]


@pytest.mark.asyncio
async def test_scan_key_prefix_filter(store: InMemoryMemoryStore) -> None:
    await store.append(MemoryScope.SESSION, "s1", "msg", "a")
    await store.append(MemoryScope.SESSION, "s1", "trace", "ignored")
    msgs = await store.scan(MemoryScope.SESSION, "s1", key_prefix="msg")
    assert [e.value for e in msgs] == ["a"]


@pytest.mark.asyncio
async def test_scopes_are_isolated(store: InMemoryMemoryStore) -> None:
    await store.put(
        MemoryEntry(scope=MemoryScope.SESSION, scope_id="s1", key="k", value=1)
    )
    await store.put(
        MemoryEntry(scope=MemoryScope.AGENT, scope_id="s1", key="k", value=2)
    )
    s = await store.get(MemoryScope.SESSION, "s1", "k")
    a = await store.get(MemoryScope.AGENT, "s1", "k")
    assert s is not None and s.value == 1
    assert a is not None and a.value == 2


@pytest.mark.asyncio
async def test_delete_removes_entry(store: InMemoryMemoryStore) -> None:
    await store.put(
        MemoryEntry(scope=MemoryScope.SESSION, scope_id="s1", key="k", value=1)
    )
    await store.delete(MemoryScope.SESSION, "s1", "k")
    assert await store.get(MemoryScope.SESSION, "s1", "k") is None


@pytest.mark.asyncio
async def test_agent_memory_facade_is_agent_scoped(store: InMemoryMemoryStore) -> None:
    am = AgentMemory(store, "writer")
    await am.put("draft", "v1")
    await am.append("turn", "step1")
    await am.append("turn", "step2")
    draft = await am.get("draft")
    assert draft is not None and draft.value == "v1"
    turns = await am.scan(key_prefix="turn")
    assert [e.value for e in turns] == ["step1", "step2"]
    # The facade binds MemoryScope.AGENT with the agent id as scope_id.
    assert all(e.scope is MemoryScope.AGENT for e in turns)
    assert all(e.scope_id == "writer" for e in turns)
    raw = await store.get(MemoryScope.AGENT, "writer", "draft")
    assert raw is not None and raw.value == "v1"


@pytest.mark.asyncio
async def test_episodic_memory_records_episode(store: InMemoryMemoryStore) -> None:
    em = EpisodicMemory(store, "demo_workflow")
    await em.record_episode("sess-a", {"summary": "done a"})
    await em.record_episode("sess-b", {"summary": "done b"})
    recent = await em.recent_episodes()
    assert len(recent) == 2
    assert recent[-1].value == {"summary": "done b"}
    just_one = await em.recent_episodes(limit=1)
    assert len(just_one) == 1
    assert just_one[0].value == {"summary": "done b"}


@pytest.mark.asyncio
async def test_sqlite_memory_store_persists_entries_across_reopen(tmp_path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SqliteMemoryStore(path)
    entry = MemoryEntry(
        scope=MemoryScope.WORKFLOW,
        scope_id="wf",
        key="episode.seed",
        value={"lesson": "persist context"},
        metadata={"source": "test"},
    )

    await store.put(entry)
    reopened = SqliteMemoryStore(path)
    out = await reopened.get(MemoryScope.WORKFLOW, "wf", "episode.seed")

    assert out is not None
    assert out.scope is MemoryScope.WORKFLOW
    assert out.scope_id == "wf"
    assert out.key == "episode.seed"
    assert out.value == {"lesson": "persist context"}
    assert out.metadata == {"source": "test"}
    assert out.created_at == entry.created_at


@pytest.mark.asyncio
async def test_sqlite_memory_store_append_continues_after_reopen(tmp_path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SqliteMemoryStore(path)
    first = await store.append(MemoryScope.AGENT, "writer", "turn", "a")
    second = await store.append(MemoryScope.AGENT, "writer", "turn", "b")

    reopened = SqliteMemoryStore(path)
    third = await reopened.append(MemoryScope.AGENT, "writer", "turn", "c")

    assert [first.key, second.key, third.key] == ["turn.0", "turn.1", "turn.2"]
    entries = await reopened.scan(MemoryScope.AGENT, "writer", key_prefix="turn")
    assert [entry.value for entry in entries] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_sqlite_memory_store_scope_and_prefix_isolation(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.sqlite")
    await store.append(MemoryScope.SESSION, "s1", "msg", "s1-msg")
    await store.append(MemoryScope.SESSION, "s1", "trace", "ignored")
    await store.append(MemoryScope.SESSION, "s2", "msg", "s2-msg")
    await store.append(MemoryScope.AGENT, "s1", "msg", "agent-msg")

    entries = await store.scan(MemoryScope.SESSION, "s1", key_prefix="msg")

    assert [entry.value for entry in entries] == ["s1-msg"]
    assert await store.get(MemoryScope.SESSION, "s1", "msg.0") is not None
    assert await store.get(MemoryScope.SESSION, "s2", "msg.0") is not None
    assert await store.get(MemoryScope.AGENT, "s1", "msg.0") is not None


@pytest.mark.asyncio
async def test_sqlite_memory_store_prefix_filter_treats_wildcards_literally(
    tmp_path,
) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.sqlite")
    await store.append(MemoryScope.SESSION, "s1", "msg_%", "literal")
    await store.append(MemoryScope.SESSION, "s1", "msg_x", "wildcard")

    entries = await store.scan(MemoryScope.SESSION, "s1", key_prefix="msg_%")

    assert [entry.value for entry in entries] == ["literal"]


@pytest.mark.asyncio
async def test_sqlite_memory_store_delete_removes_only_one_scoped_key(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.sqlite")
    await store.put(
        MemoryEntry(scope=MemoryScope.SESSION, scope_id="s1", key="k", value=1)
    )
    await store.put(
        MemoryEntry(scope=MemoryScope.AGENT, scope_id="s1", key="k", value=2)
    )

    await store.delete(MemoryScope.SESSION, "s1", "k")

    assert await store.get(MemoryScope.SESSION, "s1", "k") is None
    agent_entry = await store.get(MemoryScope.AGENT, "s1", "k")
    assert agent_entry is not None and agent_entry.value == 2


@pytest.mark.asyncio
async def test_sqlite_memory_store_supports_facades(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.sqlite")
    agent_memory = AgentMemory(store, "researcher")
    episodic_memory = EpisodicMemory(store, "workflow")

    await agent_memory.append("turn", {"content": "draft"})
    await episodic_memory.record_episode("sess-1", {"summary": "done"})

    assert [entry.value for entry in await agent_memory.scan(key_prefix="turn")] == [
        {"content": "draft"}
    ]
    episodes = await episodic_memory.recent_episodes()
    assert len(episodes) == 1
    assert episodes[0].value == {"summary": "done"}
