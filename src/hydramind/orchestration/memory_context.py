"""Opt-in memory context projection for planning and execution prompts."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from hydramind.memory import MemoryEntry, MemoryScope, MemoryStore


class MemoryContextQuery(BaseModel):
    """One explicit memory scan request."""

    model_config = ConfigDict(frozen=True)

    scope: MemoryScope
    scope_id: str
    key_prefix: str | None = None
    limit: int = Field(default=5, ge=1, le=50)


class MemoryContextPolicy(BaseModel):
    """Caller opt-in policy for prompt memory context."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    queries: tuple[MemoryContextQuery, ...] = ()
    max_entries: int = Field(default=8, ge=1, le=100)


class MemoryContextRequest(BaseModel):
    """Runtime request passed to a memory-context retriever."""

    model_config = ConfigDict(frozen=True)

    policy: MemoryContextPolicy
    purpose: str
    workflow_name: str | None = None
    session_id: str | None = None
    node_key: str | None = None
    goal_objective: str | None = None
    current_plan_name: str | None = None
    feedback_refs: tuple[str, ...] = ()


class MemoryContextEntry(BaseModel):
    """One bounded memory item safe to project into prompt context."""

    model_config = ConfigDict(frozen=True)

    scope: MemoryScope
    scope_id: str
    key: str
    value: Any
    evidence_ref: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MemoryContext(BaseModel):
    """Projected prompt context with stable evidence references."""

    model_config = ConfigDict(frozen=True)

    purpose: str
    entries: tuple[MemoryContextEntry, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(entry.evidence_ref for entry in self.entries)

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "entries": [entry.model_dump(mode="json") for entry in self.entries],
            "evidence_refs": list(self.evidence_refs),
        }


@runtime_checkable
class MemoryContextRetriever(Protocol):
    """Read-only retriever for bounded prompt memory context."""

    async def retrieve(self, request: MemoryContextRequest) -> MemoryContext: ...


class StoreMemoryContextRetriever:
    """MemoryStore-backed retriever using explicit scans only."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def retrieve(self, request: MemoryContextRequest) -> MemoryContext:
        policy = request.policy
        if not policy.enabled or not policy.queries:
            return MemoryContext(purpose=request.purpose)

        entries: list[MemoryContextEntry] = []
        for query in policy.queries:
            scanned = await self._store.scan(
                query.scope,
                query.scope_id,
                key_prefix=query.key_prefix,
            )
            for entry in scanned[-query.limit :]:
                entries.append(_context_entry(entry))
                if len(entries) >= policy.max_entries:
                    return MemoryContext(
                        purpose=request.purpose,
                        entries=tuple(entries),
                    )
        return MemoryContext(purpose=request.purpose, entries=tuple(entries))


def _context_entry(entry: MemoryEntry) -> MemoryContextEntry:
    return MemoryContextEntry(
        scope=entry.scope,
        scope_id=entry.scope_id,
        key=entry.key,
        value=entry.value,
        evidence_ref=_memory_evidence_ref(entry),
        metadata=dict(entry.metadata),
        created_at=entry.created_at.isoformat(),
    )


def _memory_evidence_ref(entry: MemoryEntry) -> str:
    return f"memory://{entry.scope.value}/{entry.scope_id}/{entry.key}"
