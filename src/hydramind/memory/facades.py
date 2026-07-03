"""AgentMemory / EpisodicMemory — scope-bound facades over MemoryStore."""

from __future__ import annotations

from typing import Any

from hydramind.memory.base import MemoryEntry, MemoryScope, MemoryStore


class AgentMemory:
    """Per-agent memory scoped to one agent identity within a session.

    Thin facade that binds ``MemoryScope.AGENT`` and an ``agent_id`` (the
    team member id). Used to record each agent's turn output so an agent's
    own history is recoverable independently of the flat session trajectory.
    No caching, no extra state.
    """

    def __init__(self, store: MemoryStore, agent_id: str) -> None:
        self._store = store
        self._agent_id = agent_id

    @property
    def agent_id(self) -> str:
        return self._agent_id

    async def put(
        self, key: str, value: Any, *, metadata: dict[str, Any] | None = None
    ) -> None:
        await self._store.put(
            MemoryEntry(
                scope=MemoryScope.AGENT,
                scope_id=self._agent_id,
                key=key,
                value=value,
                metadata=metadata or {},
            )
        )

    async def get(self, key: str) -> MemoryEntry | None:
        return await self._store.get(MemoryScope.AGENT, self._agent_id, key)

    async def append(
        self,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        return await self._store.append(
            MemoryScope.AGENT,
            self._agent_id,
            key,
            value,
            metadata=metadata,
        )

    async def scan(self, *, key_prefix: str | None = None) -> list[MemoryEntry]:
        return await self._store.scan(
            MemoryScope.AGENT, self._agent_id, key_prefix=key_prefix
        )


class EpisodicMemory:
    """Long-term, workflow-scoped memory across sessions."""

    def __init__(self, store: MemoryStore, workflow_name: str) -> None:
        self._store = store
        self._workflow = workflow_name

    @property
    def workflow_name(self) -> str:
        return self._workflow

    async def put(
        self, key: str, value: Any, *, metadata: dict[str, Any] | None = None
    ) -> None:
        await self._store.put(
            MemoryEntry(
                scope=MemoryScope.WORKFLOW,
                scope_id=self._workflow,
                key=key,
                value=value,
                metadata=metadata or {},
            )
        )

    async def get(self, key: str) -> MemoryEntry | None:
        return await self._store.get(MemoryScope.WORKFLOW, self._workflow, key)

    async def record_episode(
        self,
        session_id: str,
        summary: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Append one session-completion snapshot keyed by session id."""
        return await self._store.append(
            MemoryScope.WORKFLOW,
            self._workflow,
            f"episode.{session_id}",
            summary,
            metadata=metadata,
        )

    async def recent_episodes(self, *, limit: int | None = None) -> list[MemoryEntry]:
        episodes = await self._store.scan(
            MemoryScope.WORKFLOW, self._workflow, key_prefix="episode."
        )
        if limit is None:
            return episodes
        return episodes[-limit:]
