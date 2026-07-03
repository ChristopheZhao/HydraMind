"""Trace-to-episode projection for episodic memory."""

from __future__ import annotations

from collections import Counter

from hydramind.memory.base import MemoryScope
from hydramind.memory.ownership import MemoryWriteAuthority, MemoryWriteClass
from hydramind.observability.event_details import (
    AgentMessageSentDetail,
    AgentTurnDetail,
    ToolCallStartedDetail,
)
from hydramind.observability.events import ObservationEvent, ObservationEventKind

_TERMINAL_KINDS = {
    ObservationEventKind.SESSION_COMPLETED,
    ObservationEventKind.SESSION_FAILED,
    ObservationEventKind.SESSION_CANCELLED,
}

_AGENT_TURN_KINDS = {
    ObservationEventKind.AGENT_TURN_STARTED,
    ObservationEventKind.AGENT_TURN_COMPLETED,
    ObservationEventKind.AGENT_MESSAGE_SENT,
}


class AgentTurnMemoryObserver:
    """Observer that records each agent's turn output to ``MemoryScope.AGENT``.

    The PRODUCTION write path for ``MemoryScope.AGENT`` (DEV-14): on an
    ``AGENT_MESSAGE_SENT`` event (the agent's produced turn output) it appends
    a per-agent record keyed by the event ``actor`` (the team member id), so an
    agent's own turn history is recoverable independently of the flat session
    trajectory.

    This write is PROMPT_AFFECTING (S4a/F6): the record is read back into later
    prompts via ``StoreMemoryContextRetriever`` → ``AgentPromptContextBuilder``.
    It therefore goes through the control-owned ``MemoryWriteAuthority``
    (versioned, durable append, raises on failure) and is marked ``critical``
    so the emitter surfaces a write failure instead of swallowing it. It still
    never touches ``RuntimeSession`` or the control plane — it owns memory state
    only.
    """

    #: Critical-observer marker read by the emitter; a failure surfaces.
    memory_write_class: MemoryWriteClass = MemoryWriteClass.PROMPT_AFFECTING
    critical: bool = True

    def __init__(self, authority: MemoryWriteAuthority) -> None:
        self._authority = authority

    async def on_event(self, event: ObservationEvent) -> None:
        if event.kind is not ObservationEventKind.AGENT_MESSAGE_SENT:
            return
        if not event.actor:
            return
        detail = AgentMessageSentDetail.from_event_detail(event.detail)
        # S4c: stable key for this logical turn so a duplicate queue delivery
        # (replaying the same AGENT_MESSAGE_SENT) does not double-append the
        # prompt-affecting per-agent turn record.
        turn = "-" if detail.turn_index is None else str(detail.turn_index)
        idempotency_key = (
            f"agent-turn:{event.session_id}:{event.execution_id or '-'}:"
            f"{detail.interaction_id or '-'}:{turn}:{event.actor}"
        )
        await self._authority.append(
            MemoryScope.AGENT,
            event.actor,
            f"turn.{event.session_id}",
            {
                "session_id": event.session_id,
                "interaction_id": detail.interaction_id,
                "turn_index": detail.turn_index,
                "role": detail.role,
                "content_preview": detail.content_preview,
            },
            write_class=MemoryWriteClass.PROMPT_AFFECTING,
            metadata={
                "agent_id": event.actor,
                "node_key": event.node_key,
                "source": "observability_agent_turn_projection",
            },
            idempotency_key=idempotency_key,
        )


class EpisodeProjectorObserver:
    """Observer that writes compact episode summaries, not raw trajectories.

    The episode summary is PROMPT_AFFECTING (S4a/F6): ``recent_episodes`` are
    retrieved back into later prompts. The terminal write therefore goes through
    the control-owned ``MemoryWriteAuthority`` (versioned, durable append,
    raises on failure) and the observer is marked ``critical`` so the emitter
    surfaces a write failure instead of swallowing it.
    """

    #: Critical-observer marker read by the emitter; a failure surfaces.
    memory_write_class: MemoryWriteClass = MemoryWriteClass.PROMPT_AFFECTING
    critical: bool = True

    def __init__(
        self, authority: MemoryWriteAuthority, *, workflow_name: str
    ) -> None:
        self._authority = authority
        self._workflow_name = workflow_name
        self._events_by_session: dict[str, list[ObservationEvent]] = {}

    async def on_event(self, event: ObservationEvent) -> None:
        events = self._events_by_session.setdefault(event.session_id, [])
        events.append(event)
        if event.kind in _TERMINAL_KINDS:
            summary = build_episode_summary(events)
            # S4c: one episode summary per terminal session. A duplicate queue
            # delivery that replays the terminal event must not append a second
            # prompt-affecting episode record for the same session.
            idempotency_key = f"episode:{event.session_id}:{event.kind.value}"
            await self._authority.append(
                MemoryScope.WORKFLOW,
                self._workflow_name,
                f"episode.{event.session_id}",
                summary,
                write_class=MemoryWriteClass.PROMPT_AFFECTING,
                metadata={
                    "trace_ids": summary["trace_ids"],
                    "execution_ids": summary["execution_ids"],
                    "source": "observability_trace_projection",
                },
                idempotency_key=idempotency_key,
            )
            self._events_by_session.pop(event.session_id, None)

    def buffered_session_ids(self) -> tuple[str, ...]:
        """Return sessions with pending non-terminal events, for tests/ops."""

        return tuple(sorted(self._events_by_session))


def build_episode_summary(events: list[ObservationEvent]) -> dict[str, object]:
    """Create a compact trajectory-level summary from observation events."""

    counts = Counter(event.kind.value for event in events)
    node_keys = sorted({event.node_key for event in events if event.node_key})
    trace_ids = sorted({event.trace_id for event in events if event.trace_id})
    execution_ids = sorted(
        {event.execution_id for event in events if event.execution_id}
    )
    tool_names = []
    for event in events:
        if event.kind is ObservationEventKind.TOOL_CALL_STARTED:
            name = ToolCallStartedDetail.from_event_detail(event.detail).tool_name
            if isinstance(name, str):
                tool_names.append(name)
    terminal = next((event.kind.value for event in reversed(events) if event.kind in _TERMINAL_KINDS), None)
    agent_turns, agent_turn_counts = _agent_dimension(events)
    return {
        "session_id": events[-1].session_id if events else "",
        "terminal_event": terminal,
        "event_count": len(events),
        "event_counts": dict(sorted(counts.items())),
        "node_keys": node_keys,
        "trace_ids": trace_ids,
        "execution_ids": execution_ids,
        "tool_names": tool_names,
        "agent_turns": agent_turns,
        "agent_turn_counts": agent_turn_counts,
    }


def _agent_dimension(
    events: list[ObservationEvent],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Reconstruct the ordered who-acted-next agent/turn sequence (DEV-15/39).

    Returns an ordered ``agent_turns`` list — one entry per first-class agent
    turn event, in emission order, so the member acting order is recoverable
    from the compact episode summary (no raw trajectory needed) — plus a
    per-agent turn-event count map. Keeps the summary compact: only the
    ``(agent_id, turn_index, kind)`` triple is retained, never raw content.
    """

    agent_turns: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for event in events:
        if event.kind not in _AGENT_TURN_KINDS:
            continue
        agent_id = event.actor
        if not agent_id:
            continue
        turn_detail = AgentTurnDetail.from_event_detail(event.detail)
        agent_turns.append(
            {
                "agent_id": agent_id,
                "turn_index": turn_detail.turn_index,
                "kind": event.kind.value,
            }
        )
        counts[agent_id] += 1
    return agent_turns, dict(sorted(counts.items()))
