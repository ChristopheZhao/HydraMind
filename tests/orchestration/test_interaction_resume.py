"""S5b — resume a native MAS team from durable interaction state.

Done-signal (95 §F5 Phase 4): a native MAS team interrupted after some turns
complete RESUMES on a fresh attempt without replaying the whole node — the
already-completed member is NOT re-run (its tool side effect / authoritative
message is not duplicated), its authoritative message is reloaded as context for
the remaining members, and the interaction completes from durable state. The
durable interaction is keyed by (session, node) so the new attempt (new
execution_id) finds and continues the prior one. ``recovered_from_turn_index``
is set.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.control import (
    ControlPlane,
    SessionService,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    durable_interaction_id,
)
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
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.execution_harness import ProviderExecutionHarnessRuntime
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.tools import ToolContext

NODE_KEY = "collaborate"


class _Crash(BaseException):
    """Hard crash (not an ``Exception``) so the executor's fail handler does NOT
    run — modelling a worker killed mid-turn / turn-lease expiry, leaving the
    durable interaction RUNNING with the prior turns recorded."""


class _Harness(ModelProvider):
    name = "resume-harness"

    def __init__(
        self,
        *,
        content_by_id: dict[str, str],
        side_effects: dict[str, int],
        crash_ids: set[str],
        seen_seed_by_id: dict[str, tuple[str, ...]],
    ) -> None:
        self._content_by_id = content_by_id
        self._side_effects = side_effects
        self._crash_ids = crash_ids
        self._seen_seed_by_id = seen_seed_by_id

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
        # role == agent id here (members declare role == id below).
        agent_id = role or "unknown"
        self._seen_seed_by_id[agent_id] = tuple(m.content for m in messages[:-1])
        if agent_id in self._crash_ids:
            raise _Crash("worker killed mid-turn")
        self._side_effects[agent_id] = self._side_effects.get(agent_id, 0) + 1
        return InvocationResult(
            content=self._content_by_id.get(agent_id, f"done-{agent_id}"),
            stop_reason=StopReason.END_TURN,
            model_id=f"model-{agent_id}",
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
        name="resume-wf",
        nodes=(WorkflowNodeSpec(key=NODE_KEY, role="coordinator"),),
    )


def _team() -> TeamSpec:
    # PIPELINE so member N reads members 1..N-1 (proves context reload on resume).
    return TeamSpec(
        id="pipeline-3",
        members=(
            AgentSpec(id="m1", role="m1"),
            AgentSpec(id="m2", role="m2"),
            AgentSpec(id="m3", role="m3"),
        ),
        protocol={"topology": "pipeline"},
    )


async def _run(
    *,
    control: ControlPlane,
    session_id: str,
    provider: ModelProvider,
    execution_id: str,
) -> InvocationResult:
    async def emit_trace(*_: Any, **__: Any) -> None:
        return None

    async def execute_tool_round(**_: Any) -> list[ToolResultBlock]:
        raise AssertionError("no tool calls expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=2,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(
            node_key=node_key, role=role
        ),
        record_interaction_event=control.record_interaction_event,
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
        node_config={"mas_team": _team().model_dump(mode="json")},
    )
    return await executor.invoke_team(request)


@pytest.mark.asyncio
async def test_team_resumes_from_durable_state_without_replaying_node() -> None:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    session_id = session.id
    interaction_id = durable_interaction_id(session_id, NODE_KEY)

    side_effects: dict[str, int] = {}
    seen_seed: dict[str, tuple[str, ...]] = {}

    # --- Attempt 1: m1 completes, then m2 crashes the worker (hard) ----------
    crash_harness = _Harness(
        content_by_id={"m1": "m1-output", "m2": "m2-output", "m3": "m3-output"},
        side_effects=side_effects,
        crash_ids={"m2"},
        seen_seed_by_id=seen_seed,
    )
    with pytest.raises(_Crash):
        await _run(
            control=control,
            session_id=session_id,
            provider=crash_harness,
            execution_id="exec-1",
        )

    # m1 ran exactly once and its turn/message are durably recorded; the
    # interaction is still RUNNING (the hard crash skipped the fail handler).
    assert side_effects == {"m1": 1}
    mid = await control.get_durable_interaction(session_id, interaction_id)
    assert mid is not None
    assert mid.status is InteractionStatus.RUNNING
    assert [t.turn_index for t in mid.turns] == [0]
    assert mid.turns[0].agent_id == "m1"
    assert mid.turns[0].status is TurnStatus.COMPLETED
    assert mid.messages[0].content == "m1-output"

    # --- Attempt 2 (fresh execution_id): RESUME ------------------------------
    seen_seed.clear()
    resume_harness = _Harness(
        content_by_id={"m1": "m1-output", "m2": "m2-output", "m3": "m3-output"},
        side_effects=side_effects,
        crash_ids=set(),  # no crash this time
        seen_seed_by_id=seen_seed,
    )
    await _run(
        control=control,
        session_id=session_id,
        provider=resume_harness,
        execution_id="exec-2",  # new attempt, new execution id
    )

    # m1 was NOT re-run on resume (side effect still 1); m2 and m3 ran once.
    assert side_effects == {"m1": 1, "m2": 1, "m3": 1}

    # m1 was not spawned at all on the resume attempt (no seed recorded for it);
    # m2 saw m1's authoritative message reloaded as PIPELINE context.
    assert "m1" not in seen_seed
    assert "m1-output" in seen_seed["m2"]
    # m3 saw both prior messages (m1 reloaded + m2 fresh).
    assert "m1-output" in seen_seed["m3"]
    assert "m2-output" in seen_seed["m3"]

    final = await control.get_durable_interaction(session_id, interaction_id)
    assert final is not None
    # Completed from durable state, all three turns present exactly once.
    assert final.status is InteractionStatus.COMPLETED
    assert [t.turn_index for t in final.turns] == [0, 1, 2]
    assert [t.agent_id for t in final.turns] == ["m1", "m2", "m3"]
    assert all(t.status is TurnStatus.COMPLETED for t in final.turns)
    # m1's authoritative message is not duplicated.
    m1_messages = [m for m in final.messages if m.sender == "m1"]
    assert len(m1_messages) == 1
    assert m1_messages[0].content == "m1-output"
    # The recovery marker is set: resumed after 1 completed turn.
    assert final.recovered_from_turn_index == 1


@pytest.mark.asyncio
async def test_no_resumable_interaction_starts_fresh() -> None:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    session_id = session.id
    interaction_id = durable_interaction_id(session_id, NODE_KEY)

    side_effects: dict[str, int] = {}
    harness = _Harness(
        content_by_id={},
        side_effects=side_effects,
        crash_ids=set(),
        seen_seed_by_id={},
    )
    await _run(
        control=control,
        session_id=session_id,
        provider=harness,
        execution_id="exec-1",
    )
    interaction = await control.get_durable_interaction(session_id, interaction_id)
    assert interaction is not None
    assert interaction.status is InteractionStatus.COMPLETED
    # Fresh start: no recovery marker, all members ran once.
    assert interaction.recovered_from_turn_index is None
    assert side_effects == {"m1": 1, "m2": 1, "m3": 1}
