"""Contract tests for versioned/typed load-bearing event-detail payloads (S3a).

These guard the cross-layer evidence contract between observation events and the
memory projectors: the consumed detail of ``AGENT_MESSAGE_SENT``,
``AGENT_TURN_*`` and ``TOOL_CALL_STARTED`` must carry a ``schema_version`` and
round-trip through the typed models without losing the consumed fields, and a
newer schema_version must be rejected explicitly rather than silently mis-read.
"""

from __future__ import annotations

import pytest

from hydramind.harness.base import ModelHint
from hydramind.kernel.contracts import Turn
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.observability import ObservationEventKind
from hydramind.observability.event_details import (
    EVENT_DETAIL_SCHEMA_VERSION,
    AgentMessageSentDetail,
    AgentTurnDetail,
    ToolCallStartedDetail,
)
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
)
from hydramind.orchestration.collaboration_events import NativeTeamEventEmitter
from hydramind.orchestration.collaboration_interaction import team_turn_detail


def _team() -> TeamSpec:
    return TeamSpec(
        id="contract-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )


def _turn_detail() -> dict[str, object]:
    team = _team()
    return team_turn_detail(
        team=team,
        member=team.members[0],
        turn=Turn(index=0, agent_id="writer"),
        interaction_id="interaction-contract",
        workspace_id="room-1",
    )


def test_team_turn_detail_carries_schema_version() -> None:
    detail = _turn_detail()
    assert detail["schema_version"] == EVENT_DETAIL_SCHEMA_VERSION
    # Wire shape preserved: the pre-S3a consumed keys remain present.
    for key in ("interaction_id", "turn_index", "team_id", "role", "topology"):
        assert key in detail


def test_agent_message_sent_detail_round_trips() -> None:
    raw = {**_turn_detail(), "content_preview": "draft v1"}
    typed = AgentMessageSentDetail.from_event_detail(raw)

    assert typed.schema_version == EVENT_DETAIL_SCHEMA_VERSION
    assert typed.interaction_id == "interaction-contract"
    assert typed.turn_index == 0
    assert typed.role == "writer"
    assert typed.topology == "pipeline"
    assert typed.workspace_id == "room-1"
    assert typed.content_preview == "draft v1"
    # No loss of consumed fields vs the emitted detail.
    dumped = typed.model_dump()
    for key, value in raw.items():
        assert dumped[key] == value


def test_agent_turn_detail_round_trips() -> None:
    typed = AgentTurnDetail.from_event_detail(_turn_detail())
    assert typed.schema_version == EVENT_DETAIL_SCHEMA_VERSION
    assert typed.turn_index == 0
    assert typed.team_id == "contract-team"


def test_tool_call_started_detail_round_trips() -> None:
    raw = {
        "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
        "round": 1,
        "tool_call_id": "call-1",
        "tool_name": "search",
        "arguments": {"q": "x"},
    }
    typed = ToolCallStartedDetail.from_event_detail(raw)
    assert typed.schema_version == EVENT_DETAIL_SCHEMA_VERSION
    assert typed.tool_name == "search"
    assert typed.round == 1
    assert typed.tool_call_id == "call-1"
    # extra="allow" keeps unmodeled keys (e.g. arguments) available.
    assert typed.model_dump()["arguments"] == {"q": "x"}


def test_missing_schema_version_is_accepted_as_current() -> None:
    # Pre-S3a payloads without schema_version remain readable.
    typed = AgentMessageSentDetail.from_event_detail(
        {"interaction_id": "i", "turn_index": 3, "content_preview": "p"}
    )
    assert typed.interaction_id == "i"
    assert typed.turn_index == 3


@pytest.mark.parametrize(
    "model",
    [AgentMessageSentDetail, AgentTurnDetail, ToolCallStartedDetail],
)
def test_future_schema_version_is_rejected_explicitly(
    model: type[AgentMessageSentDetail | AgentTurnDetail | ToolCallStartedDetail],
) -> None:
    with pytest.raises(ValueError, match="unsupported event-detail schema_version"):
        model.from_event_detail({"schema_version": EVENT_DETAIL_SCHEMA_VERSION + 1})


async def test_emitted_agent_turn_events_carry_schema_version() -> None:
    captured: list[tuple[ObservationEventKind, dict[str, object]]] = []

    async def emit(
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, object] | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None:
        captured.append((kind, dict(detail or {})))

    emitter = NativeTeamEventEmitter(
        model_hint=ModelHint.BALANCED, emit_trace=emit
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="plan",
        execution_id="exec",
        trace_id="trace",
        messages=[],
        system="",
        tools=None,
        agent_role="writer",
        node_config={},
    )
    turn_detail = _turn_detail()

    await emitter.turn_started(
        request=request, actor="writer", turn_detail=turn_detail
    )
    await emitter.turn_completed(
        request=request,
        actor="writer",
        turn_detail=turn_detail,
        member_result={"stop_reason": "end_turn", "tool_call_count": 0},
    )
    await emitter.message_sent(
        request=request, actor="writer", turn_detail=turn_detail, content="hi"
    )

    kinds = {kind for kind, _ in captured}
    assert kinds == {
        ObservationEventKind.AGENT_TURN_STARTED,
        ObservationEventKind.AGENT_TURN_COMPLETED,
        ObservationEventKind.AGENT_MESSAGE_SENT,
    }
    for _, detail in captured:
        assert detail["schema_version"] == EVENT_DETAIL_SCHEMA_VERSION
