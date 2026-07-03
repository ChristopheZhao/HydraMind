"""Worker operator CLI handlers for queue health/readiness/recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from hydramind.cli_queue import (
    build_cli_queue_adapter,
    queue_config_error,
    queue_messages_payload,
)
from hydramind.runtime import (
    queue_dead_letters,
    queue_health,
    replay_queue_dead_letters,
    worker_readiness,
)
from hydramind.runtime_worker import WorkerHealthSnapshot, WorkerReadinessSnapshot


def handle_worker_operator(args: argparse.Namespace) -> int | None:
    if args.worker_command == "health":
        source_error = _worker_health_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        health_result = asyncio.run(_run_worker_health(args))
        print(health_result.model_dump_json())
        return 0
    if args.worker_command == "readiness":
        source_error = _worker_readiness_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        store_error = _worker_readiness_store_error(args)
        if store_error is not None:
            print(json.dumps(store_error, ensure_ascii=False, default=str))
            return 1
        readiness_result = asyncio.run(_run_worker_readiness(args))
        print(readiness_result.model_dump_json())
        return 0 if readiness_result.ready else 1
    if args.worker_command == "dead-letters":
        source_error = _worker_dead_letters_source_error(args)
        if source_error is not None:
            print(json.dumps(source_error, ensure_ascii=False, default=str))
            return 1
        limit_error = _worker_dead_letters_limit_error(args)
        if limit_error is not None:
            print(json.dumps(limit_error, ensure_ascii=False, default=str))
            return 1
        queue_error = queue_config_error(args)
        if queue_error is not None:
            print(json.dumps(queue_error, ensure_ascii=False, default=str))
            return 1
        dead_letters_result = asyncio.run(_run_worker_dead_letters(args))
        print(json.dumps(dead_letters_result, ensure_ascii=False, default=str))
        return 0
    return None


async def _run_worker_health(args: argparse.Namespace) -> WorkerHealthSnapshot:
    queue = build_cli_queue_adapter(args)
    assert queue is not None
    try:
        return await queue_health(
            queue_adapter=queue,
            worker_id=getattr(args, "worker_id", "queue-health"),
        )
    finally:
        await queue.close()


async def _run_worker_readiness(
    args: argparse.Namespace,
) -> WorkerReadinessSnapshot:
    queue = build_cli_queue_adapter(args)
    assert queue is not None
    try:
        return worker_readiness(
            queue_adapter=queue,
            worker_id=getattr(args, "worker_id", "worker-readiness"),
            session_store_kind=args.session_store,
            store_path=args.store_path,
        )
    finally:
        await queue.close()


async def _run_worker_dead_letters(args: argparse.Namespace) -> dict[str, Any]:
    queue = build_cli_queue_adapter(args)
    assert queue is not None
    try:
        command = getattr(args, "dead_letters_command", "")
        limit = getattr(args, "limit", None)
        if command == "list":
            messages = await queue_dead_letters(
                queue_adapter=queue,
                limit=limit,
            )
            return {
                "queue_name": queue.name,
                "command": "list",
                "limit": limit,
                "count": len(messages),
                "dead_letters": queue_messages_payload(messages),
            }
        if command == "replay":
            if not isinstance(limit, int):
                raise RuntimeError("dead-letters replay limit must be validated")
            replayed = await replay_queue_dead_letters(
                queue_adapter=queue,
                limit=limit,
                reset_attempt=not bool(getattr(args, "preserve_attempt", False)),
                remove_from_dead_letters=not bool(
                    getattr(args, "retain_dead_letter", False)
                ),
                metadata=_parse_metadata(getattr(args, "metadata", [])),
            )
            return {
                "queue_name": queue.name,
                "command": "replay",
                "limit": limit,
                "count": len(replayed),
                "reset_attempt": not bool(getattr(args, "preserve_attempt", False)),
                "removed_from_dead_letters": not bool(
                    getattr(args, "retain_dead_letter", False)
                ),
                "replayed": queue_messages_payload(replayed),
            }
        raise RuntimeError(f"unknown dead-letters command {command!r}")
    finally:
        await queue.close()


def _parse_metadata(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--metadata expects KEY=VALUE, got {item!r}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit("--metadata key must not be empty")
        try:
            parsed[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed[key] = raw_value
    return parsed


def _worker_health_source_error(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "queue", None) is None:
        return {
            "ok": False,
            "reason": "worker_health_requires_queue",
            "message": "worker health requires --queue redis",
            "worker_command": args.worker_command,
        }
    return None


def _worker_readiness_source_error(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "queue", None) is None:
        return {
            "ok": False,
            "reason": "worker_readiness_requires_queue",
            "message": "worker readiness requires --queue redis",
            "worker_command": args.worker_command,
        }
    return None


def _worker_readiness_store_error(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.session_store == "sqlite" and getattr(args, "store_path", None) is None:
        return {
            "ok": False,
            "reason": "worker_readiness_sqlite_requires_store_path",
            "message": "worker readiness with --session-store sqlite requires --store-path",
            "worker_command": args.worker_command,
            "session_store": args.session_store,
        }
    return None


def _worker_dead_letters_source_error(
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if getattr(args, "queue", None) is None:
        return {
            "ok": False,
            "reason": "worker_dead_letters_requires_queue",
            "message": "worker dead-letters commands require --queue redis",
            "worker_command": args.worker_command,
            "dead_letters_command": getattr(args, "dead_letters_command", None),
        }
    return None


def _worker_dead_letters_limit_error(
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    command = getattr(args, "dead_letters_command", None)
    limit = getattr(args, "limit", None)
    if command == "replay" and limit is None:
        return {
            "ok": False,
            "reason": "worker_dead_letters_replay_requires_limit",
            "message": "worker dead-letters replay requires --limit",
            "worker_command": args.worker_command,
            "dead_letters_command": command,
        }
    if limit is not None and limit <= 0:
        return {
            "ok": False,
            "reason": "worker_dead_letters_invalid_limit",
            "field": "limit",
            "value": limit,
            "message": "limit must be positive",
            "worker_command": args.worker_command,
            "dead_letters_command": command,
        }
    return None
