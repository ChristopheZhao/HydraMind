"""Native team event emission boundary tests."""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.harness import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    StopReason,
    ToolSpec,
)
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.observability import ObservationEventKind
from hydramind.orchestration.collaboration import CollaborationExecutionRequest
from hydramind.orchestration.collaboration_events import NativeTeamEventEmitter


@pytest.fixture
def team_request() -> CollaborationExecutionRequest:
    return CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=[ToolSpec(name="search.web", description="search")],
        agent_role="coordinator",
        node_config={},
    )


@pytest.fixture
def team() -> TeamSpec:
    return TeamSpec(
        id="team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="lead", role="lead"),
        ),
        protocol={
            "topology": "coordinator",
            "aggregation": "coordinator_summary",
            "coordinator_id": "lead",
        },
    )


@pytest.mark.asyncio
async def test_invocation_events_preserve_team_runtime_detail(
    team_request: CollaborationExecutionRequest,
    team: TeamSpec,
) -> None:
    emitted: list[tuple[ObservationEventKind, str | None, dict[str, Any]]] = []

    async def emit_trace(
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, Any] | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None:
        del session_id, node_key, execution_id, trace_id, level
        emitted.append((kind, actor, detail or {}))

    emitter = NativeTeamEventEmitter(
        model_hint=ModelHint.POWERFUL,
        emit_trace=emit_trace,
    )

    await emitter.invocation_started(request=team_request, team=team)
    await emitter.invocation_completed(
        request=team_request,
        team=team,
        invocation=InvocationResult(
            content="final team response",
            stop_reason=StopReason.END_TURN,
            model_id="team",
        ),
    )

    assert [event[0] for event in emitted] == [
        ObservationEventKind.MODEL_INVOKE_STARTED,
        ObservationEventKind.MODEL_INVOKE_COMPLETED,
    ]
    started = emitted[0][2]
    assert started["role"] == "coordinator"
    assert started["message_count"] == 1
    assert started["tool_count"] == 1
    assert started["model_hint"] == "powerful"
    assert started["team_id"] == "team"
    assert started["member_ids"] == ["writer", "lead"]

    completed = emitted[1][2]
    assert completed["model_id"] == "team"
    assert completed["stop_reason"] == "end_turn"
    assert completed["content_preview"] == "final team response"
    assert completed["usage"] == {
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


@pytest.mark.asyncio
async def test_turn_events_preserve_actor_and_detail_shape(
    team_request: CollaborationExecutionRequest,
) -> None:
    emitted: list[tuple[ObservationEventKind, str | None, dict[str, Any]]] = []

    async def emit_trace(
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, Any] | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None:
        del session_id, node_key, execution_id, trace_id, level
        emitted.append((kind, actor, detail or {}))

    emitter = NativeTeamEventEmitter(
        model_hint=ModelHint.BALANCED,
        emit_trace=emit_trace,
    )
    turn_detail = {
        "interaction_id": "interaction-exec",
        "turn_index": 1,
        "team_id": "team",
        "role": "lead",
        "topology": "coordinator",
    }

    await emitter.coordinator_handoff(
        request=team_request,
        actor="lead",
        turn_detail=turn_detail,
    )
    await emitter.turn_started(
        request=team_request,
        actor="lead",
        turn_detail=turn_detail,
    )
    await emitter.turn_completed(
        request=team_request,
        actor="lead",
        turn_detail=turn_detail,
        member_result={"stop_reason": "end_turn", "tool_call_count": 2},
    )
    await emitter.message_sent(
        request=team_request,
        actor="lead",
        turn_detail=turn_detail,
        content="approved",
    )
    await emitter.vote(
        request=team_request,
        actor="lead",
        turn_detail=turn_detail,
        content=" approved ",
    )

    assert [event[0] for event in emitted] == [
        ObservationEventKind.AGENT_HANDOFF,
        ObservationEventKind.AGENT_TURN_STARTED,
        ObservationEventKind.AGENT_TURN_COMPLETED,
        ObservationEventKind.AGENT_MESSAGE_SENT,
        ObservationEventKind.AGENT_VOTE,
    ]
    assert [event[1] for event in emitted] == ["lead"] * 5
    assert emitted[0][2]["handoff"] == "coordinator"
    assert emitted[2][2]["tool_call_count"] == 2
    assert emitted[3][2]["content_preview"] == "approved"
    assert emitted[4][2]["vote"] == "approved"
