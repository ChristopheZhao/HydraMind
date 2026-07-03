"""Focused tests for collaboration execution extraction."""

from __future__ import annotations

from typing import Any

import pytest

import hydramind.orchestration.collaboration as collaboration_module
import hydramind.orchestration.collaboration_contracts as collaboration_contracts
from hydramind.control import InteractionLogEventKind, InteractionLogRecord
from hydramind.harness import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    StopReason,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.harness.base import ToolCall
from hydramind.harness.provider import ModelProvider
from hydramind.mas import AgentSpec, SharedWorkspace, TeamSpec
from hydramind.memory import (
    AgentTurnMemoryObserver,
    InMemoryMemoryStore,
    MemoryScope,
    MemoryWriteAuthority,
)
from hydramind.observability import Emitter, ObservationEvent, ObservationEventKind
from hydramind.orchestration.agent import OrchestratorAgent
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.collaboration_logging import NativeTeamInteractionLogger
from hydramind.orchestration.collaboration_team import NativeTeamExecutor
from hydramind.orchestration.execution_harness import ProviderExecutionHarnessRuntime
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.tools import ToolContext


class _RecordingHarness(ModelProvider):
    name = "recording"

    def __init__(self) -> None:
        self.spawned_tools: dict[str, tuple[str, ...]] = {}

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
        del messages, system, model_hint, max_tokens
        role = role or "unknown"
        self.spawned_tools[role] = tuple(tool.name for tool in tools or ())
        return InvocationResult(
            content=f"done-{role}",
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


class _SeedRecordingHarness(ModelProvider):
    """Records the ``parent_context.seed_messages`` threaded into each member.

    Lets the watershed test prove a PIPELINE member reads its predecessor's output
    while BROADCAST members stay independent.
    """

    name = "seed-recording"

    def __init__(self) -> None:
        self.seed_by_role: dict[str, tuple[Message, ...]] = {}

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
        del system, tools, model_hint, max_tokens
        role = role or "unknown"
        self.seed_by_role[role] = tuple(messages[:-1])
        return InvocationResult(
            content=f"done-{role}",
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


async def _collect_team_events(
    topology: str,
    *,
    members: tuple[AgentSpec, ...],
    protocol: dict[str, Any] | None = None,
    workspace: SharedWorkspace | None = None,
    records: list[InteractionLogRecord] | None = None,
) -> list[ObservationEvent]:
    """Run a team and capture every emitted ObservationEvent via a real Emitter.

    Drives the full executor -> emit_trace -> Emitter -> Observer path so the
    per-turn first-class events (with their ``actor`` attribution) can be
    asserted end-to-end (S99).
    """

    backend = _SeedRecordingHarness()
    collected: list[ObservationEvent] = []

    class _Collector:
        async def on_event(self, event: ObservationEvent) -> None:
            collected.append(event)

    emitter = Emitter([_Collector()])

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
        await emitter.emit(
            ObservationEvent(
                kind=kind,
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                level=level,
                actor=actor,
                detail=detail or {},
            )
        )

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    async def record_interaction_event(
        record: InteractionLogRecord,
    ) -> InteractionLogRecord:
        if records is None:
            raise AssertionError("unexpected interaction log record")
        records.append(record)
        return record

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
        record_interaction_event=(
            record_interaction_event if records is not None else None
        ),
    )
    team = TeamSpec(
        id=f"{topology}-events-team",
        members=members,
        protocol=protocol or {"topology": topology},
        workspace=workspace,
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )
    await executor.invoke_team(request)
    return collected


@pytest.mark.asyncio
async def test_multi_member_interaction_emits_per_member_turn_events() -> None:
    # DEV-16/25/34: a 2-member interaction is observable as per-member attributed
    # turns (actor=member.id), not a single MODEL_INVOKE.
    events = await _collect_team_events(
        "pipeline",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
    )

    started = [e for e in events if e.kind is ObservationEventKind.AGENT_TURN_STARTED]
    completed = [
        e for e in events if e.kind is ObservationEventKind.AGENT_TURN_COMPLETED
    ]
    messages = [e for e in events if e.kind is ObservationEventKind.AGENT_MESSAGE_SENT]

    assert [e.actor for e in started] == ["writer", "reviewer"]
    assert [e.actor for e in completed] == ["writer", "reviewer"]
    assert [e.actor for e in messages] == ["writer", "reviewer"]
    # Per-turn detail carries interaction identity + turn index (DEV-34).
    assert [e.detail["turn_index"] for e in started] == [0, 1]
    assert all(
        e.detail["interaction_id"] == "interaction-sess-collaborate" for e in started
    )
    # Backward-compat: the MODEL_INVOKE pair is still emitted.
    assert (
        sum(1 for e in events if e.kind is ObservationEventKind.MODEL_INVOKE_STARTED)
        == 1
    )


@pytest.mark.asyncio
async def test_workspace_id_is_visible_on_per_turn_events() -> None:
    events = await _collect_team_events(
        "pipeline",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        workspace=SharedWorkspace(id="draft-room", scope="team"),
    )

    started = [e for e in events if e.kind is ObservationEventKind.AGENT_TURN_STARTED]
    messages = [e for e in events if e.kind is ObservationEventKind.AGENT_MESSAGE_SENT]

    assert [e.detail["workspace_id"] for e in started] == [
        "draft-room",
        "draft-room",
    ]
    assert [e.detail["workspace_id"] for e in messages] == [
        "draft-room",
        "draft-room",
    ]


@pytest.mark.asyncio
async def test_native_team_records_control_owned_interaction_log() -> None:
    records: list[InteractionLogRecord] = []

    await _collect_team_events(
        "pipeline",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        workspace=SharedWorkspace(id="draft-room", scope="team"),
        records=records,
    )

    assert [record.event_kind for record in records] == [
        InteractionLogEventKind.INTERACTION_STARTED,
        InteractionLogEventKind.TURN_STARTED,
        InteractionLogEventKind.TURN_COMPLETED,
        InteractionLogEventKind.MESSAGE_SENT,
        InteractionLogEventKind.TURN_STARTED,
        InteractionLogEventKind.TURN_COMPLETED,
        InteractionLogEventKind.MESSAGE_SENT,
        InteractionLogEventKind.INTERACTION_COMPLETED,
    ]
    assert {record.session_id for record in records} == {"sess"}
    assert {record.node_key for record in records} == {"collaborate"}
    assert {record.execution_id for record in records} == {"exec"}
    assert {record.trace_id for record in records} == {"trace"}
    assert {record.interaction_id for record in records} == {
        "interaction-sess-collaborate"
    }
    assert {record.team_id for record in records} == {"pipeline-events-team"}
    assert {record.workspace_id for record in records} == {"draft-room"}

    messages = [
        record
        for record in records
        if record.event_kind is InteractionLogEventKind.MESSAGE_SENT
    ]
    assert [record.actor for record in messages] == ["writer", "reviewer"]
    assert [record.turn_index for record in messages] == [0, 1]
    assert [record.content_preview for record in messages] == [
        "done-writer",
        "done-reviewer",
    ]
    assert records[0].detail["member_ids"] == ["writer", "reviewer"]


@pytest.mark.asyncio
async def test_interaction_log_records_bounded_message_preview() -> None:
    records: list[InteractionLogRecord] = []

    async def record_interaction_event(
        record: InteractionLogRecord,
    ) -> InteractionLogRecord:
        records.append(record)
        return record

    team = TeamSpec(
        id="team-long",
        members=(AgentSpec(id="writer", role="writer"),),
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    await NativeTeamInteractionLogger(record_interaction_event).message_sent(
        request=request,
        team=team,
        interaction_id="interaction-exec",
        workspace_id=None,
        actor="writer",
        turn_index=0,
        content="x" * 900,
        detail={"turn_index": 0},
    )

    assert len(records) == 1
    assert records[0].content_preview is not None
    assert len(records[0].content_preview) <= 160
    assert records[0].content_preview.endswith("...")


@pytest.mark.asyncio
async def test_coordinator_topology_emits_handoff_to_coordinator() -> None:
    # COORDINATOR: control is handed to the coordinator member -> AGENT_HANDOFF.
    events = await _collect_team_events(
        "coordinator",
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
    handoffs = [e for e in events if e.kind is ObservationEventKind.AGENT_HANDOFF]
    assert [e.actor for e in handoffs] == ["lead"]
    assert handoffs[0].detail["handoff"] == "coordinator"


@pytest.mark.asyncio
async def test_agent_turn_observer_writes_agent_scoped_memory() -> None:
    # DEV-14: the AgentTurnMemoryObserver is a real MemoryScope.AGENT production
    # write path — each member's turn output lands under its own agent id.
    backend = _SeedRecordingHarness()
    store = InMemoryMemoryStore()
    emitter = Emitter([AgentTurnMemoryObserver(MemoryWriteAuthority(store))])

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
        await emitter.emit(
            ObservationEvent(
                kind=kind,
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                level=level,
                actor=actor,
                detail=detail or {},
            )
        )

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(
        id="agent-memory-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    await executor.invoke_team(request)

    writer_entries = await store.scan(MemoryScope.AGENT, "writer")
    reviewer_entries = await store.scan(MemoryScope.AGENT, "reviewer")
    assert len(writer_entries) == 1
    assert len(reviewer_entries) == 1
    assert writer_entries[0].scope is MemoryScope.AGENT
    assert writer_entries[0].value["content_preview"] == "done-writer"
    assert writer_entries[0].metadata["source"] == "observability_agent_turn_projection"


async def _run_team_topology(
    topology: str,
) -> tuple[_SeedRecordingHarness, InvocationResult]:
    backend = _SeedRecordingHarness()

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
        del kind, session_id, node_key, execution_id, trace_id, detail, level, actor

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(
        id=f"{topology}-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": topology},
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )
    result = await executor.invoke_team(request)
    return backend, result


@pytest.mark.asyncio
async def test_pipeline_member_reads_predecessor_output() -> None:
    # The watershed: protocol.topology=PIPELINE makes member N read member N-1's
    # output (speaker-attributed via Message.name), driven solely by the kernel
    # scheduler — not a flat fan-out.
    backend, _ = await _run_team_topology("pipeline")
    assert backend.seed_by_role["writer"] == ()  # first member has no predecessor
    reviewer_seed = backend.seed_by_role["reviewer"]
    assert len(reviewer_seed) == 1
    assert reviewer_seed[0].name == "writer"
    assert reviewer_seed[0].content == "done-writer"
    assert reviewer_seed[0].role is MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_broadcast_members_stay_independent() -> None:
    # Same team, BROADCAST topology: members receive NO peer context — the
    # behavioural difference proves topology genuinely drives execution.
    backend, _ = await _run_team_topology("broadcast")
    assert backend.seed_by_role["writer"] == ()
    assert backend.seed_by_role["reviewer"] == ()


@pytest.mark.asyncio
async def test_coordinator_routes_workers_and_summary_aggregation_reduces() -> None:
    # COORDINATOR topology: workers act independently first, the coordinator acts
    # last reading all peer outputs; COORDINATOR_SUMMARY reduces the team result to
    # the coordinator's output (distinct from COLLECT's all-member list).
    backend = _SeedRecordingHarness()

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
        del kind, session_id, node_key, execution_id, trace_id, detail, level, actor

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(
        id="coordinator-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
            AgentSpec(id="lead", role="lead"),
        ),
        protocol={
            "topology": "coordinator",
            "aggregation": "coordinator_summary",
            "coordinator_id": "lead",
        },
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    result = await executor.invoke_team(request)

    # Workers act independently; the coordinator reads BOTH peer outputs.
    assert backend.seed_by_role["writer"] == ()
    assert backend.seed_by_role["reviewer"] == ()
    lead_seed = backend.seed_by_role["lead"]
    assert {m.name for m in lead_seed} == {"writer", "reviewer"}
    assert {m.content for m in lead_seed} == {"done-writer", "done-reviewer"}

    # COORDINATOR_SUMMARY reduces the team result to the coordinator's output.
    aggregation = result.raw["mas_team"]["aggregation"]
    assert aggregation["strategy"] == "coordinator_summary"
    assert aggregation["coordinator_id"] == "lead"
    assert aggregation["summary"] == "done-lead"


@pytest.mark.asyncio
async def test_broadcast_aggregation_is_collect() -> None:
    # COLLECT keeps every member's result; aggregation marks the strategy as collect
    # (distinct from the coordinator-summary reduction).
    _, result = await _run_team_topology("broadcast")
    aggregation = result.raw["mas_team"]["aggregation"]
    assert aggregation["strategy"] == "collect"
    assert "summary" not in aggregation
    assert len(result.raw["mas_team"]["results"]) == 2


async def _run_team(
    *,
    members: tuple[AgentSpec, ...],
    protocol: dict[str, Any],
) -> tuple[_SeedRecordingHarness, InvocationResult]:
    backend = _SeedRecordingHarness()

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
        del kind, session_id, node_key, execution_id, trace_id, detail, level, actor

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(id="mode-team", members=members, protocol=protocol)
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )
    result = await executor.invoke_team(request)
    return backend, result


@pytest.mark.asyncio
async def test_debate_runs_rounds_with_every_member_reading_transcript() -> None:
    # DEBATE: each of 2 members acts each round (default 2 rounds = 4 turns) and
    # reads the accumulated transcript of prior turns.
    backend, result = await _run_team(
        members=(
            AgentSpec(id="pro", role="pro"),
            AgentSpec(id="con", role="con"),
        ),
        protocol={"mode": "debate", "topology": "broadcast"},
    )
    results = result.raw["mas_team"]["results"]
    # 2 members x 2 default rounds = 4 turns.
    assert [r["agent_id"] for r in results] == ["pro", "con", "pro", "con"]
    # The last member to act read the full prior transcript (3 prior messages).
    last_con_seed = backend.seed_by_role["con"]
    assert len(last_con_seed) == 3
    assert [m.name for m in last_con_seed] == ["pro", "con", "pro"]


@pytest.mark.asyncio
async def test_debate_honors_explicit_rounds_metadata() -> None:
    _, result = await _run_team(
        members=(
            AgentSpec(id="pro", role="pro"),
            AgentSpec(id="con", role="con"),
        ),
        protocol={"mode": "debate", "topology": "broadcast", "metadata": {"rounds": 1}},
    )
    results = result.raw["mas_team"]["results"]
    # 2 members x 1 round = 2 turns (NOT the multi-round default).
    assert [r["agent_id"] for r in results] == ["pro", "con"]


@pytest.mark.asyncio
async def test_vote_tallies_and_majority_picks_plurality_and_emits_agent_vote() -> None:
    # 3 voters; two share a role -> same content -> same vote -> plurality.
    events = await _collect_team_events(
        "broadcast",
        members=(
            AgentSpec(id="v1", role="yes"),
            AgentSpec(id="v2", role="yes"),
            AgentSpec(id="v3", role="no"),
        ),
        protocol={
            "mode": "vote",
            "topology": "broadcast",
            "aggregation": "vote",
            "arbitration": "majority",
        },
    )
    votes = [e for e in events if e.kind is ObservationEventKind.AGENT_VOTE]
    # AGENT_VOTE emitted once per voter, attributed to the voter.
    assert [e.actor for e in votes] == ["v1", "v2", "v3"]
    assert votes[0].detail["vote"] == "done-yes"
    # S3b: canonical vote rides alongside raw without removing the raw key.
    assert votes[0].detail["vote_canonical"] == "done-yes"

    backend, result = await _run_team(
        members=(
            AgentSpec(id="v1", role="yes"),
            AgentSpec(id="v2", role="yes"),
            AgentSpec(id="v3", role="no"),
        ),
        protocol={
            "mode": "vote",
            "topology": "broadcast",
            "aggregation": "vote",
            "arbitration": "majority",
        },
    )
    del backend
    aggregation = result.raw["mas_team"]["aggregation"]
    assert aggregation["strategy"] == "vote"
    # Tally keys are CANONICAL vote values (not raw content.strip()).
    assert aggregation["tally"] == {"done-yes": 2, "done-no": 1}
    assert aggregation["winner"] == "done-yes"
    assert aggregation["winner_count"] == 2
    # S3b typed-derived keys: no tie, no invalid votes, all per-voter records.
    assert aggregation["tie"] is False
    assert aggregation["tied_options"] == []
    assert aggregation["invalid"] == []
    assert [v["agent_id"] for v in aggregation["votes"]] == ["v1", "v2", "v3"]
    assert all(v["valid"] for v in aggregation["votes"])


@pytest.mark.asyncio
async def test_vote_majority_tie_breaks_deterministically() -> None:
    # A 1-1 tie breaks by sorted vote value -> "done-aaa" < "done-bbb".
    _, result = await _run_team(
        members=(
            AgentSpec(id="v1", role="bbb"),
            AgentSpec(id="v2", role="aaa"),
        ),
        protocol={
            "mode": "vote",
            "topology": "broadcast",
            "aggregation": "vote",
            "arbitration": "majority",
        },
    )
    aggregation = result.raw["mas_team"]["aggregation"]
    assert aggregation["tally"] == {"done-bbb": 1, "done-aaa": 1}
    assert aggregation["winner"] == "done-aaa"
    # S3b: the tie is represented EXPLICITLY, not hidden behind the winner.
    assert aggregation["tie"] is True
    assert aggregation["tied_options"] == ["done-aaa", "done-bbb"]
    assert aggregation["winner_count"] == 1


@pytest.mark.asyncio
async def test_delegation_runs_delegator_first_then_delegates() -> None:
    # DELEGATION: the delegator (coordinator_id) acts first on the task, then each
    # delegate acts reading the delegator's instruction.
    backend, result = await _run_team(
        members=(
            AgentSpec(id="delegate", role="delegate"),
            AgentSpec(id="boss", role="boss"),
        ),
        protocol={
            "mode": "delegation",
            "topology": "coordinator",
            "coordinator_id": "boss",
        },
    )
    results = result.raw["mas_team"]["results"]
    assert [r["agent_id"] for r in results] == ["boss", "delegate"]
    # The delegator acts on the task with no peer context.
    assert backend.seed_by_role["boss"] == ()
    # The delegate reads the delegator's instruction.
    delegate_seed = backend.seed_by_role["delegate"]
    assert len(delegate_seed) == 1
    assert delegate_seed[0].name == "boss"
    assert delegate_seed[0].content == "done-boss"


def test_orchestrator_no_longer_owns_collaboration_method_bodies() -> None:
    assert not hasattr(OrchestratorAgent, "_invoke_team")
    assert not hasattr(OrchestratorAgent, "_run_team_member")
    assert not hasattr(OrchestratorAgent, "_drain_subagent_tool_calls")
    assert hasattr(CollaborationExecutor, "invoke_team")
    # S98: the legacy subagent_group collaboration path is retired.
    assert not hasattr(CollaborationExecutor, "invoke_legacy_subagent_group")
    assert not hasattr(CollaborationExecutor, "_run_group_subagent")


def test_native_team_execution_lives_on_team_boundary() -> None:
    assert hasattr(NativeTeamExecutor, "invoke")
    assert (
        collaboration_module.CollaborationExecutionRequest
        is collaboration_contracts.CollaborationExecutionRequest
    )
    assert "_run_team_member" not in collaboration_module.__dict__
    assert "_team_spec_from_config" not in collaboration_module.__dict__
    assert "_team_detail" not in collaboration_module.__dict__
    assert "_team_origin" not in collaboration_module.__dict__
    assert "_tools_for_agent" not in collaboration_module.__dict__


@pytest.mark.asyncio
async def test_team_execution_filters_tools_per_member() -> None:
    backend = _RecordingHarness()
    events: list[tuple[ObservationEventKind, dict[str, Any]]] = []

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
        del session_id, node_key, execution_id, trace_id, level, actor
        events.append((kind, detail or {}))

    async def execute_tool_round(
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        tool_calls: tuple[ToolCall, ...],
        round_no: int,
        tools: list[ToolSpec],
        agent_role: str,
        tool_context: ToolContext,
        origin: dict[str, Any] | None,
        successful_results_by_fingerprint: dict[str, tuple[str, str]],
    ) -> list[ToolResultBlock]:
        del (
            session_id,
            node_key,
            execution_id,
            trace_id,
            tool_calls,
            round_no,
            tools,
            agent_role,
            tool_context,
            origin,
            successful_results_by_fingerprint,
        )
        raise AssertionError("tool-drain callback should not run without tool calls")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(
        id="tool-team",
        tools=("search.web", "image.generate"),
        members=(
            AgentSpec(id="researcher", role="researcher", tools=("search.web",)),
            AgentSpec(id="designer", role="designer", tools=("image.generate",)),
        ),
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=[
            ToolSpec(name="search.web", description="search"),
            ToolSpec(name="image.generate", description="image"),
        ],
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    result = await executor.invoke_team(request)

    assert backend.spawned_tools == {
        "researcher": ("search.web",),
        "designer": ("image.generate",),
    }
    assert result.raw["mas_team"]["team_id"] == "tool-team"
    assert [
        detail["execution_mode"]
        for kind, detail in events
        if kind is ObservationEventKind.MODEL_INVOKE_STARTED
    ] == ["team"]
