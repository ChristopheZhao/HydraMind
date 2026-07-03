"""S5a — durable, control-owned authoritative native MAS interaction state.

Proves the additive durable interaction schema is recorded through the control
boundary as a team runs (record-only), is re-readable with FULL authoritative
message content (not the bounded preview), carries the typed protocol outcome,
bumps a version/append sequence, survives a fresh SessionService over the same
store, and stays idempotent under duplicate delivery — while the kernel
Interaction stays ephemeral and InteractionLogRecord remains a projection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hydramind.control import (
    ControlPlane,
    DurableInteraction,
    InteractionLogRecord,
    SessionService,
    SqliteSessionStore,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.control.interaction_state import durable_interaction_id
from hydramind.control.store import InMemorySessionStore
from hydramind.harness import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    StopReason,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.harness.provider import ModelProvider
from hydramind.kernel.contracts import InteractionStatus, TurnStatus
from hydramind.mas import AgentSpec, SharedWorkspace, TeamSpec
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.execution_harness import ProviderExecutionHarnessRuntime
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.tools import ToolContext

NODE_KEY = "collaborate"
EXECUTION_ID = "exec"

# A long body so the durable message proves FULL content is stored, unlike the
# InteractionLogRecord preview (capped at 512 chars).
LONG_BODY = "writer says: " + ("X" * 900)


class _ContentHarness(ModelProvider):
    """Returns per-role scripted content through the provider seam."""

    name = "content"

    def __init__(self, content_by_role: dict[str, str]) -> None:
        self._content_by_role = content_by_role

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
    ) -> InvocationResult:
        del messages, tools, system, model_hint, max_tokens
        role = role or "unknown"
        return InvocationResult(
            content=self._content_by_role.get(role, f"done-{role}"),
            stop_reason=StopReason.END_TURN,
            model_id=f"model-{role}",
        )

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        del model_hint
        return f"model-{role or 'unknown'}"

    def context_limit(self, role: str | None = None) -> int:
        del role
        return 10_000


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="durable-interaction-wf",
        nodes=(WorkflowNodeSpec(key=NODE_KEY, role="coordinator"),),
    )


async def _run_team(
    *,
    control: ControlPlane,
    session_id: str,
    team: TeamSpec,
    provider: ModelProvider,
    records: list[InteractionLogRecord] | None = None,
    execution_id: str = EXECUTION_ID,
) -> InvocationResult:
    async def emit_trace(*_: Any, **__: Any) -> None:
        return None

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    async def record_interaction_event(
        record: InteractionLogRecord,
    ) -> InteractionLogRecord:
        # Run the projection seam through control too (mirrors the live wiring),
        # so the preview projection coexists with the authoritative durable state.
        recorded = await control.record_interaction_event(record)
        if records is not None:
            records.append(recorded)
        return recorded

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(
            node_key=node_key, role=role
        ),
        record_interaction_event=record_interaction_event,
        durable_recorder=control,
    )
    request = CollaborationExecutionRequest(
        session_id=session_id,
        node_key=NODE_KEY,
        execution_id=execution_id,
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )
    return await executor.invoke_team(request)


async def _make_control(store: Any) -> tuple[ControlPlane, SessionService, str]:
    service = SessionService(store)
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    return control, service, session.id


@pytest.mark.asyncio
async def test_durable_authoritative_state_recorded_with_full_content() -> None:
    control, service, session_id = await _make_control(InMemorySessionStore())
    team = TeamSpec(
        id="pipeline-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
        workspace=SharedWorkspace(id="draft-room", scope="team"),
    )
    harness = _ContentHarness(
        {"writer": LONG_BODY, "reviewer": "looks good"}
    )

    await _run_team(
        control=control, session_id=session_id, team=team, provider=harness
    )

    interaction = await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert isinstance(interaction, DurableInteraction)
    # Interaction completed and is the authoritative aggregate.
    assert interaction.status is InteractionStatus.COMPLETED
    assert interaction.team_id == "pipeline-team"
    assert interaction.node_key == NODE_KEY
    assert interaction.execution_id == EXECUTION_ID
    assert interaction.workspace_id == "draft-room"
    assert interaction.member_ids == ("writer", "reviewer")
    assert interaction.recovered_from_turn_index is None  # S5a is not recovery

    # Turns: both COMPLETED, in order, with the unused S5b lease fields all None.
    assert [t.turn_index for t in interaction.turns] == [0, 1]
    assert [t.agent_id for t in interaction.turns] == ["writer", "reviewer"]
    assert all(t.status is TurnStatus.COMPLETED for t in interaction.turns)
    for turn in interaction.turns:
        assert turn.turn_lease_token is None
        assert turn.turn_lease_owner is None
        assert turn.turn_lease_expires_at is None
        assert turn.last_heartbeat_at is None
        assert turn.completed_at is not None

    # Messages carry FULL authoritative content (NOT the truncated preview).
    writer_msg = interaction.messages[0]
    assert writer_msg.sender == "writer"
    assert writer_msg.content == LONG_BODY
    assert len(writer_msg.content) > 512  # would be truncated in the projection
    assert interaction.messages[1].content == "looks good"

    # The InteractionLogRecord projection stays a bounded preview (<= 512), so
    # the durable record is provably NOT the same as the preview projection.
    session = await service.get_session(session_id)
    log_entries = session.metadata["interaction_log"]["entries"]
    previews = [
        e["content_preview"]
        for e in log_entries
        if e["event_kind"] == "message_sent" and e["actor"] == "writer"
    ]
    assert len(previews) == 1
    assert len(previews[0]) <= 512
    assert previews[0] != LONG_BODY  # preview is truncated; durable is full


@pytest.mark.asyncio
async def test_version_increments_per_durable_append() -> None:
    control, _service, session_id = await _make_control(InMemorySessionStore())
    team = TeamSpec(
        id="ver-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    await _run_team(
        control=control,
        session_id=session_id,
        team=team,
        provider=_ContentHarness({}),
    )

    interaction = await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert interaction is not None
    # version 1 = start; +1 per turn (2) + per message (2) + complete (1) = 6.
    # COLLECT aggregation records no outcome, so no extra bump there.
    assert interaction.version == 6


@pytest.mark.asyncio
async def test_vote_outcome_recorded_typed_and_durable() -> None:
    control, _service, session_id = await _make_control(InMemorySessionStore())
    team = TeamSpec(
        id="vote-team",
        members=(
            AgentSpec(id="a", role="voter"),
            AgentSpec(id="b", role="voter"),
            AgentSpec(id="c", role="voter"),
        ),
        protocol={
            "mode": "vote",
            "topology": "broadcast",
            "aggregation": "vote",
            "arbitration": "majority",
        },
    )
    # Equivalent wording ("yes" / "Yes." / "no") collapses canonically (S3b).
    contents = iter(["yes", "Yes.", "no"])

    class _SeqProvider(_ContentHarness):
        async def complete(
            self,
            messages: list[Message],
            *,
            tools: list[ToolSpec] | None = None,
            system: str | None = None,
            role: str,
            model_hint: ModelHint = ModelHint.BALANCED,
            max_tokens: int | None = None,
        ) -> InvocationResult:
            del messages, tools, system, model_hint, max_tokens
            return InvocationResult(
                content=next(contents),
                stop_reason=StopReason.END_TURN,
                model_id=f"model-{role}",
            )

    await _run_team(
        control=control,
        session_id=session_id,
        team=team,
        provider=_SeqProvider({}),
    )

    interaction = await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert interaction is not None
    assert interaction.outcome is not None
    vote = interaction.outcome.vote
    assert vote is not None
    assert interaction.outcome.coordinator is None
    # "yes" and "Yes." canonicalize to one key -> winner "yes" with count 2.
    assert vote.tally == {"yes": 2, "no": 1}
    assert vote.winner == "yes"
    assert vote.winner_count == 2
    assert vote.tie is False


@pytest.mark.asyncio
async def test_coordinator_outcome_recorded_typed_and_durable() -> None:
    control, _service, session_id = await _make_control(InMemorySessionStore())
    team = TeamSpec(
        id="coord-team",
        members=(
            AgentSpec(id="lead", role="lead"),
            AgentSpec(id="helper", role="helper"),
        ),
        protocol={
            "mode": "team",
            "topology": "coordinator",
            "aggregation": "coordinator_summary",
            "coordinator_id": "lead",
        },
    )
    harness = _ContentHarness({"lead": "final summary by lead", "helper": "draft"})

    await _run_team(
        control=control, session_id=session_id, team=team, provider=harness
    )

    interaction = await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert interaction is not None
    assert interaction.outcome is not None
    coordinator = interaction.outcome.coordinator
    assert coordinator is not None
    assert interaction.outcome.vote is None
    assert coordinator.coordinator_id == "lead"
    assert coordinator.summary == "final summary by lead"


@pytest.mark.asyncio
async def test_durable_state_survives_fresh_session_service(tmp_path: Path) -> None:
    store_path = tmp_path / "sessions.db"
    control, _service, session_id = await _make_control(
        SqliteSessionStore(store_path)
    )
    team = TeamSpec(
        id="restart-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    await _run_team(
        control=control,
        session_id=session_id,
        team=team,
        provider=_ContentHarness({"writer": LONG_BODY, "reviewer": "ok"}),
    )

    # Fresh SessionService over the SAME on-disk store == restart.
    reopened = SessionService(SqliteSessionStore(store_path))
    interaction = await reopened.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert interaction is not None
    assert interaction.status is InteractionStatus.COMPLETED
    assert [t.status for t in interaction.turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED,
    ]
    # Authoritative FULL content is re-read after restart, not a preview.
    assert interaction.messages[0].content == LONG_BODY


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_double_durable_state() -> None:
    """S4c idempotency composes with S5a: a replayed turn/message is not doubled."""

    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    session_id = session.id

    await control.start_interaction(
        session_id,
        interaction_id=durable_interaction_id(session_id, NODE_KEY),
        node_key=NODE_KEY,
        execution_id=EXECUTION_ID,
        team_id="dup-team",
        protocol_mode="team",
        topology="pipeline",
        member_ids=("writer",),
    )
    first = await control.record_interaction_turn(
        session_id,
        interaction_id=durable_interaction_id(session_id, NODE_KEY),
        turn_index=0,
        agent_id="writer",
        status=TurnStatus.COMPLETED,
    )
    await control.record_interaction_message(
        session_id,
        interaction_id=durable_interaction_id(session_id, NODE_KEY),
        turn_index=0,
        sender="writer",
        content=LONG_BODY,
    )
    version_after_first_round = (
        await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    ).version

    # Duplicate delivery of the SAME logical turn + message.
    again_turn = await control.record_interaction_turn(
        session_id,
        interaction_id=durable_interaction_id(session_id, NODE_KEY),
        turn_index=0,
        agent_id="writer",
        status=TurnStatus.COMPLETED,
    )
    again_msg = await control.record_interaction_message(
        session_id,
        interaction_id=durable_interaction_id(session_id, NODE_KEY),
        turn_index=0,
        sender="writer",
        content=LONG_BODY,
    )

    assert first.turns[0].turn_index == 0
    # No double-append: still exactly one turn + one message, version unchanged.
    assert len(again_turn.turns) == 1
    assert len(again_msg.messages) == 1
    assert again_msg.version == version_after_first_round


@pytest.mark.asyncio
async def test_interaction_failure_records_durable_failed_status() -> None:
    """A team that raises mid-run records FAILED durable state through control."""

    control, _service, session_id = await _make_control(InMemorySessionStore())
    team = TeamSpec(
        id="boom-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )

    class _BoomProvider(_ContentHarness):
        async def complete(
            self,
            messages: list[Message],
            *,
            tools: list[ToolSpec] | None = None,
            system: str | None = None,
            role: str,
            model_hint: ModelHint = ModelHint.BALANCED,
            max_tokens: int | None = None,
        ) -> InvocationResult:
            del messages, tools, system, role, model_hint, max_tokens
            raise RuntimeError("member exploded")

    with pytest.raises(RuntimeError, match="member exploded"):
        await _run_team(
            control=control,
            session_id=session_id,
            team=team,
            provider=_BoomProvider({}),
        )

    interaction = await control.get_durable_interaction(session_id, durable_interaction_id(session_id, NODE_KEY))
    assert interaction is not None
    assert interaction.status is InteractionStatus.FAILED
    assert interaction.error is not None
    assert "member exploded" in interaction.error


@pytest.mark.asyncio
async def test_kernel_interaction_stays_ephemeral_and_log_is_projection() -> None:
    """Durable state is additive: the kernel Interaction is not the durable store,
    and InteractionLogRecord remains a bounded preview projection."""

    control, service, session_id = await _make_control(InMemorySessionStore())
    records: list[InteractionLogRecord] = []
    team = TeamSpec(
        id="proj-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    await _run_team(
        control=control,
        session_id=session_id,
        team=team,
        provider=_ContentHarness({"writer": LONG_BODY, "reviewer": "ok"}),
        records=records,
    )

    # The projection still exists (kinds unchanged) and is bounded.
    kinds = [r.event_kind.value for r in records]
    assert "interaction_started" in kinds
    assert "message_sent" in kinds
    assert "interaction_completed" in kinds
    for record in records:
        if record.content_preview is not None:
            assert len(record.content_preview) <= 512

    # The kernel Interaction value type is NOT what is persisted: the durable
    # aggregate is a DurableInteraction (control type), and RuntimeSession holds
    # it under durable_interactions, separate from the preview log.
    session = await service.get_session(session_id)
    assert durable_interaction_id(session_id, NODE_KEY) in session.durable_interactions
    durable = session.durable_interactions[durable_interaction_id(session_id, NODE_KEY)]
    assert isinstance(durable, DurableInteraction)
    assert "interaction_log" in session.metadata  # projection coexists
