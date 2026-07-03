"""QueueExecutionHost worker-loop tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import hydramind.control.session_service as session_service_module
import hydramind.runtime as runtime_module
from hydramind.control import (
    AgentReport,
    AttemptStatus,
    ControlPlane,
    Gate,
    GateOutcome,
    GoalArtifactQualityContract,
    InMemorySessionStore,
    NodeState,
    NodeStatus,
    RuntimeSession,
    SessionService,
    SessionStatus,
    SqliteSessionStore,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.harness import StopReason, ToolCall
from hydramind.memory import MemoryScope, SqliteMemoryStore
from hydramind.observability import Emitter, ListObserver, ObservationEventKind
from hydramind.orchestration import GoalSpec, OrchestratorAgent
from hydramind.queue import (
    CeleryQueueAdapter,
    InMemoryQueueAdapter,
    QueueCapabilityError,
    RedisStreamQueueAdapter,
)
from hydramind.runtime import (
    create_queue_adapter,
    create_queued_goal_session,
    queue_dead_letters,
    queue_health,
    replay_queue_dead_letters,
    run_queued_goal_session_once,
    worker_readiness,
)
from hydramind.runtime_worker import (
    QueueExecutionHost,
    WorkerDeliveryAction,
    WorkerLoopStopReason,
    worker_loop_exit_contract,
)
from hydramind.testing import MockProvider, ScriptedTurn
from tests.queue.fake_redis_streams import FakeRedisStreams


def _plan_turn(
    *, key: str = "work", role: str = "writer", tools: tuple[str, ...] = ()
) -> ScriptedTurn:
    """A scripted planner turn returning deterministic plan JSON.

    Drives ``ModelGoalPlanner`` offline (no rule planner): the agent planner
    pops this turn, parses it into a single-task ``ExecutionPlan``, and the
    goal's expected-artifacts/quality-contract projection is applied by
    ``execution_plan_from_payload`` so the persisted plan carries them.
    """
    task: dict[str, Any] = {"key": key, "role": role}
    if tools:
        task["tools"] = list(tools)
    return ScriptedTurn(content=json.dumps({"tasks": [task]}))


def _noop_revise_turn() -> ScriptedTurn:
    """A scripted planner revise turn that makes no plan changes."""
    return ScriptedTurn(content=json.dumps({"rationale": "no actionable change"}))


class SlowMockProvider(MockProvider):
    def __init__(self, *, delay_seconds: float, scripted: list[ScriptedTurn]) -> None:
        super().__init__(scripted=scripted)
        self._delay_seconds = delay_seconds

    async def complete(self, *args, **kwargs):
        await asyncio.sleep(self._delay_seconds)
        return await super().complete(*args, **kwargs)


def _workflow() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="worker-demo",
        nodes=(WorkflowNodeSpec(key="plan", role="planner"),),
    )


def test_create_queue_adapter_builds_memory_queue() -> None:
    queue = create_queue_adapter("memory")

    assert isinstance(queue, InMemoryQueueAdapter)


def test_create_queue_adapter_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="redis queue adapter requires"):
        create_queue_adapter("redis")
    with pytest.raises(ValueError, match="unknown queue adapter kind"):
        create_queue_adapter("unknown")


def test_worker_readiness_reports_distributed_ready_redis_sqlite(
    tmp_path: Path,
) -> None:
    queue = RedisStreamQueueAdapter(redis_client=FakeRedisStreams())

    snapshot = worker_readiness(
        queue_adapter=queue,
        worker_id="ops-worker",
        session_store_kind="sqlite",
        store_path=tmp_path / "sessions.sqlite",
    )

    assert snapshot.ready is True
    assert snapshot.distributed_worker_ready is True
    assert snapshot.local_worker_ready is True
    assert snapshot.worker_id == "ops-worker"
    assert snapshot.queue_name == "redis-stream"
    assert snapshot.queue_pollable is True
    assert snapshot.queue_distribution == "broker"
    assert snapshot.session_store_kind == "sqlite"
    assert snapshot.session_store_path == str(tmp_path / "sessions.sqlite")
    assert snapshot.session_store_persistent is True
    assert snapshot.session_store_cas_capable is True
    assert "queue_supports_pollable_delivery" in snapshot.reasons
    assert "queue_is_broker_backed" in snapshot.reasons
    assert "sqlite_session_store_is_persistent_cas_capable" in snapshot.reasons
    assert "distributed_worker_preflight_ready" in snapshot.reasons


def test_worker_readiness_reports_local_only_in_memory_store() -> None:
    snapshot = worker_readiness(
        queue_adapter=InMemoryQueueAdapter(),
        session_store_kind="memory",
    )

    assert snapshot.ready is False
    assert snapshot.distributed_worker_ready is False
    assert snapshot.local_worker_ready is True
    assert snapshot.queue_name == "in-memory"
    assert snapshot.queue_pollable is True
    assert snapshot.queue_distribution == "process_local"
    assert snapshot.session_store_kind == "memory"
    assert snapshot.session_store_persistent is False
    assert snapshot.session_store_cas_capable is False
    assert "queue_is_process_local" in snapshot.reasons
    assert "in_memory_session_store_is_process_local" in snapshot.reasons
    assert "distributed_worker_preflight_not_ready" in snapshot.reasons


async def test_worker_host_runs_one_queued_session_and_acks() -> None:
    observer = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([observer]))
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-1",
        lease_ttl_seconds=60,
    ).run_once()

    assert result.status == "completed"
    assert result.session_id == session.id
    assert result.worker_id == "worker-1"
    assert result.queue_name == "in-memory"
    assert result.message_handle is not None
    assert result.queue_attempt == 0
    assert result.delivery_action is WorkerDeliveryAction.ACK
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
    refreshed = await service.get_session(session.id)
    assert refreshed.status is SessionStatus.COMPLETED
    latest = refreshed.nodes["plan"].latest_attempt()
    assert latest is not None
    assert latest.lease_token is None
    lease_events = [
        event
        for event in observer.events
        if event.kind is ObservationEventKind.EXECUTION_LEASE_GRANTED
    ]
    assert len(lease_events) == 1
    assert lease_events[0].actor == "worker-1"
    assert lease_events[0].execution_id == latest.id


async def test_worker_host_runs_with_redis_stream_queue_adapter() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    session = await agent.start_session()
    queue = RedisStreamQueueAdapter(
        redis_client=FakeRedisStreams(),
        consumer_name="worker-redis",
    )
    await queue.enqueue(session.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-redis",
    ).run_once()

    assert result.status == "completed"
    assert result.session_id == session.id
    assert result.worker_id == "worker-redis"
    assert result.queue_name == "redis-stream"
    assert result.message_handle is not None
    assert result.delivery_action is WorkerDeliveryAction.ACK
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0


async def test_worker_host_loop_drains_until_idle_limit() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(
            scripted=[
                ScriptedTurn(content='{"session": 1}'),
                ScriptedTurn(content='{"session": 2}'),
            ]
        ),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    first = await agent.start_session()
    second = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(first.id)
    await queue.enqueue(second.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-loop",
    ).run_loop(timeout=0.001, max_idle_cycles=1)

    assert result.stop_reason is WorkerLoopStopReason.MAX_IDLE_CYCLES
    assert result.iterations == 3
    assert result.deliveries == 2
    assert result.acked == 2
    assert result.nack_retried == 0
    assert result.nack_dropped == 0
    assert result.errors == 0
    assert result.idle_cycles == 1
    assert result.consecutive_idle_cycles == 1
    assert result.last_result is not None
    assert result.last_result.status == "idle"
    assert result.worker_id == "worker-loop"
    assert result.queue_name == "in-memory"
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_ms is not None
    assert result.exit_code == 0
    assert result.restart_recommended is False
    assert worker_loop_exit_contract(result).exit_code == 0
    assert worker_loop_exit_contract(result).restart_recommended is False
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0

    refreshed_first = await service.get_session(first.id)
    refreshed_second = await service.get_session(second.id)
    assert refreshed_first.status is SessionStatus.COMPLETED
    assert refreshed_second.status is SessionStatus.COMPLETED


async def test_worker_host_loop_stops_when_requested_before_polling() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"unused": true}')]),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter()

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-stop",
    ).run_loop(stop_requested=lambda: True)

    assert result.stop_reason is WorkerLoopStopReason.STOP_REQUESTED
    assert result.iterations == 0
    assert result.deliveries == 0
    assert result.acked == 0
    assert result.nack_retried == 0
    assert result.nack_dropped == 0
    assert result.errors == 0
    assert result.exit_code == 0
    assert result.restart_recommended is False
    assert result.idle_cycles == 0
    assert result.consecutive_idle_cycles == 0
    assert result.last_result is None
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0


async def test_worker_host_loop_stops_when_requested_after_delivery() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)
    stop_state = {"requested": False}

    class StopAfterDelivery:
        async def run_session(
            self,
            session_id: str,
            *,
            execution_owner: str | None = None,
            lease_ttl_seconds: int = 300,
            lease_heartbeat_interval_seconds: float | None = None,
        ):
            decision = await agent.run_session(
                session_id,
                execution_owner=execution_owner,
                lease_ttl_seconds=lease_ttl_seconds,
                lease_heartbeat_interval_seconds=lease_heartbeat_interval_seconds,
            )
            stop_state["requested"] = True
            return decision

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=StopAfterDelivery(),
        worker_id="worker-stop",
    ).run_loop(stop_requested=lambda: stop_state["requested"])

    assert result.stop_reason is WorkerLoopStopReason.STOP_REQUESTED
    assert result.iterations == 1
    assert result.deliveries == 1
    assert result.acked == 1
    assert result.nack_retried == 0
    assert result.nack_dropped == 0
    assert result.errors == 0
    assert result.exit_code == 0
    assert result.restart_recommended is False
    assert result.idle_cycles == 0
    assert result.last_result is not None
    assert result.last_result.session_id == session.id
    assert result.last_result.delivery_action is WorkerDeliveryAction.ACK
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0


async def test_worker_host_loop_stops_at_max_iterations_with_work_remaining() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(
            scripted=[
                ScriptedTurn(content='{"session": 1}'),
                ScriptedTurn(content='{"session": 2}'),
            ]
        ),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    first = await agent.start_session()
    second = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(first.id)
    await queue.enqueue(second.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-loop",
    ).run_loop(max_iterations=1)

    assert result.stop_reason is WorkerLoopStopReason.MAX_ITERATIONS
    assert result.iterations == 1
    assert result.deliveries == 1
    assert result.acked == 1
    assert result.idle_cycles == 0
    assert result.last_result is not None
    assert result.last_result.session_id == first.id
    assert await queue.pending() == 1

    refreshed_first = await service.get_session(first.id)
    refreshed_second = await service.get_session(second.id)
    assert refreshed_first.status is SessionStatus.COMPLETED
    assert refreshed_second.status is SessionStatus.QUEUED


async def test_worker_host_loop_counts_delivery_errors_and_preserves_retry() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter()
    await queue.enqueue("missing-session")

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
    ).run_loop(max_iterations=1)

    assert result.stop_reason is WorkerLoopStopReason.MAX_ITERATIONS
    assert result.iterations == 1
    assert result.deliveries == 1
    assert result.acked == 0
    assert result.nack_retried == 1
    assert result.nack_dropped == 0
    assert result.errors == 1
    assert result.exit_code == 1
    assert result.restart_recommended is True
    assert worker_loop_exit_contract(result).exit_code == 1
    assert worker_loop_exit_contract(result).restart_recommended is True
    assert result.last_result is not None
    assert result.last_result.status == "error"
    assert result.last_result.delivery_action is WorkerDeliveryAction.NACK_RETRY
    assert await queue.pending() == 1
    retried = await queue.dequeue()
    assert retried is not None
    assert retried.attempt == 1


async def test_worker_host_loop_counts_dropped_delivery_errors() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter()
    await queue.enqueue("missing-session")

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
    ).run_loop(max_iterations=1, retry_on_error=False)

    assert result.stop_reason is WorkerLoopStopReason.MAX_ITERATIONS
    assert result.iterations == 1
    assert result.deliveries == 1
    assert result.acked == 0
    assert result.nack_retried == 0
    assert result.nack_dropped == 1
    assert result.errors == 1
    assert result.exit_code == 1
    assert result.restart_recommended is True
    assert result.last_result is not None
    assert result.last_result.status == "error"
    assert result.last_result.delivery_action is WorkerDeliveryAction.NACK_DROP
    assert result.last_result.retry_on_error is False
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0


async def test_worker_host_loop_rejects_non_positive_limits() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    host = QueueExecutionHost(queue=InMemoryQueueAdapter(), orchestrator=agent)

    with pytest.raises(ValueError, match="max_iterations"):
        await host.run_loop(max_iterations=0)
    with pytest.raises(ValueError, match="max_idle_cycles"):
        await host.run_loop(max_idle_cycles=0)


async def test_worker_host_heartbeats_during_long_node_execution() -> None:
    observer = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([observer]))
    agent = OrchestratorAgent(
        provider=SlowMockProvider(
            delay_seconds=0.05,
            scripted=[ScriptedTurn(content='{"ok": true}')],
        ),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-heartbeat",
        lease_ttl_seconds=1,
        lease_heartbeat_interval_seconds=0.01,
    ).run_once()

    assert result.status == "completed"
    refreshed = await service.get_session(session.id)
    latest = refreshed.nodes["plan"].latest_attempt()
    assert latest is not None
    assert latest.lease_token is None
    heartbeat_events = [
        event
        for event in observer.events
        if event.kind is ObservationEventKind.EXECUTION_LEASE_HEARTBEAT
    ]
    assert heartbeat_events
    assert {event.actor for event in heartbeat_events} == {"worker-heartbeat"}
    assert {event.execution_id for event in heartbeat_events} == {latest.id}


async def test_worker_host_recovers_expired_running_execution_on_redelivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([observer]))
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
        control=control,
        workflow=_workflow(),
    )
    session = await agent.start_session()
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service_module, "_now", lambda: opened_at)
    execution = await control.open_node_execution(
        session.id,
        "plan",
        trace_id="trace-expired-worker",
    )
    await control.grant_node_execution_lease(
        session.id,
        "plan",
        execution.id,
        owner="worker-dead",
        ttl_seconds=1,
    )
    monkeypatch.setattr(
        session_service_module,
        "_now",
        lambda: opened_at + timedelta(seconds=2),
    )
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-redelivery",
        lease_ttl_seconds=60,
    ).run_once()

    assert result.status == "completed"
    refreshed = await service.get_session(session.id)
    assert refreshed.status is SessionStatus.COMPLETED
    attempts = refreshed.nodes["plan"].attempts
    assert len(attempts) == 2
    assert attempts[0].status is AttemptStatus.ABORTED
    assert attempts[0].error == "execution lease expired"
    assert attempts[0].lease_token is None
    assert attempts[1].status is AttemptStatus.SUCCEEDED
    assert refreshed.nodes["plan"].status is NodeStatus.COMPLETED
    event_kinds = [event.kind for event in observer.events]
    assert ObservationEventKind.NODE_EXECUTION_ABORTED in event_kinds
    assert ObservationEventKind.NODE_REVISED in event_kinds


async def test_worker_host_redelivery_from_different_worker_recovers_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
        control=control,
        workflow=_workflow(),
    )
    session = await agent.start_session()
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service_module, "_now", lambda: opened_at)
    execution = await control.open_node_execution(
        session.id,
        "plan",
        trace_id="trace-multi-worker",
    )
    await control.grant_node_execution_lease(
        session.id,
        "plan",
        execution.id,
        owner="worker-lost",
        ttl_seconds=1,
    )
    monkeypatch.setattr(
        session_service_module,
        "_now",
        lambda: opened_at + timedelta(seconds=2),
    )
    queue = InMemoryQueueAdapter(visibility_timeout_seconds=0.01)
    await queue.enqueue(session.id)
    first_delivery = await queue.dequeue(timeout=1.0)
    assert first_delivery is not None
    assert first_delivery.attempt == 0

    await asyncio.sleep(0.02)
    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-redelivery",
        lease_ttl_seconds=60,
    ).run_once()

    assert result.status == "completed"
    assert result.worker_id == "worker-redelivery"
    assert result.queue_attempt == 1
    assert result.message_handle is not None
    assert result.message_handle != first_delivery.handle
    assert result.delivery_action is WorkerDeliveryAction.ACK
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0

    await queue.ack(first_delivery)
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0

    refreshed = await service.get_session(session.id)
    attempts = refreshed.nodes["plan"].attempts
    assert len(attempts) == 2
    assert attempts[0].status is AttemptStatus.ABORTED
    assert attempts[0].error == "execution lease expired"
    assert attempts[0].lease_owner is None
    assert attempts[1].status is AttemptStatus.SUCCEEDED


async def test_worker_host_releases_lease_when_gate_waits() -> None:
    observer = ListObserver()
    service = SessionService(InMemorySessionStore(), emitter=Emitter([observer]))

    async def gate_fn(
        session: RuntimeSession, node: NodeState, report: AgentReport
    ) -> Gate | None:
        del session, report
        return Gate(
            name="manual_review",
            node_key=node.key,
            outcome=GateOutcome.REQUIRES_DECISION,
        )

    agent = OrchestratorAgent(
        provider=MockProvider(scripted=[ScriptedTurn(content='{"needs_review": true}')]),
        control=ControlPlane(service, gate_fn=gate_fn),
        workflow=_workflow(),
    )
    session = await agent.start_session()
    queue = InMemoryQueueAdapter()
    await queue.enqueue(session.id)

    result = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-gate",
    ).run_once()

    assert result.status == "waiting_gate"
    refreshed = await service.get_session(session.id)
    latest = refreshed.nodes["plan"].latest_attempt()
    assert latest is not None
    assert latest.lease_token is None
    assert latest.lease_owner is None
    lease_kinds = [
        event.kind
        for event in observer.events
        if event.kind
        in {
            ObservationEventKind.EXECUTION_LEASE_GRANTED,
            ObservationEventKind.EXECUTION_LEASE_RELEASED,
        }
    ]
    assert lease_kinds == [
        ObservationEventKind.EXECUTION_LEASE_GRANTED,
        ObservationEventKind.EXECUTION_LEASE_RELEASED,
    ]


async def test_worker_host_nacks_delivery_errors_for_retry() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter()
    await queue.enqueue("missing-session")

    result = await QueueExecutionHost(queue=queue, orchestrator=agent).run_once()

    assert result.status == "error"
    assert result.session_id == "missing-session"
    assert result.queue_name == "in-memory"
    assert result.message_handle is not None
    assert result.queue_attempt == 0
    assert result.delivery_action is WorkerDeliveryAction.NACK_RETRY
    assert result.retry_on_error is True
    assert result.error_type == "SessionNotFoundError"
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
    assert await queue.pending() == 1
    retried = await queue.dequeue()
    assert retried is not None
    assert retried.attempt == 1
    assert retried.metadata["last_delivery_reason"] == "nack"


async def test_worker_host_can_drop_delivery_errors_without_retry() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter()
    await queue.enqueue("missing-session")

    result = await QueueExecutionHost(queue=queue, orchestrator=agent).run_once(
        retry_on_error=False
    )

    assert result.status == "error"
    assert result.delivery_action is WorkerDeliveryAction.NACK_DROP
    assert result.retry_on_error is False
    assert result.error_type == "SessionNotFoundError"
    assert await queue.pending() == 0
    assert await queue.in_flight() == 0


async def test_worker_host_reports_idle_when_queue_is_empty() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )

    result = await QueueExecutionHost(
        queue=InMemoryQueueAdapter(), orchestrator=agent
    ).run_once(timeout=0.001)

    assert result.status == "idle"
    assert result.session_id is None
    assert result.worker_id == "queue-worker"
    assert result.queue_name == "in-memory"
    assert result.delivery_action is WorkerDeliveryAction.IDLE
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_ms is not None
    assert result.duration_ms >= 0


async def test_worker_host_rejects_enqueue_only_queue_before_polling() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = CeleryQueueAdapter(celery_app=object())

    with pytest.raises(QueueCapabilityError) as excinfo:
        QueueExecutionHost(queue=queue, orchestrator=agent)

    message = str(excinfo.value)
    assert "does not support pollable delivery" in message
    assert "dequeue, ack, and nack" in message


async def test_worker_host_health_snapshot_reports_queue_liveness() -> None:
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=_workflow(),
    )
    queue = InMemoryQueueAdapter(max_delivery_attempts=1)
    await queue.enqueue("dead-session")
    dead_delivery = await queue.dequeue(timeout=1.0)
    assert dead_delivery is not None
    await queue.nack(dead_delivery, retry=True)
    await queue.enqueue("in-flight-session")
    await queue.enqueue("pending-session")
    in_flight = await queue.dequeue(timeout=1.0)
    assert in_flight is not None

    snapshot = await QueueExecutionHost(
        queue=queue,
        orchestrator=agent,
        worker_id="worker-health",
    ).health()

    assert snapshot.worker_id == "worker-health"
    assert snapshot.queue_name == "in-memory"
    assert snapshot.pending == 1
    assert snapshot.in_flight == 1
    assert snapshot.dead_letters == 1
    assert snapshot.checked_at.tzinfo is UTC


async def test_runtime_queue_health_helper_requires_no_orchestrator() -> None:
    queue = InMemoryQueueAdapter()
    await queue.enqueue("pending-session")

    snapshot = await queue_health(
        queue_adapter=queue,
        worker_id="runtime-health",
    )

    assert snapshot.worker_id == "runtime-health"
    assert snapshot.queue_name == "in-memory"
    assert snapshot.pending == 1
    assert snapshot.in_flight == 0
    assert snapshot.dead_letters == 0
    assert snapshot.checked_at.tzinfo is UTC


async def test_runtime_queue_dead_letters_helper_reports_queue_local_entries() -> None:
    queue = InMemoryQueueAdapter(max_delivery_attempts=1)
    await queue.enqueue("dead-session")
    delivery = await queue.dequeue(timeout=1.0)
    assert delivery is not None
    await queue.nack(delivery, retry=True)

    dead_letters = await queue_dead_letters(queue_adapter=queue)

    assert len(dead_letters) == 1
    assert dead_letters[0].session_id == "dead-session"
    assert dead_letters[0].metadata["dead_letter_source"] == "nack"


async def test_runtime_queue_dead_letter_replay_fails_closed_without_support() -> None:
    queue = InMemoryQueueAdapter(max_delivery_attempts=1)

    with pytest.raises(QueueCapabilityError, match="replay_dead_letters"):
        await replay_queue_dead_letters(queue_adapter=queue, limit=1)


async def test_worker_goal_once_completes_when_artifact_satisfies_quality_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "goal.sqlite"
    artifact_root = tmp_path / "artifacts"
    # The artifact written by the scripted backend must satisfy the quality
    # contract: at least 100 chars and contain ``# Intro``.
    body = "# Intro\n" + ("x" * 200)
    backend = MockProvider(
        scripted=[
            _plan_turn(tools=("artifact.write_text",)),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="write",
                        name="artifact.write_text",
                        arguments={"path": "report.md", "content": body},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)
    contract = GoalArtifactQualityContract(
        min_length=100,
        required_sections=("# Intro",),
    )

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Write a report",
            available_tools=("artifact.write_text",),
            expected_artifacts=("report.md",),
            quality_contract=contract,
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        artifact_root=str(artifact_root),
        planner_name="model",
        quality_contract=contract,
    )
    # The persisted session must already carry the contract on the planned task.
    stored = await SqliteSessionStore(store_path).get(session.id)
    assert stored is not None
    persisted_contract = stored.metadata["execution_plan"]["tasks"][-1][
        "contract"
    ]["quality_contract"]
    assert persisted_contract["min_length"] == 100
    assert persisted_contract["required_sections"] == ["# Intro"]

    result = await run_queued_goal_session_once(
        session_id=session.id,
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        artifact_root=str(artifact_root),
        planner_name="model",
    )

    assert result.status == "completed", result
    refreshed = await SqliteSessionStore(store_path).get(session.id)
    assert refreshed is not None
    assert refreshed.status is SessionStatus.COMPLETED
    assert (artifact_root / "report.md").read_text(encoding="utf-8") == body


async def test_create_queued_goal_session_persists_runtime_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "overrides-enqueue.sqlite"
    backend = MockProvider(
        scripted=[_plan_turn(tools=("artifact.write_text",))]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Persist overrides",
            available_tools=("artifact.write_text",),
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
        enable_episodic_memory=True,
        enable_agent_memory=True,
        max_tool_rounds=8,
    )
    stored = await SqliteSessionStore(store_path).get(session.id)
    assert stored is not None
    overrides = stored.metadata.get("runtime_overrides")
    assert overrides == {
        "enable_episodic_memory": True,
        "enable_agent_memory": True,
        "max_tool_rounds": 8,
    }


async def test_create_queued_goal_session_persists_memory_store_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "memory-overrides.sqlite"
    memory_path = tmp_path / "goal-memory.sqlite"
    backend = MockProvider(scripted=[_plan_turn(tools=("artifact.write_text",))])
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Persist memory store overrides",
            available_tools=("artifact.write_text",),
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
        enable_agent_memory=True,
        memory_store_kind="sqlite",
        memory_store_path=memory_path,
    )

    stored = await SqliteSessionStore(store_path).get(session.id)
    assert stored is not None
    assert stored.metadata.get("runtime_overrides") == {
        "enable_agent_memory": True,
        "memory_store_kind": "sqlite",
        "memory_store_path": str(memory_path),
    }


def _build_semantic_runtime_bundle_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Patch build_goal_runtime_bundle to capture the effective kwargs."""

    captured: list[dict[str, Any]] = []
    real_builder = runtime_module.build_goal_runtime_bundle

    def _capture(**kwargs: Any):
        captured.append(kwargs)
        return real_builder(**kwargs)

    monkeypatch.setattr(runtime_module, "build_goal_runtime_bundle", _capture)
    return captured


async def test_worker_goal_once_inherits_persisted_runtime_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "worker-inherits-overrides.sqlite"
    memory_path = tmp_path / "worker-inherits-memory.sqlite"
    backend = MockProvider(
        scripted=[
            _plan_turn(tools=("artifact.write_text",)),
            ScriptedTurn(content='{"done": true}'),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Inherit runtime overrides",
            available_tools=("artifact.write_text",),
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
        enable_episodic_memory=True,
        enable_agent_memory=True,
        memory_store_kind="sqlite",
        memory_store_path=memory_path,
        max_tool_rounds=8,
        max_auto_repairs=2,
    )

    captured = _build_semantic_runtime_bundle_capture(monkeypatch)
    # Worker did NOT pass the flags — must inherit from session metadata.
    await run_queued_goal_session_once(
        session_id=session.id,
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
        enable_episodic_memory=False,
        enable_agent_memory=False,
    )
    assert captured, "build_goal_runtime_bundle must be invoked once"
    kwargs = captured[-1]
    assert kwargs["enable_episodic_memory"] is True
    assert kwargs["enable_agent_memory"] is True
    assert kwargs["memory_store_kind"] == "sqlite"
    assert kwargs["memory_store_path"] == memory_path
    assert kwargs["max_tool_rounds"] == 8
    assert kwargs["max_auto_repairs"] == 2

    entries = await SqliteMemoryStore(memory_path).scan(
        MemoryScope.WORKFLOW,
        "goals",
        key_prefix=f"episode.{session.id}",
    )
    assert entries


async def test_persisted_overrides_missing_session_degrades_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuinely absent session means defaults — quietly, but logged.

    Regression for PLAN-20260612-001 D2: the loader swallowed every exception,
    so a missing session and a broken store were indistinguishable.
    """
    from hydramind.runtime_queue_goal import _persisted_runtime_overrides

    store = InMemorySessionStore()
    with caplog.at_level("WARNING", logger="hydramind.runtime.queue_goal"):
        overrides = await _persisted_runtime_overrides(store, "no-such-session")

    assert overrides == {}
    assert any(
        "no-such-session" in record.getMessage() for record in caplog.records
    ), "missing session must leave a warning trail"


async def test_persisted_overrides_propagates_store_failures() -> None:
    """A failing store must propagate so the delivery is retried (D2).

    Silently returning {} would run the queued goal with default runtime
    configuration instead of the persisted one.
    """
    from hydramind.runtime_queue_goal import _persisted_runtime_overrides

    class BrokenStore(InMemorySessionStore):
        async def get(self, session_id: str) -> RuntimeSession | None:
            raise RuntimeError("store unavailable")

    with pytest.raises(RuntimeError, match="store unavailable"):
        await _persisted_runtime_overrides(BrokenStore(), "any-session")


async def test_persisted_overrides_never_swallows_cancellation() -> None:
    """CancelledError must pass through the override loader untouched (D2)."""
    from hydramind.runtime_queue_goal import _persisted_runtime_overrides

    class CancelledStore(InMemorySessionStore):
        async def get(self, session_id: str) -> RuntimeSession | None:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _persisted_runtime_overrides(CancelledStore(), "any-session")


async def test_worker_goal_once_cli_flag_overrides_persisted_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "worker-cli-overrides.sqlite"
    backend = MockProvider(
        scripted=[
            _plan_turn(tools=("artifact.write_text",)),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)

    session = await create_queued_goal_session(
        GoalSpec(
            objective="CLI override flag",
            available_tools=("artifact.write_text",),
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
    )
    stored = await SqliteSessionStore(store_path).get(session.id)
    assert stored is not None
    # Default (no overrides) — nothing persisted.
    assert stored.metadata.get("runtime_overrides") is None

    captured = _build_semantic_runtime_bundle_capture(monkeypatch)
    await run_queued_goal_session_once(
        session_id=session.id,
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
        enable_episodic_memory=True,
        enable_agent_memory=True,
        max_tool_rounds=12,
    )
    assert captured
    kwargs = captured[-1]
    assert kwargs["enable_episodic_memory"] is True
    assert kwargs["enable_agent_memory"] is True
    assert kwargs["max_tool_rounds"] == 12


async def test_worker_goal_once_defaults_off_when_neither_side_specifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "worker-defaults-off.sqlite"
    backend = MockProvider(
        scripted=[
            _plan_turn(tools=("artifact.write_text",)),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Default off",
            available_tools=("artifact.write_text",),
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
    )

    captured = _build_semantic_runtime_bundle_capture(monkeypatch)
    await run_queued_goal_session_once(
        session_id=session.id,
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        planner_name="model",
    )
    kwargs = captured[-1]
    assert kwargs["enable_episodic_memory"] is False
    assert kwargs["enable_agent_memory"] is False
    assert kwargs["max_tool_rounds"] is None
    assert kwargs["max_auto_repairs"] is None


async def test_worker_goal_once_halts_at_gate_on_containment_safety_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "goal.sqlite"
    artifact_root = tmp_path / "artifacts"
    # The artifact exists but references a local image asset that escapes
    # artifact_root, so the surviving boundary SAFETY verifier (artifact-root
    # containment) must fail. The planner agent then declines to repair (empty
    # revise delta), so the run halts at ``WAITING_GATE`` for an external
    # decision. No rule quality threshold is involved — only safety determinism.
    backend = MockProvider(
        scripted=[
            _plan_turn(tools=("artifact.write_text",)),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="write",
                        name="artifact.write_text",
                        arguments={
                            "path": "report.md",
                            "content": "![escape](../../secret.png)",
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": false}'),
            # The planner agent declines to repair (empty delta) -> gate surfaced.
            _noop_revise_turn(),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: backend)
    contract = GoalArtifactQualityContract()

    session = await create_queued_goal_session(
        GoalSpec(
            objective="Write a report",
            available_tools=("artifact.write_text",),
            expected_artifacts=("report.md",),
            quality_contract=contract,
        ),
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        artifact_root=str(artifact_root),
        planner_name="model",
        quality_contract=contract,
    )

    result = await run_queued_goal_session_once(
        session_id=session.id,
        provider_name="scripted",
        env_file=None,
        session_store_kind="sqlite",
        store_path=store_path,
        artifact_root=str(artifact_root),
        planner_name="model",
    )

    assert result.status == "waiting_gate", result
    refreshed = await SqliteSessionStore(store_path).get(session.id)
    assert refreshed is not None
    assert refreshed.status is SessionStatus.WAITING_GATE
    work_node = refreshed.nodes["work"]
    gate = work_node.latest_gate()
    assert gate is not None
    assert gate.name == "verifier_feedback"
    failed_names = {
        item.get("name")
        for item in gate.detail.get("failed_verifiers", [])
        if isinstance(item, dict)
    }
    assert "artifact.local_assets_contained" in failed_names
