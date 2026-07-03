"""Trace-to-episode projection tests."""

from __future__ import annotations

import pytest

from hydramind.memory import (
    AgentTurnMemoryObserver,
    EpisodeProjectorObserver,
    EpisodicMemory,
    InMemoryMemoryStore,
    MemoryScope,
    MemoryWriteAuthority,
    MemoryWriteClass,
    build_episode_summary,
)
from hydramind.observability import ObservationEvent, ObservationEventKind


def _event(kind: ObservationEventKind, **detail: object) -> ObservationEvent:
    return ObservationEvent(
        kind=kind,
        session_id="sess-1",
        node_key="plan",
        trace_id="trace-1",
        execution_id="exec-1",
        detail=dict(detail),
    )


def _agent_event(
    kind: ObservationEventKind, actor: str, turn_index: int
) -> ObservationEvent:
    return ObservationEvent(
        kind=kind,
        session_id="sess-1",
        node_key="collaborate",
        trace_id="trace-1",
        execution_id="exec-1",
        actor=actor,
        detail={"turn_index": turn_index, "interaction_id": "interaction-exec-1"},
    )


def test_episode_summary_is_compact_and_trace_referenced() -> None:
    summary = build_episode_summary(
        [
            _event(ObservationEventKind.NODE_EXECUTION_STARTED),
            ObservationEvent(
                kind=ObservationEventKind.MODEL_INVOKE_COMPLETED,
                session_id="sess-1",
                node_key="plan",
                trace_id="trace-2",
                execution_id="exec-2",
            ),
            _event(ObservationEventKind.TOOL_CALL_STARTED, tool_name="search.web"),
            _event(ObservationEventKind.SESSION_COMPLETED),
        ]
    )

    assert summary["session_id"] == "sess-1"
    assert summary["terminal_event"] == "session_completed"
    assert summary["trace_ids"] == ["trace-1", "trace-2"]
    assert summary["execution_ids"] == ["exec-1", "exec-2"]
    assert summary["tool_names"] == ["search.web"]
    assert "events" not in summary
    # No agent turns in this trajectory -> empty agent dimension (still compact).
    assert summary["agent_turns"] == []
    assert summary["agent_turn_counts"] == {}


def test_episode_summary_recovers_who_acted_next() -> None:
    # DEV-15/39: the ordered agent/turn sequence is recoverable from the new
    # first-class turn events, while raw trajectory is NOT stored.
    summary = build_episode_summary(
        [
            _event(ObservationEventKind.NODE_EXECUTION_STARTED),
            _agent_event(ObservationEventKind.AGENT_TURN_STARTED, "writer", 0),
            _agent_event(ObservationEventKind.AGENT_TURN_COMPLETED, "writer", 0),
            _agent_event(ObservationEventKind.AGENT_MESSAGE_SENT, "writer", 0),
            _agent_event(ObservationEventKind.AGENT_TURN_STARTED, "reviewer", 1),
            _agent_event(ObservationEventKind.AGENT_TURN_COMPLETED, "reviewer", 1),
            _agent_event(ObservationEventKind.AGENT_MESSAGE_SENT, "reviewer", 1),
            _agent_event(ObservationEventKind.AGENT_TURN_STARTED, "writer", 2),
            _agent_event(ObservationEventKind.AGENT_MESSAGE_SENT, "writer", 2),
            _event(ObservationEventKind.SESSION_COMPLETED),
        ]
    )

    # The member acting order is reconstructable: writer -> reviewer -> writer.
    started_order = [
        t["agent_id"]
        for t in summary["agent_turns"]
        if t["kind"] == "agent_turn_started"
    ]
    assert started_order == ["writer", "reviewer", "writer"]
    # Per-agent aggregation counts every first-class agent turn event.
    assert summary["agent_turn_counts"] == {"reviewer": 3, "writer": 5}
    # Still compact: no raw trajectory.
    assert "events" not in summary


@pytest.mark.asyncio
async def test_agent_turn_observer_writes_agent_scope_entry() -> None:
    # DEV-14: AgentTurnMemoryObserver is a production MemoryScope.AGENT writer.
    store = InMemoryMemoryStore()
    observer = AgentTurnMemoryObserver(MemoryWriteAuthority(store))

    # Non-message agent events are ignored (only AGENT_MESSAGE_SENT records output).
    await observer.on_event(
        _agent_event(ObservationEventKind.AGENT_TURN_STARTED, "writer", 0)
    )
    await observer.on_event(
        ObservationEvent(
            kind=ObservationEventKind.AGENT_MESSAGE_SENT,
            session_id="sess-1",
            node_key="collaborate",
            actor="writer",
            detail={
                "turn_index": 0,
                "interaction_id": "interaction-exec-1",
                "role": "writer",
                "content_preview": "draft-text",
            },
        )
    )

    entries = await store.scan(MemoryScope.AGENT, "writer")
    assert len(entries) == 1
    assert entries[0].scope is MemoryScope.AGENT
    assert entries[0].value["content_preview"] == "draft-text"
    assert entries[0].metadata["source"] == "observability_agent_turn_projection"
    # S4a/F6: the control-owned authority stamps the write class + version.
    assert (
        entries[0].metadata["memory_write_class"]
        == MemoryWriteClass.PROMPT_AFFECTING.value
    )
    assert entries[0].metadata["memory_write_version"] == 0


@pytest.mark.asyncio
async def test_episode_projector_records_on_terminal_event() -> None:
    store = InMemoryMemoryStore()
    memory = EpisodicMemory(store, "workflow")
    projector = EpisodeProjectorObserver(
        MemoryWriteAuthority(store), workflow_name="workflow"
    )

    await projector.on_event(_event(ObservationEventKind.NODE_EXECUTION_STARTED))
    assert await memory.recent_episodes() == []
    assert projector.buffered_session_ids() == ("sess-1",)

    await projector.on_event(_event(ObservationEventKind.SESSION_COMPLETED))

    episodes = await memory.recent_episodes()
    assert len(episodes) == 1
    assert episodes[0].value["terminal_event"] == "session_completed"
    assert episodes[0].metadata["source"] == "observability_trace_projection"
    assert projector.buffered_session_ids() == ()
