"""SessionStore — persistence abstraction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from hydramind.control.models import RuntimeSession


class SessionStoreConflictError(RuntimeError):
    """Raised when a stale RuntimeSession version is written."""


@runtime_checkable
class SessionStore(Protocol):
    """Storage interface for RuntimeSession instances."""

    async def put(self, session: RuntimeSession) -> None: ...
    async def get(self, session_id: str) -> RuntimeSession | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def all_ids(self) -> list[str]: ...


class InMemorySessionStore:
    """Process-local, dict-backed store. Suitable for tests and single-process use."""

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}

    async def put(self, session: RuntimeSession) -> None:
        current = self._sessions.get(session.id)
        _assert_expected_version(session, current)
        session.version += 1
        # Defensive deep-copy so callers can't mutate stored state through references.
        self._sessions[session.id] = session.model_copy(deep=True)

    async def get(self, session_id: str) -> RuntimeSession | None:
        stored = self._sessions.get(session_id)
        return stored.model_copy(deep=True) if stored is not None else None

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def all_ids(self) -> list[str]:
        return list(self._sessions.keys())


class SqliteSessionStore:
    """SQLite-backed RuntimeSession store.

    The store persists the full Pydantic model as JSON so SessionService remains
    the single owner of mutation semantics.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._memory_conn: sqlite3.Connection | None = None
        if self.path == Path(":memory:"):
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    async def put(self, session: RuntimeSession) -> None:
        with self._connect() as conn:
            conn.execute("begin immediate")
            try:
                row = conn.execute(
                    "select payload_json from runtime_sessions where id = ?",
                    (session.id,),
                ).fetchone()
                current = (
                    RuntimeSession.model_validate_json(str(row["payload_json"]))
                    if row is not None
                    else None
                )
                _assert_expected_version(session, current)
                session.version += 1
                payload = session.model_dump_json()
                conn.execute(
                    """
                    insert into runtime_sessions (
                        id, workflow_name, workflow_version, status, updated_at, payload_json
                    )
                    values (?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        workflow_name = excluded.workflow_name,
                        workflow_version = excluded.workflow_version,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        payload_json = excluded.payload_json
                    """,
                    (
                        session.id,
                        session.workflow_name,
                        session.workflow_version,
                        session.status.value,
                        session.updated_at.isoformat(),
                        payload,
                    ),
                )
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    async def get(self, session_id: str) -> RuntimeSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "select payload_json from runtime_sessions where id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return RuntimeSession.model_validate_json(str(row["payload_json"]))

    async def delete(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("delete from runtime_sessions where id = ?", (session_id,))
            conn.commit()

    async def all_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("select id from runtime_sessions order by updated_at, id").fetchall()
        return [str(row["id"]) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            yield self._memory_conn
            return
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists runtime_sessions (
                    id text primary key,
                    workflow_name text not null,
                    workflow_version text not null,
                    status text not null,
                    updated_at text not null,
                    payload_json text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists ix_runtime_sessions_status_updated
                on runtime_sessions(status, updated_at)
                """
            )
            conn.commit()


def _assert_expected_version(
    session: RuntimeSession,
    current: RuntimeSession | None,
) -> None:
    if current is None:
        if session.version != 0:
            raise SessionStoreConflictError(
                f"session {session.id!r} does not exist; expected version 0 "
                f"for insert, got {session.version}"
            )
        return
    if current.version != session.version:
        raise SessionStoreConflictError(
            f"session {session.id!r} version conflict: stored={current.version}, "
            f"attempted={session.version}"
        )
