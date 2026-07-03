"""Layered memory — pluggable typed key-value store with scope semantics.

See ``docs/architecture/50-memory-and-observability.md`` §1.
"""

from hydramind.memory.base import (
    MemoryEntry,
    MemoryScope,
    MemoryStore,
)
from hydramind.memory.facades import AgentMemory, EpisodicMemory
from hydramind.memory.ownership import (
    MemoryWriteAuthority,
    MemoryWriteClass,
    MemoryWriteError,
    is_runtime_influencing,
)
from hydramind.memory.projector import (
    AgentTurnMemoryObserver,
    EpisodeProjectorObserver,
    build_episode_summary,
)
from hydramind.memory.stores import InMemoryMemoryStore, SqliteMemoryStore

__all__ = [
    "AgentMemory",
    "AgentTurnMemoryObserver",
    "EpisodeProjectorObserver",
    "EpisodicMemory",
    "InMemoryMemoryStore",
    "MemoryEntry",
    "MemoryScope",
    "MemoryStore",
    "MemoryWriteAuthority",
    "MemoryWriteClass",
    "MemoryWriteError",
    "SqliteMemoryStore",
    "build_episode_summary",
    "is_runtime_influencing",
]
