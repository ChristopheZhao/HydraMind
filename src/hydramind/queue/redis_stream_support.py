"""Internal Redis Streams queue message codecs and response normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from hydramind.queue.base import QueueMessage

_HANDLE_SEPARATOR = "|"
_REDIS_DELIVERY_METADATA_KEYS = frozenset(
    {
        "redis_stream",
        "redis_stream_id",
        "redis_consumer_group",
        "redis_consumer",
    }
)


def _message_fields(message: QueueMessage) -> dict[str, str]:
    return {
        "session_id": message.session_id,
        "attempt": str(message.attempt),
        "metadata": json.dumps(message.metadata, sort_keys=True),
    }


def _next_delivery_metadata(
    message: QueueMessage,
    *,
    reason: str,
    source_handle: str,
    next_attempt: int,
) -> dict[str, Any]:
    metadata = dict(message.metadata)
    metadata.update(
        {
            "last_delivery_reason": reason,
            "last_delivery_handle": source_handle,
            "last_delivery_attempt": message.attempt,
            "delivery_attempt": next_attempt,
        }
    )
    return metadata


def _replay_metadata(
    message: QueueMessage,
    *,
    dead_letter_stream_key: str,
    reset_attempt: bool,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    replay_metadata = {
        key: value
        for key, value in message.metadata.items()
        if key not in _REDIS_DELIVERY_METADATA_KEYS
    }
    replay_metadata.update(metadata or {})
    replay_metadata.update(
        {
            "replay_source": "dead_letter",
            "replay_dead_letter_stream": dead_letter_stream_key,
            "replay_dead_letter_handle": message.handle,
            "replay_original_attempt": message.attempt,
            "replay_attempt_reset": reset_attempt,
            "replay_count": _next_replay_count(replay_metadata.get("replay_count")),
        }
    )
    return replay_metadata


def _next_replay_count(value: Any) -> int:
    if isinstance(value, int):
        return value + 1
    return 1


def _message_from_fields(
    entry_id: str,
    fields: Mapping[Any, Any],
) -> QueueMessage:
    normalized = {_to_text(key): _to_text(value) for key, value in fields.items()}
    metadata = _loads_metadata(normalized.get("metadata", "{}"))
    return QueueMessage(
        session_id=normalized.get("session_id", ""),
        attempt=int(normalized.get("attempt", "0") or "0"),
        metadata=metadata,
        handle=entry_id,
    )


def _loads_metadata(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _validate_positive_limit(limit: int | None) -> None:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")


def _first_stream_entry(response: Any) -> tuple[str, Mapping[Any, Any]] | None:
    streams = _stream_response(response)
    for _stream_name, entries in streams:
        normalized_entries = _stream_entries(entries)
        if normalized_entries:
            return normalized_entries[0]
    return None


def _claimed_entries(response: Any) -> list[tuple[str, Mapping[Any, Any]]]:
    if isinstance(response, (tuple, list)):
        if len(response) >= 2:
            return _stream_entries(response[1])
        return []
    return _stream_entries(response)


def _stream_response(response: Any) -> list[tuple[str, Any]]:
    if not isinstance(response, list):
        return []
    streams: list[tuple[str, Any]] = []
    for item in response:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            streams.append((_to_text(item[0]), item[1]))
    return streams


def _stream_entries(entries: Any) -> list[tuple[str, Mapping[Any, Any]]]:
    if not isinstance(entries, list):
        return []
    normalized: list[tuple[str, Mapping[Any, Any]]] = []
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) < 2:
            continue
        entry_id = _to_text(entry[0])
        fields = entry[1]
        if isinstance(fields, Mapping):
            normalized.append((entry_id, fields))
    return normalized


def _split_handle(handle: str) -> tuple[str, str]:
    if _HANDLE_SEPARATOR not in handle:
        return handle, ""
    entry_id, token = handle.rsplit(_HANDLE_SEPARATOR, 1)
    return entry_id, token


def _join_handle(entry_id: str, token: str) -> str:
    return f"{entry_id}{_HANDLE_SEPARATOR}{token}"


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
