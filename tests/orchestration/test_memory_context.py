"""Memory context projection contracts."""

from __future__ import annotations

import pytest

from hydramind.memory import InMemoryMemoryStore, MemoryScope
from hydramind.orchestration import (
    MemoryContextPolicy,
    MemoryContextQuery,
    MemoryContextRequest,
    StoreMemoryContextRetriever,
)


@pytest.mark.asyncio
async def test_store_memory_context_retriever_scans_explicit_scope_with_limit() -> None:
    store = InMemoryMemoryStore()
    await store.append(
        MemoryScope.WORKFLOW,
        "article-workflow",
        "episode.session-a",
        {"summary": "old"},
    )
    await store.append(
        MemoryScope.WORKFLOW,
        "article-workflow",
        "episode.session-b",
        {"summary": "new"},
        metadata={"source": "episode_projector"},
    )

    context = await StoreMemoryContextRetriever(store).retrieve(
        MemoryContextRequest(
            policy=MemoryContextPolicy(
                enabled=True,
                max_entries=1,
                queries=(
                    MemoryContextQuery(
                        scope=MemoryScope.WORKFLOW,
                        scope_id="article-workflow",
                        key_prefix="episode.",
                        limit=1,
                    ),
                ),
            ),
            purpose="planner.initial_plan",
            workflow_name="article-workflow",
        )
    )

    assert len(context.entries) == 1
    entry = context.entries[0]
    assert entry.value == {"summary": "new"}
    assert entry.evidence_ref.startswith("memory://workflow/article-workflow/")
    assert context.as_prompt_payload()["evidence_refs"] == [entry.evidence_ref]


@pytest.mark.asyncio
async def test_disabled_memory_context_policy_returns_empty_projection() -> None:
    store = InMemoryMemoryStore()
    await store.append(MemoryScope.GLOBAL, "global", "episode.any", {"summary": "x"})

    context = await StoreMemoryContextRetriever(store).retrieve(
        MemoryContextRequest(
            policy=MemoryContextPolicy(
                enabled=False,
                queries=(
                    MemoryContextQuery(
                        scope=MemoryScope.GLOBAL,
                        scope_id="global",
                        key_prefix="episode.",
                    ),
                ),
            ),
            purpose="executor.node_prompt",
        )
    )

    assert context.entries == ()
    assert context.evidence_refs == ()
