"""MemoryStore implementations."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hydramind.memory.base import MemoryEntry, MemoryScope


class InMemoryMemoryStore:
    """Dict-backed MemoryStore. Suitable for tests and single-process use."""

    def __init__(self) -> None:
        # Key: (scope, scope_id) → key → MemoryEntry
        self._entries: dict[tuple[MemoryScope, str], dict[str, MemoryEntry]] = {}
        self._append_counters: dict[tuple[MemoryScope, str, str], int] = {}

    def _bucket(
        self, scope: MemoryScope, scope_id: str
    ) -> dict[str, MemoryEntry]:
        return self._entries.setdefault((scope, scope_id), {})

    async def put(self, entry: MemoryEntry) -> None:
        self._bucket(entry.scope, entry.scope_id)[entry.key] = entry

    async def get(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> MemoryEntry | None:
        return self._entries.get((scope, scope_id), {}).get(key)

    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        counter_key = (scope, scope_id, key)
        idx = self._append_counters.get(counter_key, 0)
        self._append_counters[counter_key] = idx + 1
        composite_key = f"{key}.{idx}"
        entry = MemoryEntry(
            scope=scope,
            scope_id=scope_id,
            key=composite_key,
            value=value,
            metadata=metadata or {},
        )
        await self.put(entry)
        return entry

    async def scan(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        key_prefix: str | None = None,
    ) -> list[MemoryEntry]:
        bucket = self._entries.get((scope, scope_id), {})
        items: list[MemoryEntry] = list(bucket.values())
        if key_prefix is not None:
            items = [e for e in items if e.key.startswith(key_prefix)]
        return sorted(items, key=lambda e: e.created_at)

    async def delete(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> None:
        self._entries.get((scope, scope_id), {}).pop(key, None)


class SqliteMemoryStore:
    """SQLite-backed MemoryStore for durable local memory entries."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._memory_conn: sqlite3.Connection | None = None
        if self.path == Path(":memory:"):
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    async def put(self, entry: MemoryEntry) -> None:
        with self._connect() as conn:
            conn.execute("begin immediate")
            try:
                conn.execute(
                    """
                    insert into memory_entries (
                        scope, scope_id, key, created_at, payload_json
                    )
                    values (?, ?, ?, ?, ?)
                    on conflict(scope, scope_id, key) do update set
                        created_at = excluded.created_at,
                        payload_json = excluded.payload_json
                    """,
                    _entry_params(entry),
                )
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    async def get(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> MemoryEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select payload_json
                from memory_entries
                where scope = ? and scope_id = ? and key = ?
                """,
                (scope.value, scope_id, key),
            ).fetchone()
        if row is None:
            return None
        return MemoryEntry.model_validate_json(str(row["payload_json"]))

    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        with self._connect() as conn:
            conn.execute("begin immediate")
            try:
                idx = _next_append_index(
                    [
                        str(row["key"])
                        for row in conn.execute(
                            """
                            select key
                            from memory_entries
                            where scope = ? and scope_id = ? and key like ?
                            """,
                            (scope.value, scope_id, f"{key}.%"),
                        ).fetchall()
                    ],
                    key,
                )
                entry = MemoryEntry(
                    scope=scope,
                    scope_id=scope_id,
                    key=f"{key}.{idx}",
                    value=value,
                    metadata=metadata or {},
                )
                conn.execute(
                    """
                    insert into memory_entries (
                        scope, scope_id, key, created_at, payload_json
                    )
                    values (?, ?, ?, ?, ?)
                    """,
                    _entry_params(entry),
                )
            except Exception:
                conn.rollback()
                raise
            conn.commit()
        return entry

    async def scan(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        key_prefix: str | None = None,
    ) -> list[MemoryEntry]:
        params: list[Any] = [scope.value, scope_id]
        key_filter = ""
        if key_prefix is not None:
            key_filter = " and key like ? escape '\\'"
            params.append(f"{_escape_like(key_prefix)}%")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select payload_json
                from memory_entries
                where scope = ? and scope_id = ?{key_filter}
                order by created_at, id, key
                """,
                params,
            ).fetchall()
        return [
            MemoryEntry.model_validate_json(str(row["payload_json"]))
            for row in rows
        ]

    async def delete(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                delete from memory_entries
                where scope = ? and scope_id = ? and key = ?
                """,
                (scope.value, scope_id, key),
            )
            conn.commit()

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
                create table if not exists memory_entries (
                    id integer primary key autoincrement,
                    scope text not null,
                    scope_id text not null,
                    key text not null,
                    created_at text not null,
                    payload_json text not null,
                    unique(scope, scope_id, key)
                )
                """
            )
            conn.execute(
                """
                create index if not exists ix_memory_entries_scope_created
                on memory_entries(scope, scope_id, created_at, id)
                """
            )
            conn.execute(
                """
                create index if not exists ix_memory_entries_scope_key
                on memory_entries(scope, scope_id, key)
                """
            )
            conn.commit()


def _entry_params(entry: MemoryEntry) -> tuple[str, str, str, str, str]:
    return (
        entry.scope.value,
        entry.scope_id,
        entry.key,
        entry.created_at.isoformat(),
        entry.model_dump_json(),
    )


def _next_append_index(existing_keys: list[str], base_key: str) -> int:
    pattern = re.compile(rf"^{re.escape(base_key)}\.(\d+)$")
    indexes = [
        int(match.group(1))
        for key in existing_keys
        if (match := pattern.match(key)) is not None
    ]
    return max(indexes, default=-1) + 1


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
