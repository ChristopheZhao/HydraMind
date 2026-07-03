"""SQLite SessionStore persistence tests."""

from __future__ import annotations

import pytest

from hydramind.control import (
    SessionService,
    SessionStatus,
    SessionStoreConflictError,
    SqliteSessionStore,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="sqlite-demo",
        nodes=(WorkflowNodeSpec(key="plan", role="planner"),),
    )


async def test_sqlite_session_store_persists_runtime_session(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite"
    store = SqliteSessionStore(db_path)
    service = SessionService(store)

    created = await service.create_session(_blueprint(), input_payload={"topic": "x"})
    await service.mark_session_running(created.id)
    await service.complete_session(created.id, summary={"ok": True})

    reloaded_service = SessionService(SqliteSessionStore(db_path))
    reloaded = await reloaded_service.get_session(created.id)

    assert reloaded.status is SessionStatus.COMPLETED
    assert reloaded.input_payload == {"topic": "x"}
    assert reloaded.summary_output == {"ok": True}
    assert await store.all_ids() == [created.id]


async def test_sqlite_session_store_rejects_stale_session_versions(tmp_path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite")
    service = SessionService(store)
    created = await service.create_session(_blueprint())
    first = await store.get(created.id)
    second = await store.get(created.id)
    assert first is not None
    assert second is not None
    assert first.version == second.version == 1

    first.metadata["writer"] = "first"
    await store.put(first)
    assert first.version == 2

    second.metadata["writer"] = "second"
    with pytest.raises(SessionStoreConflictError, match="version conflict"):
        await store.put(second)

    latest = await store.get(created.id)
    assert latest is not None
    assert latest.version == 2
    assert latest.metadata["writer"] == "first"


async def test_sqlite_session_store_delete_and_missing(tmp_path) -> None:
    store = SqliteSessionStore(tmp_path / "sessions.sqlite")
    service = SessionService(store)
    created = await service.create_session(_blueprint())

    await store.delete(created.id)

    assert await store.get(created.id) is None
    assert await store.all_ids() == []


async def test_sqlite_session_store_supports_memory_database() -> None:
    store = SqliteSessionStore(":memory:")
    service = SessionService(store)

    created = await service.create_session(_blueprint())
    reloaded = await store.get(created.id)

    assert reloaded is not None
    assert reloaded.id == created.id
