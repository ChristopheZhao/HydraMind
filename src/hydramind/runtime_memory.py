"""Runtime-edge assembly for optional goal memory wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hydramind.memory import (
    AgentTurnMemoryObserver,
    EpisodeProjectorObserver,
    MemoryStore,
    MemoryWriteAuthority,
)
from hydramind.observability import Emitter
from hydramind.orchestration import MemoryContextRetriever, StoreMemoryContextRetriever
from hydramind.runtime_support import create_memory_store


@dataclass(frozen=True)
class GoalMemoryRuntime:
    """Effective memory dependencies for one goal runtime bundle."""

    emitter: Emitter | None
    memory_store: MemoryStore | None
    memory_retriever: MemoryContextRetriever | None


def build_goal_memory_runtime(
    *,
    emitter: Emitter | None,
    enable_episodic_memory: bool,
    enable_agent_memory: bool,
    memory_store: MemoryStore | None,
    memory_store_kind: str | None,
    memory_store_path: str | Path | None,
    memory_retriever: MemoryContextRetriever | None,
) -> GoalMemoryRuntime:
    """Assemble optional memory observers and prompt-context retrieval."""

    effective_emitter = emitter
    effective_memory_store = memory_store
    if effective_memory_store is None:
        if memory_store_kind is not None:
            effective_memory_store = create_memory_store(
                memory_store_kind,
                memory_store_path,
            )
        elif memory_store_path is not None:
            raise ValueError("--memory-store-path requires --memory-store")

    if enable_episodic_memory or enable_agent_memory:
        effective_memory_store = effective_memory_store or create_memory_store("memory")
        effective_emitter = effective_emitter or Emitter()
        # Control-owned authority for prompt-affecting memory writes (S4a/F6):
        # versioned, durable-append, raises on failure.
        authority = MemoryWriteAuthority(effective_memory_store)
        if enable_episodic_memory:
            effective_emitter.add(
                EpisodeProjectorObserver(authority, workflow_name="goals")
            )
        if enable_agent_memory:
            effective_emitter.add(AgentTurnMemoryObserver(authority))

    effective_memory_retriever = memory_retriever
    if effective_memory_retriever is None and effective_memory_store is not None:
        effective_memory_retriever = StoreMemoryContextRetriever(effective_memory_store)

    return GoalMemoryRuntime(
        emitter=effective_emitter,
        memory_store=effective_memory_store,
        memory_retriever=effective_memory_retriever,
    )
