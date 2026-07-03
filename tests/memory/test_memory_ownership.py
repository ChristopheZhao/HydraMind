"""S4a / F6 — memory write classification + prompt-affecting ownership.

PLAN-20260618-001 §5 S4 / §6, architecture/95 §F6 + Phase 3. Proves:

1. memory writes are classified (``MemoryWriteClass``) and prompt-affecting
   writes go through the control-owned ``MemoryWriteAuthority`` with a
   versioned/sequenced record;
2. a FAILING prompt-affecting memory store SURFACES the failure (emit raises
   ``CriticalObserverError``) instead of silently swallowing it;
3. a failing TELEMETRY-only observer is still swallowed (isolation preserved);
4. happy-path episode-summary + agent-turn memory are still written and
   retrievable (existing wiring unchanged).
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.memory import (
    AgentMemory,
    AgentTurnMemoryObserver,
    EpisodeProjectorObserver,
    EpisodicMemory,
    InMemoryMemoryStore,
    MemoryEntry,
    MemoryScope,
    MemoryWriteAuthority,
    MemoryWriteClass,
    MemoryWriteError,
    is_runtime_influencing,
)
from hydramind.observability import (
    CriticalObserverError,
    Emitter,
    ListObserver,
    ObservationEvent,
    ObservationEventKind,
)


def _message_event(actor: str = "writer") -> ObservationEvent:
    return ObservationEvent(
        kind=ObservationEventKind.AGENT_MESSAGE_SENT,
        session_id="sess-1",
        node_key="collaborate",
        actor=actor,
        detail={
            "turn_index": 0,
            "interaction_id": "interaction-1",
            "role": actor,
            "content_preview": "draft-text",
        },
    )


def _terminal_event() -> ObservationEvent:
    return ObservationEvent(
        kind=ObservationEventKind.SESSION_COMPLETED,
        session_id="sess-1",
        node_key="plan",
        trace_id="trace-1",
        execution_id="exec-1",
    )


class _RaisingMemoryStore(InMemoryMemoryStore):
    """A store whose append always fails — models a backing-store outage."""

    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        raise OSError("disk full")


# --- 1. classification + versioned control-owned authority --------------------


def test_write_classes_are_classified() -> None:
    assert MemoryWriteClass.PROMPT_AFFECTING.value == "prompt_affecting"
    assert is_runtime_influencing(MemoryWriteClass.PROMPT_AFFECTING)
    assert is_runtime_influencing(MemoryWriteClass.GATE_AFFECTING)
    assert is_runtime_influencing(MemoryWriteClass.RECOVERY_AFFECTING)
    assert not is_runtime_influencing(MemoryWriteClass.TELEMETRY)


@pytest.mark.asyncio
async def test_authority_stamps_write_class_and_monotonic_version() -> None:
    store = InMemoryMemoryStore()
    authority = MemoryWriteAuthority(store)

    for _ in range(3):
        await authority.append(
            MemoryScope.AGENT,
            "writer",
            "turn.sess-1",
            {"content_preview": "x"},
            write_class=MemoryWriteClass.PROMPT_AFFECTING,
        )

    entries = await store.scan(MemoryScope.AGENT, "writer")
    versions = [e.metadata["memory_write_version"] for e in entries]
    assert versions == [0, 1, 2]  # monotonic per-key sequence
    assert all(
        e.metadata["memory_write_class"] == MemoryWriteClass.PROMPT_AFFECTING.value
        for e in entries
    )


@pytest.mark.asyncio
async def test_authority_rejects_telemetry_class() -> None:
    authority = MemoryWriteAuthority(InMemoryMemoryStore())
    with pytest.raises(ValueError):
        await authority.append(
            MemoryScope.AGENT,
            "writer",
            "k",
            {},
            write_class=MemoryWriteClass.TELEMETRY,
        )


@pytest.mark.asyncio
async def test_authority_raises_on_store_failure() -> None:
    authority = MemoryWriteAuthority(_RaisingMemoryStore())
    with pytest.raises(MemoryWriteError):
        await authority.append(
            MemoryScope.AGENT,
            "writer",
            "k",
            {},
            write_class=MemoryWriteClass.PROMPT_AFFECTING,
        )


def test_prompt_affecting_observers_are_marked_critical() -> None:
    authority = MemoryWriteAuthority(InMemoryMemoryStore())
    turn = AgentTurnMemoryObserver(authority)
    episode = EpisodeProjectorObserver(authority, workflow_name="goals")
    assert turn.critical is True
    assert episode.critical is True
    assert turn.memory_write_class is MemoryWriteClass.PROMPT_AFFECTING
    assert episode.memory_write_class is MemoryWriteClass.PROMPT_AFFECTING


# --- 2. failing prompt-affecting write SURFACES (not swallowed) ---------------


@pytest.mark.asyncio
async def test_failing_prompt_affecting_memory_write_is_surfaced() -> None:
    # §6: an observer failure on prompt-affecting memory must surface as runtime
    # evidence, NOT be silently swallowed as telemetry.
    authority = MemoryWriteAuthority(_RaisingMemoryStore())
    emitter = Emitter([AgentTurnMemoryObserver(authority)])

    with pytest.raises(CriticalObserverError) as excinfo:
        await emitter.emit(_message_event())

    assert any(isinstance(f, MemoryWriteError) for f in excinfo.value.failures)


@pytest.mark.asyncio
async def test_failing_episode_projection_is_surfaced() -> None:
    authority = MemoryWriteAuthority(_RaisingMemoryStore())
    emitter = Emitter([EpisodeProjectorObserver(authority, workflow_name="goals")])

    with pytest.raises(CriticalObserverError):
        await emitter.emit(_terminal_event())


@pytest.mark.asyncio
async def test_critical_failure_still_runs_other_observers() -> None:
    # Telemetry isolation is preserved even alongside a failing critical observer:
    # the telemetry observer still receives the event before the surface raise.
    telemetry = ListObserver()
    authority = MemoryWriteAuthority(_RaisingMemoryStore())
    emitter = Emitter([telemetry, AgentTurnMemoryObserver(authority)])

    with pytest.raises(CriticalObserverError):
        await emitter.emit(_message_event())

    assert len(telemetry.events) == 1  # telemetry still ran


# --- 3. telemetry-only observer failure is still swallowed --------------------


@pytest.mark.asyncio
async def test_telemetry_observer_failure_is_still_swallowed() -> None:
    class _BoomTelemetry:  # no ``critical`` marker -> telemetry-only
        async def on_event(self, event: ObservationEvent) -> None:
            raise RuntimeError("boom")

    ok = ListObserver()
    emitter = Emitter([_BoomTelemetry(), ok])

    await emitter.emit(_message_event())  # must NOT raise (isolation preserved)
    assert len(ok.events) == 1


# --- 4. happy path unchanged --------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_agent_turn_and_episode_are_retrievable() -> None:
    store = InMemoryMemoryStore()
    authority = MemoryWriteAuthority(store)
    emitter = Emitter(
        [
            AgentTurnMemoryObserver(authority),
            EpisodeProjectorObserver(authority, workflow_name="goals"),
        ]
    )

    await emitter.emit(_message_event())
    await emitter.emit(_terminal_event())

    agent_entries = await AgentMemory(store, "writer").scan()
    assert len(agent_entries) == 1
    assert agent_entries[0].value["content_preview"] == "draft-text"

    episodes = await EpisodicMemory(store, "goals").recent_episodes()
    assert len(episodes) == 1
    assert episodes[0].value["terminal_event"] == "session_completed"
