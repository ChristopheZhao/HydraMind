"""Memory primitives: MemoryEntry, MemoryScope, MemoryStore Protocol."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(StrEnum):
    """Visibility of a memory entry."""

    AGENT = "agent"        # visible to one agent identity within one session
    SESSION = "session"    # visible to all agents within one session
    WORKFLOW = "workflow"  # visible across all sessions of one workflow
    GLOBAL = "global"      # visible across all workflows (use sparingly)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryEntry(BaseModel):
    """One stored memory record."""

    model_config = ConfigDict(frozen=True)

    scope: MemoryScope
    scope_id: str = Field(
        ...,
        description="agent id / session id / workflow name / 'global'",
    )
    key: str
    value: Any
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """Asynchronous typed key-value store for memory entries."""

    async def put(self, entry: MemoryEntry) -> None: ...

    async def get(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> MemoryEntry | None: ...

    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Store ``value`` under an auto-suffixed key so multiple ``append``
        calls with the same ``key`` preserve order (e.g. ``key.0`` ``key.1``)."""

    async def scan(
        self,
        scope: MemoryScope,
        scope_id: str,
        *,
        key_prefix: str | None = None,
    ) -> list[MemoryEntry]:
        """Return entries under (scope, scope_id), optionally filtered by
        key prefix, ordered by ``created_at``."""

    async def delete(
        self, scope: MemoryScope, scope_id: str, key: str
    ) -> None: ...
