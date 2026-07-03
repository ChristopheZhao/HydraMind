"""Shared CLI queue adapter and payload helpers."""

from __future__ import annotations

import argparse
from typing import Any

from hydramind.queue import QueueAdapter, QueueMessage
from hydramind.runtime import create_queue_adapter

__all__ = [
    "build_cli_queue_adapter",
    "enqueue_queue_error",
    "queue_config_error",
    "queue_messages_payload",
    "queue_publish_payload",
]


def build_cli_queue_adapter(args: argparse.Namespace) -> QueueAdapter | None:
    queue_kind = getattr(args, "queue", None)
    if queue_kind is None:
        return None
    return create_queue_adapter(
        queue_kind,
        redis_url=getattr(args, "queue_redis_url", None),
        stream_key=getattr(args, "queue_stream_key", "hydramind:sessions"),
        group_name=getattr(args, "queue_group_name", "hydramind-workers"),
        consumer_name=getattr(args, "queue_consumer_name", "hydramind-worker"),
        visibility_timeout_seconds=getattr(
            args,
            "queue_visibility_timeout",
            60.0,
        ),
        max_delivery_attempts=getattr(args, "queue_max_delivery_attempts", None),
    )


def enqueue_queue_error(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "queue", None) is not None and not bool(args.enqueue_only):
        return {
            "ok": False,
            "reason": "queue_requires_enqueue_only",
            "message": "--queue is only valid with --enqueue-only for run/goal",
            "command": args.command,
        }
    return queue_config_error(args)


def queue_config_error(args: argparse.Namespace) -> dict[str, Any] | None:
    queue_kind = getattr(args, "queue", None)
    if queue_kind is None:
        return None
    if queue_kind == "redis" and not getattr(args, "queue_redis_url", None):
        return {
            "ok": False,
            "reason": "queue_redis_url_required",
            "message": "--queue redis requires --queue-redis-url",
            "queue": queue_kind,
        }
    return None


def queue_messages_payload(messages: tuple[QueueMessage, ...]) -> list[dict[str, Any]]:
    return [message.model_dump(mode="json") for message in messages]


def queue_publish_payload(
    args: argparse.Namespace,
    queue: QueueAdapter,
    message: QueueMessage,
) -> dict[str, Any]:
    return {
        "kind": getattr(args, "queue", None),
        "name": queue.name,
        "session_id": message.session_id,
        "message_handle": message.handle,
        "attempt": message.attempt,
        "metadata": dict(message.metadata),
    }
