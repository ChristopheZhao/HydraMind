"""S4c — duplicate queue delivery idempotency (PLAN-20260618-001 §6).

Proves the §6 Required Regression Check: a DUPLICATE queue delivery (the same
session_id / unit of work delivered twice — visibility-timeout re-delivery or
crash re-delivery) is idempotent for:

  (a) tool side effects,
  (b) memory writes,
  (c) repair counter increments,
  (d) interaction turns.

Layering of idempotency (documented here as the test contract):

* Node-level work is idempotent via ``NodeStatus``/lease: a COMPLETED node is
  never re-selected by ``WorkflowGraph.ready_nodes`` (QUEUED-only), and a live
  lease blocks a concurrent delivery from double-running a node. So a second
  delivery of an already-finished session is a no-op.
* The finer concerns that survive a legitimate node retry (re-delivery after a
  crash/lease-expiry that requeues the node) are deduped by the durable,
  control-owned idempotency ledger on ``RuntimeSession``
  (``processed_idempotency_keys``) plus the ``MemoryWriteAuthority``
  idempotency key.

All tests use ``MockProvider`` for determinism (replay/plumbing evidence — not
agent acceptance).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import hydramind.control.session_service as session_service_module
from hydramind.control import (
    ControlPlane,
    InMemorySessionStore,
    InteractionLogEventKind,
    InteractionLogRecord,
    NodeStatus,
    RuntimeDecisionKind,
    SessionService,
    SessionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.harness import InvocationResult
from hydramind.harness.base import StopReason, ToolCall
from hydramind.memory import (
    AgentTurnMemoryObserver,
    EpisodeProjectorObserver,
    InMemoryMemoryStore,
    MemoryScope,
    MemoryWriteAuthority,
)
from hydramind.observability import (
    Emitter,
    ListObserver,
    ObservationEvent,
    ObservationEventKind,
)
from hydramind.orchestration import OrchestratorAgent
from hydramind.queue import InMemoryQueueAdapter
from hydramind.runtime_worker import QueueExecutionHost
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import (
    ToolContext,
    ToolExecutionResult,
    ToolPolicy,
    ToolRegistry,
    ToolRiskClass,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _single_node_workflow() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="idempotency-demo",
        nodes=(WorkflowNodeSpec(key="work", role="writer"),),
    )


def _write_tool_registry(tmp_path, counter: list[int]) -> ToolRegistry:
    async def write_text(args, context):
        del context
        counter[0] += 1
        return ToolExecutionResult.ok(
            {"path": args["path"], "call_count": counter[0]}
        )

    tools = ToolRegistry(
        default_context=ToolContext(artifact_root=tmp_path, dry_run=False)
    )
    tools.register_function(
        name="artifact.write_text",
        description="write text",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=write_text,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    return tools


def _write_turn() -> ScriptedTurn:
    return ScriptedTurn(
        content="",
        tool_calls=(
            ToolCall(
                id="tool-1",
                name="artifact.write_text",
                arguments={"path": "out.txt", "content": "ok"},
            ),
        ),
        stop_reason=StopReason.TOOL_USE,
    )


# --------------------------------------------------------------------------- #
# (whole-session) duplicate delivery is a no-op once terminal
# --------------------------------------------------------------------------- #


async def test_duplicate_session_delivery_does_not_rerun_completed_node(
    tmp_path,
) -> None:
    """Delivering the SAME session_id twice does not re-run a COMPLETED node.

    Node-level idempotency via NodeStatus: the second delivery finds the session
    terminal / the node COMPLETED and runs no tool a second time.
    """

    counter = [0]
    tools = _write_tool_registry(tmp_path, counter)
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(
            scripted=[_write_turn(), ScriptedTurn(content='{"done": true}')]
        ),
        control=ControlPlane(service),
        workflow=_single_node_workflow(),
        tool_provider=tools,
        tool_runner=tools,
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    # Enqueue the SAME session id twice (duplicate delivery).
    await queue.enqueue(session.id)
    await queue.enqueue(session.id)

    host = QueueExecutionHost(queue=queue, orchestrator=agent, worker_id="w1")
    first = await host.run_once()
    second = await host.run_once()

    assert first.status == "completed"
    assert second.status == "completed"
    # Tool side effect ran exactly once across both deliveries.
    assert counter[0] == 1
    refreshed = await service.get_session(session.id)
    assert refreshed.status is SessionStatus.COMPLETED
    assert refreshed.nodes["work"].status is NodeStatus.COMPLETED
    # The node ran exactly one attempt (the second delivery added none).
    assert len(refreshed.nodes["work"].attempts) == 1


# --------------------------------------------------------------------------- #
# (a) tool side effects — durable cross-delivery dedupe on node retry
# --------------------------------------------------------------------------- #


class _SimulatedWorkerCrash(BaseException):
    """A process-death-like signal that bypasses ``run_session``'s ``except
    Exception`` handler, leaving the attempt RUNNING with a live lease (as a real
    worker crash would) so lease-expiry recovery can re-deliver it."""


class _CrashAfterFirstToolProvider(MockProvider):
    """Runs the tool on attempt 1, then crashes before the node completes.

    Attempt 1: returns the tool-call turn (tool side effect commits), then the
    next model call RAISES — simulating a worker crash AFTER a side effect but
    BEFORE the node was marked COMPLETED. Attempt 2 (the re-delivery) returns the
    SAME tool-call turn again, then a normal completion turn.
    """

    def __init__(self) -> None:
        super().__init__()
        self._calls = 0

    async def complete(self, *args, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return InvocationResult(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            )
        if self._calls == 2:
            raise _SimulatedWorkerCrash("worker crash after tool side effect")
        if self._calls == 3:
            return InvocationResult(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-2",
                        name="artifact.write_text",
                        arguments={"path": "out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            )
        return InvocationResult(content='{"done": true}', stop_reason=StopReason.END_TURN)


async def test_tool_side_effect_not_duplicated_on_crash_redelivery(tmp_path) -> None:
    """A re-delivery after a crash mid-node reuses the durable tool effect result.

    Attempt 1 commits the tool side effect then crashes (lease expires). The
    re-delivery recovers the expired execution, re-runs the node, and the
    identical tool call (same effect_fingerprint) is served from the durable,
    control-owned ledger instead of re-executing the side effect.
    """

    counter = [0]
    tools = _write_tool_registry(tmp_path, counter)
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=_CrashAfterFirstToolProvider(),
        control=control,
        workflow=_single_node_workflow(),
        tool_provider=tools,
        tool_runner=tools,
    )
    session = await agent.start_session()

    # Attempt 1: runs the tool (side effect commits), then crashes hard
    # (BaseException bypasses run_session's except Exception, mimicking a worker
    # process death that leaves the attempt RUNNING with a live lease).
    with pytest.raises(_SimulatedWorkerCrash):
        await agent.run_session(session.id, execution_owner="worker-dead")
    assert counter[0] == 1

    # The crashed attempt is still RUNNING with a lease; expire it so recovery
    # requeues the node on the next delivery (re-delivery path).
    crashed = await service.get_session(session.id)
    running = crashed.nodes["work"].attempts[-1]
    await service.heartbeat_execution_lease(
        session.id, running.id, lease_token=running.lease_token, ttl_seconds=1
    )
    later = datetime.now(UTC) + timedelta(seconds=5)
    original_now = session_service_module._now
    session_service_module._now = lambda: later
    try:
        second = await agent.run_session(
            session.id, execution_owner="worker-redelivery"
        )
    finally:
        session_service_module._now = original_now

    assert second.kind is RuntimeDecisionKind.COMPLETE
    # The tool side effect was NOT executed a second time across the re-delivery.
    assert counter[0] == 1
    refreshed = await service.get_session(session.id)
    effect_keys = [
        k
        for k, rec in refreshed.processed_idempotency_keys.items()
        if rec.kind == "tool_effect"
    ]
    assert len(effect_keys) == 1
    # The recovered re-run's tool ledger entry reused the durable prior result.
    final_attempt = refreshed.nodes["work"].attempts[-1]
    assert final_attempt.tool_executions
    assert final_attempt.tool_executions[-1].metadata.get("reused_result") is True
    assert (
        final_attempt.tool_executions[-1].metadata.get("source_tool_call_id")
        == "tool-1"
    )


# --------------------------------------------------------------------------- #
# (b) memory writes — replayed prompt-affecting write is not double-appended
# --------------------------------------------------------------------------- #


def _agent_message_event() -> ObservationEvent:
    return ObservationEvent(
        kind=ObservationEventKind.AGENT_MESSAGE_SENT,
        session_id="sess-dup",
        node_key="collaborate",
        trace_id="trace-1",
        execution_id="exec-1",
        actor="writer",
        detail={
            "interaction_id": "interaction-exec-1",
            "turn_index": 0,
            "role": "writer",
            "content_preview": "draft-text",
        },
    )


async def test_agent_turn_memory_write_idempotent_under_replay() -> None:
    """Replaying the same AGENT_MESSAGE_SENT does not double-append the record."""

    store = InMemoryMemoryStore()
    authority = MemoryWriteAuthority(store)
    observer = AgentTurnMemoryObserver(authority)

    await observer.on_event(_agent_message_event())
    # Duplicate delivery replays the identical event.
    await observer.on_event(_agent_message_event())

    entries = await store.scan(MemoryScope.AGENT, "writer")
    assert len(entries) == 1
    assert entries[0].value["content_preview"] == "draft-text"


async def test_episode_memory_write_idempotent_under_replay() -> None:
    """Replaying the terminal event does not double-append the episode summary."""

    store = InMemoryMemoryStore()
    authority = MemoryWriteAuthority(store)

    def _new_observer() -> EpisodeProjectorObserver:
        return EpisodeProjectorObserver(authority, workflow_name="wf")

    started = ObservationEvent(
        kind=ObservationEventKind.NODE_EXECUTION_STARTED,
        session_id="sess-dup",
        node_key="work",
        trace_id="trace-1",
        execution_id="exec-1",
    )
    terminal = ObservationEvent(
        kind=ObservationEventKind.SESSION_COMPLETED,
        session_id="sess-dup",
        node_key="work",
        trace_id="trace-1",
        execution_id="exec-1",
    )

    obs1 = _new_observer()
    await obs1.on_event(started)
    await obs1.on_event(terminal)

    # A second worker delivery rebuilds + replays the same terminal projection.
    obs2 = _new_observer()
    await obs2.on_event(started)
    await obs2.on_event(terminal)

    entries = await store.scan(
        MemoryScope.WORKFLOW, "wf", key_prefix="episode.sess-dup"
    )
    assert len(entries) == 1


# --------------------------------------------------------------------------- #
# (c) repair counters — durable cap holds under duplicate delivery
# --------------------------------------------------------------------------- #


async def test_repair_counter_not_double_counted_under_duplicate_reserve() -> None:
    """The durable repair budget caps total reservations across re-deliveries."""

    service = SessionService(InMemorySessionStore())
    blueprint = _single_node_workflow()
    session = await service.create_session(blueprint)

    # First reservation succeeds.
    assert await service.reserve_auto_repair_attempt(session.id, max_attempts=1)
    # A duplicate delivery that replays the repair cannot exceed the cap.
    assert not await service.reserve_auto_repair_attempt(session.id, max_attempts=1)

    assert await service.auto_repair_attempts_used(session.id) == 1


# --------------------------------------------------------------------------- #
# (d) interaction turns — replayed interaction event is not double-appended
# --------------------------------------------------------------------------- #


async def test_interaction_turn_idempotent_under_duplicate_delivery() -> None:
    """Replaying the same interaction event does not double-append the turn."""

    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_single_node_workflow())

    def _record(record_id: str) -> InteractionLogRecord:
        # Distinct random ``id`` per call, but identical LOGICAL identity, to
        # prove dedupe keys off logical identity (not the random id).
        return InteractionLogRecord(
            id=record_id,
            session_id=session.id,
            node_key="work",
            execution_id="exec-1",
            interaction_id="interaction-1",
            team_id="team-1",
            event_kind=InteractionLogEventKind.TURN_COMPLETED,
            actor="writer",
            turn_index=0,
            content_preview="turn output",
        )

    await service.record_interaction_event(session.id, _record("interaction-log-a"))
    # Duplicate delivery replays the same logical turn (different random id).
    await service.record_interaction_event(session.id, _record("interaction-log-b"))

    refreshed = await service.get_session(session.id)
    entries = refreshed.metadata["interaction_log"]["entries"]
    assert len(entries) == 1
    # A genuinely different turn IS appended (dedupe is not over-broad).
    other = InteractionLogRecord(
        session_id=session.id,
        node_key="work",
        execution_id="exec-1",
        interaction_id="interaction-1",
        team_id="team-1",
        event_kind=InteractionLogEventKind.TURN_COMPLETED,
        actor="reviewer",
        turn_index=1,
        content_preview="next turn",
    )
    await service.record_interaction_event(session.id, other)
    refreshed = await service.get_session(session.id)
    assert len(refreshed.metadata["interaction_log"]["entries"]) == 2


async def test_worker_duplicate_delivery_with_listobserver_records_once(
    tmp_path,
) -> None:
    """Two worker deliveries of one session produce a single completed-node run.

    Integration-level check combining the worker host, control ledger, and the
    observability emitter: the second delivery is a no-op (session terminal), so
    tool side effects, node attempts, and lease grants are not duplicated.
    """

    counter = [0]
    tools = _write_tool_registry(tmp_path, counter)
    observer = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([observer]))
    agent = OrchestratorAgent(
        provider=MockProvider(
            scripted=[_write_turn(), ScriptedTurn(content='{"done": true}')]
        ),
        control=ControlPlane(service),
        workflow=_single_node_workflow(),
        tool_provider=tools,
        tool_runner=tools,
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)
    await queue.enqueue(session.id)

    host = QueueExecutionHost(
        queue=queue, orchestrator=agent, worker_id="w1", lease_ttl_seconds=60
    )
    await host.run_once()
    await host.run_once()

    assert counter[0] == 1
    lease_grants = [
        e
        for e in observer.events
        if e.kind is ObservationEventKind.EXECUTION_LEASE_GRANTED
    ]
    assert len(lease_grants) == 1
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
