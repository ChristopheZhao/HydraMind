#!/usr/bin/env python3
"""Run live Redis acceptance checks for RedisStreamQueueAdapter.

The script starts an isolated local redis-server process and exercises the
real redis.asyncio client path. It is intentionally opt-in so the default unit
suite stays service-free.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hydramind.control import (
    AttemptStatus,
    ControlPlane,
    InMemorySessionStore,
    SessionService,
    SessionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.orchestration import OrchestratorAgent
from hydramind.queue import QueueMessage, RedisStreamQueueAdapter
from hydramind.runtime_worker import QueueExecutionHost, WorkerDeliveryAction
from hydramind.testing import MockProvider, ScriptedTurn


class AcceptanceError(RuntimeError):
    """Raised when live acceptance cannot prove the expected behavior."""


@dataclass
class RedisServer:
    url: str
    process: subprocess.Popen[str]
    tempdir: tempfile.TemporaryDirectory[str]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        asyncio.run(_run(args))
    except AcceptanceError as exc:
        print(f"fail: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


async def _run(args: argparse.Namespace) -> None:
    _require_redis_asyncio()
    redis_server = _resolve_redis_server(args.redis_server)
    run_id = uuid.uuid4().hex[:12]
    server = await _start_redis_server(
        redis_server=redis_server,
        startup_timeout_seconds=args.startup_timeout_seconds,
        port=args.port,
    )
    results: list[str] = []
    try:
        await _cross_client_visibility_acceptance(server.url, run_id)
        results.append("cross-client visibility reclaim")
        await _dead_letter_replay_acceptance(server.url, run_id)
        results.append("dead-letter replay")
        await _worker_lease_recovery_acceptance(server.url, run_id)
        results.append("worker lease recovery")
    finally:
        await _stop_redis_server(server)

    if not args.quiet:
        print("HydraMind live Redis acceptance")
        print(f"redis_server: {redis_server}")
        print("scope: localhost isolated redis-server process")
        for name in results:
            print(f"pass: {name}")


async def _cross_client_visibility_acceptance(url: str, run_id: str) -> None:
    stream = f"hydramind:acceptance:{run_id}:visibility"
    group = f"{stream}:workers"
    queue_a = RedisStreamQueueAdapter(
        url=url,
        stream_key=stream,
        group_name=group,
        consumer_name="worker-a",
        visibility_timeout_seconds=0.05,
    )
    queue_b = RedisStreamQueueAdapter(
        url=url,
        stream_key=stream,
        group_name=group,
        consumer_name="worker-b",
        visibility_timeout_seconds=0.05,
    )
    try:
        await queue_a.enqueue("sess-live-visibility", metadata={"case": "visibility"})
        first = await _require_message(
            queue_a.dequeue(timeout=1.0),
            "worker-a did not receive first Redis delivery",
        )
        _ensure(first.attempt == 0, "first delivery attempt should be zero")

        await asyncio.sleep(0.08)
        second = await _require_message(
            queue_b.dequeue(timeout=1.0),
            "worker-b did not reclaim expired Redis delivery",
        )

        _ensure(second.session_id == first.session_id, "reclaimed session id changed")
        _ensure(second.attempt == 1, "reclaimed delivery did not increment attempt")
        _ensure(second.handle != first.handle, "reclaimed delivery reused stale handle")
        _ensure(
            second.metadata.get("last_delivery_reason") == "visibility_timeout",
            "reclaimed delivery missing visibility timeout evidence",
        )

        await queue_a.ack(first)
        _ensure(await queue_b.in_flight() == 1, "stale ack dropped reclaimed delivery")

        await queue_b.ack(second)
        _ensure(await queue_b.pending() == 0, "acked Redis stream still has pending work")
        _ensure(await queue_b.in_flight() == 0, "acked Redis stream still has in-flight work")
    finally:
        await queue_a.close()
        await queue_b.close()


async def _dead_letter_replay_acceptance(url: str, run_id: str) -> None:
    stream = f"hydramind:acceptance:{run_id}:dead-letter"
    queue = RedisStreamQueueAdapter(
        url=url,
        stream_key=stream,
        group_name=f"{stream}:workers",
        consumer_name="worker-dlq",
        visibility_timeout_seconds=0.05,
        max_delivery_attempts=1,
    )
    try:
        await queue.enqueue("sess-live-poison", metadata={"case": "dlq"})
        delivery = await _require_message(
            queue.dequeue(timeout=1.0),
            "poison session was not delivered before nack",
        )
        await queue.nack(delivery, retry=True)

        dead_letters = await queue.dead_letters(limit=1)
        _ensure(len(dead_letters) == 1, "dead-letter stream did not receive overflow")
        dead = dead_letters[0]
        _ensure(dead.session_id == "sess-live-poison", "dead-letter session id changed")
        _ensure(
            dead.metadata.get("dead_letter_reason") == "max_delivery_attempts_exceeded",
            "dead-letter entry missing max-attempt evidence",
        )

        replayed = await queue.replay_dead_letter(
            dead,
            metadata={"operator": "live-acceptance"},
        )
        _ensure(replayed.attempt == 0, "replay did not reset queue attempt")
        _ensure(
            replayed.metadata.get("replay_dead_letter_handle") == dead.handle,
            "replay missing source dead-letter handle",
        )
        _ensure(
            "redis_stream_id" not in replayed.metadata,
            "replay leaked stale Redis delivery metadata",
        )
        _ensure(await queue.dead_letters() == (), "replay did not remove dead-letter entry")

        fresh = await _require_message(
            queue.dequeue(timeout=1.0),
            "replayed session was not delivered from Redis",
        )
        _ensure(fresh.session_id == dead.session_id, "fresh replay delivery changed session id")
        _ensure(fresh.handle != replayed.handle, "fresh replay delivery lacks new tokenized handle")
        _ensure(
            fresh.metadata.get("replay_dead_letter_handle") == dead.handle,
            "fresh replay delivery lost replay metadata",
        )
        _ensure(
            fresh.metadata.get("redis_stream_id") == replayed.handle,
            "fresh replay delivery missing new Redis stream id",
        )
        await queue.ack(fresh)
        _ensure(await queue.pending() == 0, "replayed delivery remained pending after ack")
    finally:
        await queue.close()


async def _worker_lease_recovery_acceptance(url: str, run_id: str) -> None:
    stream = f"hydramind:acceptance:{run_id}:worker"
    group = f"{stream}:workers"
    queue_lost = RedisStreamQueueAdapter(
        url=url,
        stream_key=stream,
        group_name=group,
        consumer_name="worker-lost",
        visibility_timeout_seconds=0.05,
    )
    queue_recovery = RedisStreamQueueAdapter(
        url=url,
        stream_key=stream,
        group_name=group,
        consumer_name="worker-recovery",
        visibility_timeout_seconds=0.05,
    )
    try:
        service = SessionService(InMemorySessionStore())
        control = ControlPlane(service)
        agent = OrchestratorAgent(
            provider=MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')]),
            control=control,
            workflow=_workflow(),
        )
        session = await agent.start_session()
        execution = await control.open_node_execution(
            session.id,
            "plan",
            trace_id="live-redis-lost-worker",
        )
        await control.grant_node_execution_lease(
            session.id,
            "plan",
            execution.id,
            owner="worker-lost",
            ttl_seconds=1,
        )
        await queue_lost.enqueue(session.id)
        lost_delivery = await _require_message(
            queue_lost.dequeue(timeout=1.0),
            "lost worker did not receive queued session",
        )

        await asyncio.sleep(1.1)
        result = await QueueExecutionHost(
            queue=queue_recovery,
            orchestrator=agent,
            worker_id="worker-recovery",
            lease_ttl_seconds=60,
        ).run_once(timeout=1.0)

        _ensure(result.status == "completed", f"recovery worker status was {result.status!r}")
        _ensure(result.delivery_action is WorkerDeliveryAction.ACK, "recovery worker did not ack")
        _ensure(result.queue_attempt == 1, "recovered Redis delivery did not increment attempt")
        _ensure(
            result.message_handle != lost_delivery.handle,
            "recovery worker reused lost delivery handle",
        )

        await queue_lost.ack(lost_delivery)
        _ensure(await queue_recovery.pending() == 0, "worker stream still has pending work")
        _ensure(await queue_recovery.in_flight() == 0, "worker stream still has in-flight work")

        refreshed = await service.get_session(session.id)
        _ensure(refreshed.status is SessionStatus.COMPLETED, "session did not complete")
        attempts = refreshed.nodes["plan"].attempts
        _ensure(len(attempts) == 2, "lease recovery did not preserve two attempts")
        _ensure(
            attempts[0].status is AttemptStatus.ABORTED,
            "lost execution was not aborted",
        )
        _ensure(
            attempts[0].error == "execution lease expired",
            "lost execution missing lease-expired evidence",
        )
        _ensure(
            attempts[1].status is AttemptStatus.SUCCEEDED,
            "recovery execution did not succeed",
        )
    finally:
        await queue_lost.close()
        await queue_recovery.close()


def _workflow() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="live-redis-worker",
        nodes=(WorkflowNodeSpec(key="plan", role="planner"),),
    )


async def _require_message(
    awaitable: Any,
    message: str,
) -> QueueMessage:
    value = await awaitable
    if value is None:
        raise AcceptanceError(message)
    return value


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


async def _start_redis_server(
    *,
    redis_server: Path,
    startup_timeout_seconds: float,
    port: int | None,
) -> RedisServer:
    tempdir = tempfile.TemporaryDirectory(prefix="hydramind-redis-live-")
    selected_port = port or _free_port()
    command = [
        str(redis_server),
        "--bind",
        "127.0.0.1",
        "--port",
        str(selected_port),
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        tempdir.name,
        "--dbfilename",
        "hydramind-acceptance.rdb",
        "--protected-mode",
        "yes",
        "--loglevel",
        "warning",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"redis://127.0.0.1:{selected_port}/0"
    try:
        await _wait_for_redis(url, process, startup_timeout_seconds)
    except Exception:
        await _stop_process(process)
        tempdir.cleanup()
        raise
    return RedisServer(url=url, process=process, tempdir=tempdir)


async def _wait_for_redis(
    url: str,
    process: subprocess.Popen[str],
    timeout_seconds: float,
) -> None:
    redis_asyncio = _require_redis_asyncio()
    client = redis_asyncio.Redis.from_url(url)
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                raise AcceptanceError(
                    "redis-server exited before accepting connections: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            try:
                await client.ping()
                await client.flushdb()
                return
            except Exception:
                await asyncio.sleep(0.05)
    finally:
        await _close_redis_client(client)
    raise AcceptanceError("redis-server did not accept connections before timeout")


async def _stop_redis_server(server: RedisServer) -> None:
    redis_asyncio = _require_redis_asyncio()
    client = redis_asyncio.Redis.from_url(server.url)
    try:
        with contextlib.suppress(Exception):
            await client.shutdown(nosave=True)
    finally:
        await _close_redis_client(client)
    await _stop_process(server.process)
    server.tempdir.cleanup()


async def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait, timeout=5)


async def _close_redis_client(client: Any) -> None:
    aclose = getattr(client, "aclose", None)
    if callable(aclose):
        await aclose()
        return
    close = getattr(client, "close", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result


def _require_redis_asyncio() -> Any:
    try:
        import redis.asyncio as redis_asyncio  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AcceptanceError(
            "missing redis dependency; run with "
            "`uv run --extra redis python scripts/redis_live_acceptance.py`"
        ) from exc
    return redis_asyncio


def _resolve_redis_server(command: str) -> Path:
    resolved = shutil.which(command)
    if resolved is None:
        raise AcceptanceError(f"redis-server command not found: {command!r}")
    return Path(resolved)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live Redis acceptance for HydraMind queue/worker semantics."
    )
    parser.add_argument(
        "--redis-server",
        default="redis-server",
        help="redis-server executable name or path",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="localhost port to use; defaults to an ephemeral free port",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=5.0,
        help="time to wait for redis-server startup",
    )
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
