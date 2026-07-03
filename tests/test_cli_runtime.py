"""Runtime CLI coverage for sqlite, worker, and doctor commands."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from hydramind import cli
from hydramind import cli_queue as cli_queue_module
from hydramind import runtime as runtime_module
from hydramind.control import SessionStatus, SqliteSessionStore
from hydramind.harness import (
    InvocationResult,
    Message,
    ModelHint,
    ModelProvider,
    StopReason,
    ToolCall,
    ToolSpec,
)
from hydramind.memory import MemoryScope, SqliteMemoryStore
from hydramind.orchestration import GoalSpec
from hydramind.queue import QueueMessage, RedisStreamQueueAdapter
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import external_tools
from tests.queue.fake_redis_streams import FakeRedisStreams


def _plan_turn(*tasks: dict[str, object]) -> ScriptedTurn:
    """A scripted ``ModelGoalPlanner`` turn returning deterministic plan JSON.

    S96 deleted the rule planners; offline determinism for goal CLI tests comes
    from scripting the agent planner's plan JSON (the goal's expected-artifacts/
    quality-contract projection is then applied by the planner output path).
    """
    payload = list(tasks) or [{"key": "work", "role": "executor"}]
    return ScriptedTurn(content=json.dumps({"tasks": payload}))


class AutoModelPlannerProvider(ModelProvider):
    name = "auto-model-test"

    def __init__(
        self,
        *,
        planner_content: str | None = None,
        planner_failures: int = 0,
    ) -> None:
        self.invocation_roles: list[str | None] = []
        self._planner_content = planner_content
        self._planner_failures = planner_failures

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
        self.invocation_roles.append(role)
        if role == "planner":
            if self._planner_failures:
                self._planner_failures -= 1
                raise TimeoutError("planner read operation timed out")
            return InvocationResult(
                content=self._planner_content
                or """
                {
                  "name": "auto-model-plan",
                  "tasks": [
                    {
                      "key": "write",
                      "role": "writer",
                      "description": "write note"
                    }
                  ]
                }
                """,
                stop_reason=StopReason.END_TURN,
                model_id="planner-model",
            )
        return InvocationResult(
            content='{"done": true}',
            stop_reason=StopReason.END_TURN,
            model_id="executor-model",
        )

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        del model_hint
        return "planner-model" if role == "planner" else "executor-model"

    def context_limit(self, role: str | None = None) -> int:
        del role
        return 4096


def _patch_cli_redis_queue(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: FakeRedisStreams,
) -> None:
    def _factory(
        kind: str,
        *,
        redis_url: str | None = None,
        stream_key: str = "hydramind:sessions",
        group_name: str = "hydramind-workers",
        consumer_name: str = "hydramind-worker",
        visibility_timeout_seconds: float | None = 60.0,
        max_delivery_attempts: int | None = None,
    ):
        del redis_url
        assert kind == "redis"
        return RedisStreamQueueAdapter(
            redis_client=fake_redis,
            stream_key=stream_key,
            group_name=group_name,
            consumer_name=consumer_name,
            visibility_timeout_seconds=visibility_timeout_seconds,
            max_delivery_attempts=max_delivery_attempts,
        )

    monkeypatch.setattr(cli_queue_module, "create_queue_adapter", _factory)


async def _seed_fake_redis_health_counts(
    fake_redis: FakeRedisStreams,
    *,
    stream_key: str,
) -> None:
    queue = RedisStreamQueueAdapter(
        redis_client=fake_redis,
        stream_key=stream_key,
        max_delivery_attempts=1,
    )
    try:
        await queue.enqueue("sess-in-flight")
        in_flight = await queue.dequeue(timeout=0.001)
        assert in_flight is not None

        await queue.enqueue("sess-dead")
        dead_delivery = await queue.dequeue(timeout=0.001)
        assert dead_delivery is not None
        await queue.nack(dead_delivery, retry=True)

        await queue.enqueue("sess-pending")
    finally:
        await queue.close()


async def _seed_fake_redis_dead_letters(
    fake_redis: FakeRedisStreams,
    *,
    stream_key: str,
    session_ids: tuple[str, ...],
) -> tuple[QueueMessage, ...]:
    queue = RedisStreamQueueAdapter(
        redis_client=fake_redis,
        stream_key=stream_key,
        max_delivery_attempts=1,
    )
    try:
        for session_id in session_ids:
            await queue.enqueue(session_id, metadata={"kind": "poison"})
            delivery = await queue.dequeue(timeout=0.001)
            assert delivery is not None
            await queue.nack(delivery, retry=True)
        return await queue.dead_letters()
    finally:
        await queue.close()


def test_cli_enqueue_only_then_worker_once_with_sqlite_store(tmp_path, capsys) -> None:
    store_path = tmp_path / "sessions.sqlite"
    workflow = "examples/short_video/workflow.yaml"

    create_rc = cli.main(
        [
            "run",
            workflow,
            "--provider",
            "mock",
            "--input",
            "topic=Python",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)

    worker_rc = cli.main(
        [
            "worker",
            "once",
            workflow,
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created["session_id"],
        ]
    )
    worker = json.loads(capsys.readouterr().out)

    assert create_rc == 0
    assert created["queued_only"] is True
    assert created["status"] == "queued"
    assert worker_rc == 0
    assert worker["status"] == "completed"
    assert worker["session_id"] == created["session_id"]


def test_cli_goal_enqueue_only_then_worker_goal_once_with_sqlite_store(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-sessions.sqlite"
    memory_path = tmp_path / "goal-memory.sqlite"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]}),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    create_rc = cli.main(
        [
            "goal",
            "Draft queued goal note",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--memory-store",
            "sqlite",
            "--memory-store-path",
            str(memory_path),
            "--enable-episodic-memory",
            "--enable-agent-memory",
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))

    worker_rc = cli.main(
        [
            "worker",
            "goal-once",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created["session_id"],
        ]
    )
    worker = json.loads(capsys.readouterr().out)

    assert create_rc == 0
    assert created["queued_only"] is True
    assert created["status"] == "queued"
    assert created["goal"] == "Draft queued goal note"
    assert created["plan_tasks"] == ["work"]
    assert created["enable_episodic_memory"] is True
    assert created["enable_agent_memory"] is True
    assert created["memory_store"] == "sqlite"
    assert created["memory_store_path"] == str(memory_path)
    assert stored is not None
    assert stored.metadata["runtime_overrides"] == {
        "enable_episodic_memory": True,
        "enable_agent_memory": True,
        "memory_store_kind": "sqlite",
        "memory_store_path": str(memory_path),
    }
    assert worker_rc == 0
    assert worker["status"] == "completed"
    assert worker["session_id"] == created["session_id"]
    episode_entries = asyncio.run(
        SqliteMemoryStore(memory_path).scan(
            MemoryScope.WORKFLOW,
            "goals",
            key_prefix=f"episode.{created['session_id']}",
        )
    )
    assert episode_entries


def test_cli_enqueue_only_then_worker_loop_with_sqlite_store(tmp_path, capsys) -> None:
    store_path = tmp_path / "loop-sessions.sqlite"
    workflow = "examples/short_video/workflow.yaml"
    created_sessions: list[str] = []

    for topic in ("Python", "Agents"):
        create_rc = cli.main(
            [
                "run",
                workflow,
                "--provider",
                "mock",
                "--input",
                f"topic={topic}",
                "--env-file",
                "/tmp/hydramind-missing-env",
                "--session-store",
                "sqlite",
                "--store-path",
                str(store_path),
                "--enqueue-only",
            ]
        )
        created = json.loads(capsys.readouterr().out)
        assert create_rc == 0
        assert created["status"] == "queued"
        created_sessions.append(created["session_id"])

    worker_rc = cli.main(
        [
            "worker",
            "loop",
            workflow,
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created_sessions[0],
            "--session-id",
            created_sessions[1],
            "--max-idle-cycles",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)

    assert worker_rc == 0
    assert loop["stop_reason"] == "max_idle_cycles"
    assert loop["iterations"] == 3
    assert loop["deliveries"] == 2
    assert loop["acked"] == 2
    assert loop["errors"] == 0
    assert loop["exit_code"] == worker_rc == 0
    assert loop["restart_recommended"] is False
    assert loop["last_result"]["status"] == "idle"

    store = SqliteSessionStore(store_path)
    refreshed = [
        asyncio.run(store.get(session_id))
        for session_id in created_sessions
    ]
    assert [session.status for session in refreshed if session is not None] == [
        SessionStatus.COMPLETED,
        SessionStatus.COMPLETED,
    ]


def test_cli_enqueue_only_with_redis_queue_then_worker_loop(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "redis-loop-sessions.sqlite"
    workflow = "examples/short_video/workflow.yaml"
    stream_key = "hydramind:test:workflow"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)

    create_rc = cli.main(
        [
            "run",
            workflow,
            "--provider",
            "mock",
            "--input",
            "topic=Redis",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
        ]
    )
    created = json.loads(capsys.readouterr().out)
    queued_fields = fake_redis.streams[stream_key][0][1]

    worker_rc = cli.main(
        [
            "worker",
            "loop",
            workflow,
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--max-idle-cycles",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)
    refreshed = asyncio.run(
        SqliteSessionStore(store_path).get(created["session_id"])
    )

    assert create_rc == 0
    assert created["queued_only"] is True
    assert created["queue"]["kind"] == "redis"
    assert created["queue"]["name"] == "redis-stream"
    assert created["queue"]["session_id"] == created["session_id"]
    assert queued_fields["session_id"] == created["session_id"]
    assert queued_fields["metadata"] == "{}"
    assert worker_rc == 0
    assert loop["queue_name"] == "redis-stream"
    assert loop["deliveries"] == 1
    assert loop["acked"] == 1
    assert loop["errors"] == 0
    assert loop["exit_code"] == worker_rc == 0
    assert loop["restart_recommended"] is False
    assert refreshed is not None
    assert refreshed.status is SessionStatus.COMPLETED


def test_cli_worker_health_reports_redis_queue_counts(
    capsys,
    monkeypatch,
) -> None:
    stream_key = "hydramind:test:health"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    asyncio.run(
        _seed_fake_redis_health_counts(fake_redis, stream_key=stream_key)
    )

    rc = cli.main(
        [
            "worker",
            "health",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--queue-max-delivery-attempts",
            "1",
            "--worker-id",
            "ops-health",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["worker_id"] == "ops-health"
    assert payload["queue_name"] == "redis-stream"
    assert payload["pending"] == 1
    assert payload["in_flight"] == 1
    assert payload["dead_letters"] == 1
    assert "checked_at" in payload


def test_cli_worker_readiness_reports_redis_sqlite_ready(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    store_path = tmp_path / "sessions.sqlite"

    rc = cli.main(
        [
            "worker",
            "readiness",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--worker-id",
            "ops-readiness",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ready"] is True
    assert payload["distributed_worker_ready"] is True
    assert payload["local_worker_ready"] is True
    assert payload["worker_id"] == "ops-readiness"
    assert payload["queue_name"] == "redis-stream"
    assert payload["queue_pollable"] is True
    assert payload["queue_distribution"] == "broker"
    assert payload["session_store_kind"] == "sqlite"
    assert payload["session_store_path"] == str(store_path)
    assert payload["session_store_persistent"] is True
    assert payload["session_store_cas_capable"] is True
    assert "queue_supports_pollable_delivery" in payload["reasons"]
    assert "queue_is_broker_backed" in payload["reasons"]
    assert "sqlite_session_store_is_persistent_cas_capable" in payload["reasons"]
    assert "distributed_worker_preflight_ready" in payload["reasons"]


def test_cli_worker_readiness_reports_in_memory_store_not_ready(
    capsys,
    monkeypatch,
) -> None:
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)

    rc = cli.main(
        [
            "worker",
            "readiness",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--session-store",
            "memory",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ready"] is False
    assert payload["distributed_worker_ready"] is False
    assert payload["local_worker_ready"] is True
    assert payload["queue_distribution"] == "broker"
    assert payload["session_store_durability"] == "process_local"
    assert "in_memory_session_store_is_process_local" in payload["reasons"]
    assert "distributed_worker_preflight_not_ready" in payload["reasons"]


def test_cli_worker_dead_letters_list_reports_redis_entries_without_mutation(
    capsys,
    monkeypatch,
) -> None:
    stream_key = "hydramind:test:dead-letters-list"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    seeded = asyncio.run(
        _seed_fake_redis_dead_letters(
            fake_redis,
            stream_key=stream_key,
            session_ids=("sess-dlq-1", "sess-dlq-2"),
        )
    )

    rc = cli.main(
        [
            "worker",
            "dead-letters",
            "list",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--limit",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    remaining = asyncio.run(
        RedisStreamQueueAdapter(
            redis_client=fake_redis,
            stream_key=stream_key,
        ).dead_letters()
    )

    assert rc == 0
    assert payload["queue_name"] == "redis-stream"
    assert payload["command"] == "list"
    assert payload["limit"] == 1
    assert payload["count"] == 1
    assert payload["dead_letters"][0]["session_id"] == "sess-dlq-1"
    assert payload["dead_letters"][0]["handle"] == seeded[0].handle
    assert [message.session_id for message in remaining] == [
        "sess-dlq-1",
        "sess-dlq-2",
    ]


def test_cli_worker_dead_letters_replay_requeues_bounded_batch(
    capsys,
    monkeypatch,
) -> None:
    stream_key = "hydramind:test:dead-letters-replay"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    asyncio.run(
        _seed_fake_redis_dead_letters(
            fake_redis,
            stream_key=stream_key,
            session_ids=("sess-dlq-1", "sess-dlq-2"),
        )
    )

    rc = cli.main(
        [
            "worker",
            "dead-letters",
            "replay",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--limit",
            "1",
            "--metadata",
            "operator=retry-once",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    inspection_queue = RedisStreamQueueAdapter(
        redis_client=fake_redis,
        stream_key=stream_key,
    )
    pending = asyncio.run(inspection_queue.pending())
    remaining = asyncio.run(inspection_queue.dead_letters())

    assert rc == 0
    assert payload["queue_name"] == "redis-stream"
    assert payload["command"] == "replay"
    assert payload["limit"] == 1
    assert payload["count"] == 1
    assert payload["reset_attempt"] is True
    assert payload["removed_from_dead_letters"] is True
    assert payload["replayed"][0]["session_id"] == "sess-dlq-1"
    assert payload["replayed"][0]["attempt"] == 0
    assert payload["replayed"][0]["metadata"]["operator"] == "retry-once"
    assert payload["replayed"][0]["metadata"]["replay_source"] == "dead_letter"
    assert pending == 1
    assert [message.session_id for message in remaining] == ["sess-dlq-2"]


def test_cli_enqueue_only_with_redis_queue_then_worker_daemon(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "redis-daemon-sessions.sqlite"
    workflow = "examples/short_video/workflow.yaml"
    stream_key = "hydramind:test:workflow-daemon"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)

    create_rc = cli.main(
        [
            "run",
            workflow,
            "--provider",
            "mock",
            "--input",
            "topic=Redis daemon",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
        ]
    )
    created = json.loads(capsys.readouterr().out)

    worker_rc = cli.main(
        [
            "worker",
            "daemon",
            workflow,
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--max-iterations",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)
    refreshed = asyncio.run(
        SqliteSessionStore(store_path).get(created["session_id"])
    )

    assert create_rc == 0
    assert created["queue"]["name"] == "redis-stream"
    assert worker_rc == 0
    assert loop["stop_reason"] == "max_iterations"
    assert loop["queue_name"] == "redis-stream"
    assert loop["iterations"] == 1
    assert loop["deliveries"] == 1
    assert loop["acked"] == 1
    assert loop["errors"] == 0
    assert loop["exit_code"] == worker_rc == 0
    assert loop["restart_recommended"] is False
    assert refreshed is not None
    assert refreshed.status is SessionStatus.COMPLETED


def test_cli_goal_enqueue_only_then_worker_goal_loop_inherits_memory_store(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-loop-sessions.sqlite"
    memory_path = tmp_path / "goal-loop-memory.sqlite"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "first", "role": "writer"}),
            _plan_turn({"key": "second", "role": "writer"}),
            ScriptedTurn(content='{"goal": 1}'),
            ScriptedTurn(content='{"goal": 2}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)
    created_sessions: list[str] = []

    for objective in ("Loop goal one", "Loop goal two"):
        create_rc = cli.main(
            [
                "goal",
                objective,
                "--provider",
                "scripted",
                "--env-file",
                "/tmp/hydramind-missing-env",
                "--session-store",
                "sqlite",
                "--store-path",
                str(store_path),
                "--memory-store",
                "sqlite",
                "--memory-store-path",
                str(memory_path),
                "--enable-episodic-memory",
                "--enable-agent-memory",
                "--enqueue-only",
            ]
        )
        created = json.loads(capsys.readouterr().out)
        assert create_rc == 0
        assert created["status"] == "queued"
        created_sessions.append(created["session_id"])

    worker_rc = cli.main(
        [
            "worker",
            "goal-loop",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created_sessions[0],
            "--session-id",
            created_sessions[1],
            "--max-idle-cycles",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)

    assert worker_rc == 0
    assert loop["deliveries"] == 2
    assert loop["acked"] == 2
    assert loop["errors"] == 0
    assert loop["exit_code"] == worker_rc == 0
    assert loop["restart_recommended"] is False

    store = SqliteSessionStore(store_path)
    refreshed = [
        asyncio.run(store.get(session_id))
        for session_id in created_sessions
    ]
    assert [session.status for session in refreshed if session is not None] == [
        SessionStatus.COMPLETED,
        SessionStatus.COMPLETED,
    ]
    for session_id in created_sessions:
        episode_entries = asyncio.run(
            SqliteMemoryStore(memory_path).scan(
                MemoryScope.WORKFLOW,
                "goals",
                key_prefix=f"episode.{session_id}",
            )
        )
        assert episode_entries


def test_cli_goal_redis_queue_goal_loop_inherits_memory_store(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-redis-loop-sessions.sqlite"
    memory_path = tmp_path / "goal-redis-loop-memory.sqlite"
    stream_key = "hydramind:test:goal"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer"}),
            ScriptedTurn(content='{"goal": "redis"}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    create_rc = cli.main(
        [
            "goal",
            "Loop goal via Redis",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--memory-store",
            "sqlite",
            "--memory-store-path",
            str(memory_path),
            "--enable-episodic-memory",
            "--enable-agent-memory",
            "--enqueue-only",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))
    queued_fields = fake_redis.streams[stream_key][0][1]

    worker_rc = cli.main(
        [
            "worker",
            "goal-loop",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--max-idle-cycles",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)
    episode_entries = asyncio.run(
        SqliteMemoryStore(memory_path).scan(
            MemoryScope.WORKFLOW,
            "goals",
            key_prefix=f"episode.{created['session_id']}",
        )
    )

    assert create_rc == 0
    assert created["queue"]["name"] == "redis-stream"
    assert queued_fields["session_id"] == created["session_id"]
    assert queued_fields["metadata"] == "{}"
    assert stored is not None
    assert stored.metadata["runtime_overrides"] == {
        "enable_episodic_memory": True,
        "enable_agent_memory": True,
        "memory_store_kind": "sqlite",
        "memory_store_path": str(memory_path),
    }
    assert worker_rc == 0
    assert loop["queue_name"] == "redis-stream"
    assert loop["deliveries"] == 1
    assert loop["acked"] == 1
    assert loop["errors"] == 0
    assert loop["exit_code"] == worker_rc == 0
    assert loop["restart_recommended"] is False
    assert episode_entries


def test_cli_goal_redis_queue_goal_daemon_inherits_memory_store(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-redis-daemon-sessions.sqlite"
    memory_path = tmp_path / "goal-redis-daemon-memory.sqlite"
    stream_key = "hydramind:test:goal-daemon"
    fake_redis = FakeRedisStreams()
    _patch_cli_redis_queue(monkeypatch, fake_redis)
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer"}),
            ScriptedTurn(content='{"goal": "redis-daemon"}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    create_rc = cli.main(
        [
            "goal",
            "Daemon goal via Redis",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--memory-store",
            "sqlite",
            "--memory-store-path",
            str(memory_path),
            "--enable-episodic-memory",
            "--enable-agent-memory",
            "--enqueue-only",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))

    worker_rc = cli.main(
        [
            "worker",
            "goal-daemon",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--queue-stream-key",
            stream_key,
            "--max-iterations",
            "1",
            "--timeout",
            "0.001",
        ]
    )
    loop = json.loads(capsys.readouterr().out)
    episode_entries = asyncio.run(
        SqliteMemoryStore(memory_path).scan(
            MemoryScope.WORKFLOW,
            "goals",
            key_prefix=f"episode.{created['session_id']}",
        )
    )

    assert create_rc == 0
    assert created["queue"]["name"] == "redis-stream"
    assert stored is not None
    assert stored.metadata["runtime_overrides"] == {
        "enable_episodic_memory": True,
        "enable_agent_memory": True,
        "memory_store_kind": "sqlite",
        "memory_store_path": str(memory_path),
    }
    assert worker_rc == 0
    assert loop["stop_reason"] == "max_iterations"
    assert loop["queue_name"] == "redis-stream"
    assert loop["iterations"] == 1
    assert loop["deliveries"] == 1
    assert loop["acked"] == 1
    assert loop["errors"] == 0
    assert episode_entries


def test_cli_worker_loop_requires_a_stop_bound(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "loop",
            "examples/short_video/workflow.yaml",
            "--session-id",
            "sess-missing-bound",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_loop_requires_stop_bound"


def test_cli_worker_loop_requires_session_id_or_queue(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "loop",
            "examples/short_video/workflow.yaml",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
            "--max-iterations",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_loop_requires_work_source"


def test_cli_worker_loop_redis_queue_requires_url(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "loop",
            "examples/short_video/workflow.yaml",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
            "--queue",
            "redis",
            "--max-iterations",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "queue_redis_url_required"


def test_cli_worker_health_requires_queue(capsys) -> None:
    rc = cli.main(["worker", "health"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_health_requires_queue"


def test_cli_worker_health_redis_queue_requires_url(capsys) -> None:
    rc = cli.main(["worker", "health", "--queue", "redis"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "queue_redis_url_required"


def test_cli_worker_readiness_requires_queue(capsys) -> None:
    rc = cli.main(["worker", "readiness"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_readiness_requires_queue"


def test_cli_worker_readiness_redis_queue_requires_url(capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "readiness",
            "--queue",
            "redis",
            "--session-store",
            "sqlite",
            "--store-path",
            "/tmp/sessions.sqlite",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "queue_redis_url_required"


def test_cli_worker_readiness_sqlite_requires_store_path(capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "readiness",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--session-store",
            "sqlite",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_readiness_sqlite_requires_store_path"


def test_cli_worker_dead_letters_list_requires_queue(capsys) -> None:
    rc = cli.main(["worker", "dead-letters", "list"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_dead_letters_requires_queue"


def test_cli_worker_dead_letters_redis_queue_requires_url(capsys) -> None:
    rc = cli.main(["worker", "dead-letters", "list", "--queue", "redis"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "queue_redis_url_required"


def test_cli_worker_dead_letters_replay_requires_limit(capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "dead-letters",
            "replay",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_dead_letters_replay_requires_limit"


def test_cli_worker_dead_letters_rejects_non_positive_limit(capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "dead-letters",
            "list",
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
            "--limit",
            "0",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_dead_letters_invalid_limit"


def test_cli_worker_daemon_requires_redis_queue(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "daemon",
            "examples/short_video/workflow.yaml",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_daemon_requires_queue"


def test_cli_goal_daemon_rejects_explicit_session_id(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "goal-daemon",
            "--session-id",
            "sess-not-daemon-source",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
            "--queue",
            "redis",
            "--queue-redis-url",
            "redis://fake",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "worker_daemon_rejects_session_id"


def test_cli_worker_loop_delivery_errors_return_nonzero(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "worker",
            "loop",
            "examples/short_video/workflow.yaml",
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(tmp_path / "sessions.sqlite"),
            "--session-id",
            "missing-session",
            "--max-iterations",
            "1",
        ]
    )
    loop = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert loop["iterations"] == 1
    assert loop["deliveries"] == 1
    assert loop["acked"] == 0
    assert loop["nack_retried"] == 1
    assert loop["nack_dropped"] == 0
    assert loop["errors"] == 1
    assert loop["exit_code"] == rc == 1
    assert loop["restart_recommended"] is True
    assert loop["last_result"]["status"] == "error"
    assert loop["last_result"]["delivery_action"] == "nack_retry"


def test_cli_goal_enqueue_only_persists_required_tools(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-required.sqlite"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]})
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Queue required tool goal",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--required-tool",
            "artifact.write_text",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))

    assert rc == 0
    assert created["queued_only"] is True
    assert created["required_tools"] == ["artifact.write_text"]
    assert "required_tool_evidence" not in created
    assert stored is not None
    assert stored.metadata["goal"]["required_tools"] == ["artifact.write_text"]


def test_cli_goal_enqueue_only_persists_expected_artifacts(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-artifact.sqlite"
    artifact_root = tmp_path / "artifacts"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]})
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Queue expected artifact goal",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--expected-artifact",
            "reports/delivery.md",
            "--artifact-root",
            str(artifact_root),
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))

    assert rc == 0
    assert created["queued_only"] is True
    assert created["expected_artifacts"] == ["reports/delivery.md"]
    assert created["artifact_root"] == str(artifact_root)
    assert stored is not None
    assert stored.metadata["goal"]["expected_artifacts"] == ["reports/delivery.md"]
    assert stored.metadata["execution_plan"]["tasks"][0]["contract"][
        "expected_artifacts"
    ] == ["reports/delivery.md"]


def test_cli_goal_runs_goal_driven_task_with_mock_backend(capsys, monkeypatch) -> None:
    provider = MockProvider(
        scripted=[
            _plan_turn(
                {
                    "key": "work",
                    "role": "executor",
                    "tools": ["search.web", "artifact.write_text"],
                }
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Draft a research note",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "search.web,artifact.write_text",
            "--constraint",
            "cite sources",
            "--success-criteria",
            "note text exists",
            "--input",
            "topic=HydraMind",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "completed", payload
    assert payload["goal"] == "Draft a research note"
    assert payload["workflow"].startswith("goal-draft-a-research-note")
    assert payload["plan_tasks"] == ["work"]


def test_cli_goal_reports_required_tool_evidence(capsys, monkeypatch) -> None:
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "executor", "tools": ["time.now"]}),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="time-call",
                        name="time.now",
                        arguments={},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"time_checked": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Check required time",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "time.now",
            "--required-tool",
            "time.now",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    required = {item["tool"]: item for item in payload["required_tool_evidence"]}
    assert rc == 0
    assert payload["status"] == "completed", payload
    assert payload["required_tools"] == ["time.now"]
    assert required["time.now"]["started"] is True
    assert required["time.now"]["succeeded"] is True
    assert required["time.now"]["statuses"] == ["succeeded"]


def test_cli_goal_completes_when_expected_artifact_written(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    artifact_root = tmp_path / "goal-artifacts"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]}),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="artifact-call",
                        name="artifact.write_text",
                        arguments={
                            "path": "reports/delivery.md",
                            "content": "# Delivery",
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Write expected artifact",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--expected-artifact",
            "reports/delivery.md",
            "--artifact-root",
            str(artifact_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "completed", payload
    assert payload["expected_artifacts"] == ["reports/delivery.md"]
    assert (artifact_root / "reports" / "delivery.md").exists()


def test_cli_goal_waits_gate_when_expected_artifact_missing(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    artifact_root = tmp_path / "goal-artifacts"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]}),
            ScriptedTurn(content='{"done": false}'),
            # Auto-repair triggers one agent revise that proposes no change.
            ScriptedTurn(content=json.dumps({"rationale": "no actionable change"})),
            ScriptedTurn(content='{"done": false}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Forget expected artifact",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--expected-artifact",
            "reports/missing.md",
            "--artifact-root",
            str(artifact_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "waiting_gate", payload
    assert payload["expected_artifacts"] == ["reports/missing.md"]
    assert not (artifact_root / "reports" / "missing.md").exists()


def test_cli_worker_goal_once_trace_path_writes_jsonl(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-trace-worker.sqlite"
    trace_path = tmp_path / "worker-trace.jsonl"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]}),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    create_rc = cli.main(
        [
            "goal",
            "Queue trace goal",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)

    worker_rc = cli.main(
        [
            "worker",
            "goal-once",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created["session_id"],
            "--trace-path",
            str(trace_path),
        ]
    )
    _ = json.loads(capsys.readouterr().out)
    assert create_rc == 0
    assert worker_rc == 0
    assert trace_path.exists()
    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "worker trace JSONL must not be empty"
    assert all(isinstance(json.loads(line), dict) for line in lines)


def test_cli_goal_runs_approved_process_for_expected_artifact(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    artifact_root = tmp_path / "goal-process-artifacts"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "executor", "tools": ["process.run"]}),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="process-call",
                        name="process.run",
                        arguments={
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "Path('process.txt').write_text('ok', "
                                    "encoding='utf-8'); print('done')"
                                ),
                            ],
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Run approved process artifact",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "process.run",
            "--approved-tool",
            "process.run",
            "--allow-process-command",
            sys.executable,
            "--allow-process-argv-prefix",
            f"{sys.executable} -c",
            "--expected-artifact",
            "process.txt",
            "--artifact-root",
            str(artifact_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "completed", payload
    assert payload["approved_tools"] == ["process.run"]
    assert payload["allowed_process_commands"] == [sys.executable]
    assert payload["allowed_process_argv_prefixes"] == [[sys.executable, "-c"]]
    assert (artifact_root / "process.txt").read_text(encoding="utf-8") == "ok"


def test_cli_worker_goal_once_runs_approved_process_for_expected_artifact(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    store_path = tmp_path / "goal-process.sqlite"
    artifact_root = tmp_path / "worker-process-artifacts"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "executor", "tools": ["process.run"]}),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="worker-process-call",
                        name="process.run",
                        arguments={
                            "argv": [
                                sys.executable,
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "Path('worker.txt').write_text('ok', "
                                    "encoding='utf-8'); print('done')"
                                ),
                            ],
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    create_rc = cli.main(
        [
            "goal",
            "Queue approved process artifact",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "process.run",
            "--expected-artifact",
            "worker.txt",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)

    worker_rc = cli.main(
        [
            "worker",
            "goal-once",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--session-id",
            created["session_id"],
            "--approved-tool",
            "process.run",
            "--allow-process-command",
            sys.executable,
            "--allow-process-argv-prefix",
            f"{sys.executable} -c",
            "--artifact-root",
            str(artifact_root),
        ]
    )
    worker = json.loads(capsys.readouterr().out)

    assert create_rc == 0
    assert worker_rc == 0
    assert worker["status"] == "completed", worker
    assert (artifact_root / "worker.txt").read_text(encoding="utf-8") == "ok"


def test_cli_goal_rejects_required_tool_not_available(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_create_provider",
        lambda _name: (_ for _ in ()).throw(AssertionError("runtime should not start")),
    )

    rc = cli.main(
        [
            "goal",
            "Reject missing required tool",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "search.web",
            "--required-tool",
            "image.generate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "required_tool_not_available"
    assert payload["missing_required_tools"] == ["image.generate"]
    assert payload["tools"] == ["search.web"]
    assert payload["required_tools"] == ["image.generate"]


def test_cli_goal_supports_model_planner_with_mock_backend(capsys, monkeypatch) -> None:
    provider = MockProvider(
        scripted=[
            ScriptedTurn(
                content="""
                {
                  "name": "goal-model-plan",
                  "tasks": [
                    {
                      "key": "write",
                      "role": "writer",
                      "description": "write note",
                      "tools": ["artifact.write_text"]
                    }
                  ]
                }
                """
            ),
            ScriptedTurn(content='{"note": "done"}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Write a model-planned note",
            "--provider",
            "mock",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "completed"
    assert payload["workflow"] == "goal-model-plan"
    assert payload["plan_tasks"] == ["write"]


def test_runtime_goal_auto_planner_uses_model_for_non_mock_backend() -> None:
    provider = AutoModelPlannerProvider()

    session = asyncio.run(
        runtime_module.run_goal(
            GoalSpec(
                objective="Write with auto planner",
                available_tools=(),
            ),
            provider=provider,
            env_file=None,
        )
    )

    assert session.status is SessionStatus.COMPLETED
    assert session.workflow_name == "auto-model-plan"
    assert list(session.nodes) == ["write"]
    assert provider.invocation_roles == ["planner", "writer"]


def test_runtime_goal_auto_planner_raises_when_model_plan_invalid() -> None:
    # S96: there is no rule-based fallback planner anymore -- an invalid agent
    # plan that cannot be repaired surfaces the error instead of silently
    # substituting a deterministic plan (ADR-0008).
    provider = AutoModelPlannerProvider(planner_content='{"rationale": "no tasks"}')

    with pytest.raises(ValueError, match="non-empty tasks list"):
        asyncio.run(
            runtime_module.run_goal(
                GoalSpec(
                    objective="Write with agent planner",
                    available_tools=(),
                ),
                provider=provider,
                env_file=None,
            )
        )


def test_runtime_goal_auto_planner_retries_transient_model_failure() -> None:
    provider = AutoModelPlannerProvider(planner_failures=1)

    session = asyncio.run(
        runtime_module.run_goal(
            GoalSpec(
                objective="Write after transient planner failure",
                available_tools=(),
            ),
            provider=provider,
            env_file=None,
        )
    )

    assert session.status is SessionStatus.COMPLETED
    assert session.workflow_name == "auto-model-plan"
    assert session.metadata["execution_plan"]["metadata"]["planner"] == "model"
    diagnostics = session.metadata["execution_plan"]["metadata"][
        "planner_diagnostics"
    ]
    assert diagnostics["invoke_attempts"] == 2
    assert diagnostics["retry_count"] == 1
    assert provider.invocation_roles == ["planner", "planner", "writer"]


def test_runtime_goal_auto_planner_raises_after_retry_exhaustion() -> None:
    # S96: no fallback planner -- exhausting the agent planner's invoke retries
    # surfaces the transient error rather than degrading to a rule planner.
    provider = AutoModelPlannerProvider(planner_failures=2)

    with pytest.raises(TimeoutError, match="planner read operation timed out"):
        asyncio.run(
            runtime_module.run_goal(
                GoalSpec(
                    objective="Write after exhausted planner failure",
                    available_tools=(),
                ),
                provider=provider,
                env_file=None,
            )
        )


def test_runtime_goal_uses_verifier_feedback_gate_for_missing_artifact() -> None:
    provider = MockProvider(
        scripted=[
            _plan_turn(
                {
                    "key": "work",
                    "role": "writer",
                    "expected_artifacts": ["__s18_missing__/report.txt"],
                }
            ),
            ScriptedTurn(content='{"done": true}'),
            # Auto-repair triggers one agent revise that proposes no change.
            ScriptedTurn(content=json.dumps({"rationale": "no actionable change"})),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    session = asyncio.run(
        runtime_module.run_goal(
            GoalSpec(
                objective="Verify missing artifact",
                expected_artifacts=("__s18_missing__/report.txt",),
            ),
            provider=provider,
            env_file=None,
        )
    )

    assert session.status is SessionStatus.WAITING_GATE
    gate = session.nodes["work"].latest_gate()
    assert gate is not None
    assert gate.name == "verifier_feedback"


def test_cli_doctor_providers_with_mock_backend(capsys) -> None:
    rc = cli.main(
        [
            "doctor",
            "providers",
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--roles",
            "planner,executor",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [item["role"] for item in payload["providers"]] == ["planner", "executor"]
    assert all(item["ok"] for item in payload["providers"])


def test_cli_doctor_providers_timeout_is_bounded(capsys, monkeypatch) -> None:
    class SlowProvider:
        name = "slow"

        async def complete(self, *args, **kwargs):
            await asyncio.sleep(5)

        def resolve_model(self, hint):
            return "slow"

        def context_limit(self) -> int:
            return 1

        async def close(self) -> None:
            return

    monkeypatch.setattr(cli, "create_provider", lambda _name: SlowProvider())

    rc = cli.main(
        [
            "doctor",
            "providers",
            "--provider",
            "slow",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--timeout-seconds",
            "0.01",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["providers"] == [
        {
            "role": "planner",
            "ok": False,
            "provider": "slow",
            "error_type": "TimeoutError",
            "error": "",
        }
    ]


def test_cli_doctor_tools_dry_run_executes_named_tool(tmp_path, capsys) -> None:
    rc = cli.main(
        [
            "doctor",
            "tools",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path),
            "--tool",
            (
                "search.web,artifact.write_json,artifact.read_json,"
                "artifact.write_text,artifact.read_text,artifact.exists,artifact.list,time.now"
            ),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["stats"]["total_tools"] >= 9
    assert payload["health"]["unhealthy_tools"] == 0
    assert [item["tool"] for item in payload["executions"]] == [
        "search.web",
        "artifact.write_json",
        "artifact.read_json",
        "artifact.write_text",
        "artifact.read_text",
        "artifact.exists",
        "artifact.list",
        "time.now",
    ]
    assert (tmp_path / "doctor.json").exists()
    assert (tmp_path / "doctor.txt").exists()


def test_cli_doctor_tools_live_mode_preflights_required_env(capsys, monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)

    rc = cli.main(
        [
            "doctor",
            "tools",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--live-tools",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["executions"] == []
    assert payload["missing_env"] == ["BRAVE_SEARCH_API_KEY", "DOUBAO_API_KEY"]
    assert payload["env_requirements"] == {
        "image.generate": ["DOUBAO_API_KEY"],
        "search.web": ["BRAVE_SEARCH_API_KEY"],
    }


def test_cli_doctor_tools_live_mode_preflights_selected_tool_env(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)

    rc = cli.main(
        [
            "doctor",
            "tools",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path),
            "--live-tools",
            "--tool",
            "artifact.write_text",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["missing_env"] == []
    assert payload["env_requirements"] == {}
    assert payload["executions"][0]["ok"] is True


def test_cli_doctor_tools_redacts_live_image_media_urls(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("DOUBAO_API_KEY", "doubao-test")

    def fake_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        return {
            "data": [
                {
                    "url": "https://signed.example/image.png?token=temporary",
                    "b64_json": "base64-image",
                }
            ]
        }

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)

    rc = cli.main(
        [
            "doctor",
            "tools",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path),
            "--live-tools",
            "--tool",
            "image.generate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    content = json.loads(payload["executions"][0]["content"])
    rendered = json.dumps(payload)
    assert rc == 0
    assert content["result"]["data"][0]["url"] == "<redacted>"
    assert content["result"]["data"][0]["b64_json"] == "<redacted>"
    assert "signed.example" not in rendered
    assert "base64-image" not in rendered
    assert "doubao-test" not in rendered


def test_cli_doctor_tool_loop_mock_backend_records_trace(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"

    rc = cli.main(
        [
            "doctor",
            "tool-loop",
            "--provider",
            "mock",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--trace-path",
            str(trace_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["reason"] == "passed"
    assert payload["tool"] == "time.now"
    assert payload["model_invoke_completed"] == 2
    assert payload["tool_call_started"] == 1
    assert payload["tool_call_completed"] == 1
    assert trace_path.exists()
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    completed = next(event for event in events if event["kind"] == "tool_call_completed")
    assert completed["detail"]["content_json"] is True
    assert "content_preview" in completed["detail"]


def test_cli_doctor_tool_loop_fails_when_provider_skips_tool_call(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli,
        "create_provider",
        lambda _name: MockProvider(scripted=[ScriptedTurn(content='{"direct": true}')]),
    )

    rc = cli.main(
        [
            "doctor",
            "tool-loop",
            "--provider",
            "no-tool",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--trace-path",
            str(tmp_path / "trace.jsonl"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "no_tool_call"
    assert payload["session_status"] == "completed"
    assert payload["tool_call_started"] == 0


def test_cli_doctor_goal_scenario_validates_required_tools_from_ledger(
    tmp_path, capsys, monkeypatch
) -> None:
    trace_path = tmp_path / "goal-trace.jsonl"
    provider = MockProvider(
        scripted=[
            _plan_turn(
                {
                    "key": "work",
                    "role": "executor",
                    "tools": ["search.web", "image.generate"],
                }
            ),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call-search",
                        name="search.web",
                        arguments={"query": "HydraMind MAS", "count": 1},
                    ),
                    ToolCall(
                        id="call-image",
                        name="image.generate",
                        arguments={"prompt": "blue square"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"scenario_complete": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "doctor",
            "goal-scenario",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--trace-path",
            str(trace_path),
            "--tool",
            "search.web,image.generate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    required = {item["tool"]: item for item in payload["required_tools"]}
    assert rc == 0
    assert payload["ok"] is True
    assert payload["reason"] == "passed"
    assert payload["session_status"] == "completed"
    assert required["search.web"]["succeeded"] is True
    assert required["image.generate"]["succeeded"] is True
    assert {item["tool_name"] for item in payload["tool_executions"]} == {
        "search.web",
        "image.generate",
    }
    assert payload["tool_call_completed"] == 2
    assert trace_path.exists()


def test_cli_doctor_goal_scenario_fails_when_required_tool_missing(
    tmp_path, capsys, monkeypatch
) -> None:
    trace_path = tmp_path / "goal-trace.jsonl"
    provider = MockProvider(
        scripted=[
            _plan_turn(
                {
                    "key": "work",
                    "role": "executor",
                    "tools": ["search.web", "image.generate"],
                }
            ),
            ScriptedTurn(content='{"direct": true}'),
            # Auto-repair triggers one agent revise that proposes no change, so
            # the required tool is never exercised and the run halts at the gate.
            ScriptedTurn(content=json.dumps({"rationale": "no actionable change"})),
            ScriptedTurn(content='{"direct": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "doctor",
            "goal-scenario",
            "--provider",
            "scripted",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--trace-path",
            str(trace_path),
            "--tool",
            "search.web,image.generate",
            "--required-tool",
            "image.generate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    required = {item["tool"]: item for item in payload["required_tools"]}
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "missing_required_tool"
    assert payload["session_status"] == "waiting_gate"
    assert required["image.generate"]["started"] is False
    assert payload["tool_executions"] == []
    assert trace_path.exists()


def test_cli_doctor_env_reports_presence_without_values(tmp_path, capsys, monkeypatch) -> None:
    for key in (
        "DEEPSEEK_API_KEY",
        "KIMI_API_KEY",
        "GLM_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "DOUBAO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=deepseek-test",
                "KIMI_API_KEY=kimi-test",
                "GLM_API_KEY=glm-test",
            ]
        ),
        encoding="utf-8",
    )

    rc = cli.main(
        [
            "doctor",
            "env",
            "--env-file",
            str(env_file),
            "--include-missing-template",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert rc == 1
    assert payload["profiles"]["providers"]["ok"] is True
    assert payload["profiles"]["tools"]["ok"] is False
    assert payload["missing_template"] == [
        "BRAVE_SEARCH_API_KEY=",
        "DOUBAO_API_KEY=",
    ]
    assert "deepseek-test" not in rendered
    assert "kimi-test" not in rendered
    assert "glm-test" not in rendered


def test_cli_doctor_env_profile_tools_passes_when_keys_are_in_process_env(
    capsys, monkeypatch
) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
    monkeypatch.setenv("DOUBAO_API_KEY", "doubao-test")

    rc = cli.main(
        [
            "doctor",
            "env",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--profile",
            "tools",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["profiles"]["tools"]["keys"] == [
        {"key": "BRAVE_SEARCH_API_KEY", "present": True},
        {"key": "DOUBAO_API_KEY", "present": True},
    ]
    assert "brave-test" not in rendered
    assert "doubao-test" not in rendered


def test_cli_goal_enqueue_only_persists_quality_contract(
    tmp_path, capsys, monkeypatch
) -> None:
    store_path = tmp_path / "goal-quality.sqlite"
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "min_length": 50,
                "required_sections": ["# Intro"],
            }
        ),
        encoding="utf-8",
    )
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]})
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Queue goal with quality contract",
            "--provider",
            "scripted",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--expected-artifact",
            "reports/delivery.md",
            "--quality-contract",
            str(contract_path),
            "--session-store",
            "sqlite",
            "--store-path",
            str(store_path),
            "--enqueue-only",
        ]
    )
    created = json.loads(capsys.readouterr().out)
    stored = asyncio.run(SqliteSessionStore(store_path).get(created["session_id"]))

    assert rc == 0
    assert created["queued_only"] is True
    assert created["quality_contract"]["min_length"] == 50
    assert created["quality_contract"]["required_sections"] == ["# Intro"]
    assert stored is not None
    plan_goal = stored.metadata["execution_plan"]["goal"]
    assert plan_goal["quality_contract"]["min_length"] == 50
    assert plan_goal["quality_contract"]["required_sections"] == ["# Intro"]
    last_task_contract = stored.metadata["execution_plan"]["tasks"][-1]["contract"]
    assert last_task_contract["quality_contract"]["min_length"] == 50
    assert last_task_contract["quality_contract"]["required_sections"] == ["# Intro"]


def test_cli_goal_rejects_invalid_quality_contract_path(tmp_path, capsys) -> None:
    missing_contract = tmp_path / "missing-contract.json"

    rc = cli.main(
        [
            "goal",
            "Reject missing quality contract",
            "--provider",
            "mock",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--quality-contract",
            str(missing_contract),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "quality_contract_invalid"
    assert "not found" in payload["error"]


def test_cli_goal_rejects_malformed_quality_contract(tmp_path, capsys) -> None:
    contract_path = tmp_path / "bad.json"
    contract_path.write_text(json.dumps({"min_length": "ten"}), encoding="utf-8")

    rc = cli.main(
        [
            "goal",
            "Reject malformed quality contract",
            "--provider",
            "mock",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--quality-contract",
            str(contract_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "quality_contract_invalid"
    assert "validation failed" in payload["error"]


def test_cli_goal_trace_path_writes_jsonl_observation_events(
    tmp_path, capsys, monkeypatch
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    provider = MockProvider(
        scripted=[
            _plan_turn({"key": "work", "role": "writer", "tools": ["artifact.write_text"]}),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    monkeypatch.setattr(runtime_module, "_create_provider", lambda _name: provider)

    rc = cli.main(
        [
            "goal",
            "Trace path smoke",
            "--provider",
            "scripted",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--trace-path",
            str(trace_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "completed", payload
    assert trace_path.exists()
    raw = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert raw, "trace JSONL must not be empty"
    parsed = [json.loads(line) for line in raw]
    # Every line is a valid JSON object representing an ObservationEvent.
    assert all(isinstance(item, dict) and "kind" in item for item in parsed)


def test_cli_goal_quality_contract_requires_expected_artifact(
    tmp_path, capsys
) -> None:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps({"min_length": 10}), encoding="utf-8"
    )

    rc = cli.main(
        [
            "goal",
            "Quality contract without expected artifact",
            "--provider",
            "mock",
            "--planner",
            "model",
            "--env-file",
            "/tmp/hydramind-missing-env",
            "--tool",
            "artifact.write_text",
            "--quality-contract",
            str(contract_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "quality_contract_requires_expected_artifact"
    assert "expected-artifact" in payload["message"]
    assert payload["quality_contract_path"] == str(contract_path)


def test_cli_goal_semantic_verifier_flag_is_removed() -> None:
    # S97: the agent semantic verifier is the default verify-good-enough
    # decision-maker; the opt-in --enable-semantic-verifier flag no longer
    # exists. Passing it must be an argparse error (exit code 2).
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "goal",
                "Goal with removed flag",
                "--enable-semantic-verifier",
            ]
        )
    assert excinfo.value.code == 2
