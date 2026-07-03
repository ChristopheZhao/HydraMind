"""Redis Streams QueueAdapter with pollable delivery semantics."""

from __future__ import annotations

import importlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

from hydramind.queue.base import QueueCapabilities, QueueMessage
from hydramind.queue.redis_stream_support import (
    _claimed_entries,
    _first_stream_entry,
    _join_handle,
    _loads_metadata,
    _message_fields,
    _message_from_fields,
    _next_delivery_metadata,
    _replay_metadata,
    _split_handle,
    _stream_entries,
    _to_text,
    _validate_positive_limit,
)

_RETRY_REASON_NACK = "nack"
_RETRY_REASON_VISIBILITY_TIMEOUT = "visibility_timeout"
_DEAD_LETTER_REASON_MAX_ATTEMPTS = "max_delivery_attempts_exceeded"


class RedisStreamQueueAdapter:
    """Pollable queue adapter backed by Redis Streams consumer groups.

    The adapter uses Redis only for session-id delivery. Workflow state remains
    in the SessionStore and is never read by this layer.

    Delivery budget: ``max_delivery_attempts=N`` allows exactly N deliveries of
    a message. ``QueueMessage.attempt`` is the 0-indexed delivery ordinal
    (first delivery is attempt 0); when the failure of attempt ``N-1`` would
    require delivery ``N+1``, the message is dead-lettered instead, with
    ``dead_letter_attempt == N``.
    """

    name = "redis-stream"
    capabilities = QueueCapabilities.pollable_delivery()

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        url: str | None = None,
        stream_key: str = "hydramind:sessions",
        group_name: str = "hydramind-workers",
        consumer_name: str = "hydramind-worker",
        dead_letter_stream_key: str | None = None,
        visibility_timeout_seconds: float | None = 60.0,
        max_delivery_attempts: int | None = None,
    ) -> None:
        if redis_client is None and url is None:
            raise ValueError("redis_client or url is required")
        if visibility_timeout_seconds is not None and visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        if max_delivery_attempts is not None and max_delivery_attempts <= 0:
            raise ValueError("max_delivery_attempts must be positive")
        self._client = redis_client or _create_redis_client(url=cast(str, url))
        self._stream_key = stream_key
        self._group_name = group_name
        self._consumer_name = consumer_name
        self._dead_letter_stream_key = (
            dead_letter_stream_key or f"{stream_key}:dead"
        )
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._max_delivery_attempts = max_delivery_attempts
        self._delivery_state_hash_key = f"{stream_key}:delivery_state"
        self._group_ready = False
        self._closed = False

    async def enqueue(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> QueueMessage:
        self._reject_if_closed()
        await self._ensure_group()
        message = QueueMessage(session_id=session_id, metadata=metadata or {})
        entry_id = _to_text(
            await self._client.xadd(
                self._stream_key,
                _message_fields(message),
            )
        )
        return message.model_copy(update={"handle": entry_id})

    async def dequeue(
        self, *, timeout: float | None = None
    ) -> QueueMessage | None:
        self._reject_if_closed()
        await self._ensure_group()
        reclaimed = await self._reclaim_expired()
        if reclaimed is not None:
            return reclaimed

        response = await self._client.xreadgroup(
            self._group_name,
            self._consumer_name,
            streams={self._stream_key: ">"},
            count=1,
            block=_block_ms(timeout),
        )
        entry = _first_stream_entry(response)
        if entry is None:
            return None
        entry_id, fields = entry
        message = _message_from_fields(entry_id, fields)
        return await self._issue_delivery(entry_id, message)

    async def ack(self, message: QueueMessage) -> None:
        entry_id, token = _split_handle(message.handle)
        if not await self._delivery_token_matches(entry_id, token):
            return
        await self._ack_and_delete(entry_id)

    async def nack(
        self, message: QueueMessage, *, retry: bool = True
    ) -> None:
        entry_id, token = _split_handle(message.handle)
        if not await self._delivery_token_matches(entry_id, token):
            return
        await self._ack_and_delete(entry_id)
        if retry and not self._closed:
            await self._retry_or_dead_letter(
                message,
                reason=_RETRY_REASON_NACK,
                source_handle=message.handle,
            )

    async def pending(self) -> int:
        self._reject_if_closed()
        await self._ensure_group()
        total = int(await self._client.xlen(self._stream_key))
        in_flight = await self.in_flight()
        return max(0, total - in_flight)

    async def in_flight(self) -> int:
        self._reject_if_closed()
        await self._ensure_group()
        summary = await self._client.xpending(self._stream_key, self._group_name)
        if isinstance(summary, int):
            return summary
        if isinstance(summary, Mapping):
            pending = summary.get("pending", 0)
            return int(pending)
        if isinstance(summary, tuple) and summary:
            return int(summary[0])
        return 0

    async def dead_letters(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[QueueMessage, ...]:
        self._reject_if_closed()
        _validate_positive_limit(limit)
        entries = await self._client.xrange(
            self._dead_letter_stream_key,
            count=limit,
        )
        messages = [
            _message_from_fields(_to_text(entry_id), fields)
            for entry_id, fields in _stream_entries(entries)
        ]
        return tuple(messages)

    async def replay_dead_letter(
        self,
        message: QueueMessage,
        *,
        reset_attempt: bool = True,
        remove_from_dead_letters: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> QueueMessage:
        """Requeue one dead-lettered session id with replay evidence."""

        self._reject_if_closed()
        await self._ensure_group()
        if not message.handle:
            raise ValueError("dead-letter message handle is required for replay")
        attempt = 0 if reset_attempt else message.attempt
        replayed = QueueMessage(
            session_id=message.session_id,
            attempt=attempt,
            metadata=_replay_metadata(
                message,
                dead_letter_stream_key=self._dead_letter_stream_key,
                reset_attempt=reset_attempt,
                metadata=metadata,
            ),
        )
        entry_id = _to_text(
            await self._client.xadd(
                self._stream_key,
                _message_fields(replayed),
            )
        )
        if remove_from_dead_letters:
            await self._client.xdel(self._dead_letter_stream_key, message.handle)
        return replayed.model_copy(update={"handle": entry_id})

    async def replay_dead_letters(
        self,
        *,
        limit: int,
        reset_attempt: bool = True,
        remove_from_dead_letters: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[QueueMessage, ...]:
        """Requeue a bounded batch of dead-lettered session ids in stream order."""

        _validate_positive_limit(limit)
        dead_letters = await self.dead_letters(limit=limit)
        replayed: list[QueueMessage] = []
        for message in dead_letters:
            replayed.append(
                await self.replay_dead_letter(
                    message,
                    reset_attempt=reset_attempt,
                    remove_from_dead_letters=remove_from_dead_letters,
                    metadata=metadata,
                )
            )
        return tuple(replayed)

    async def close(self) -> None:
        self._closed = True
        aclose = getattr(self._client, "aclose", None)
        if callable(aclose):
            await aclose()
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._client.xgroup_create(
                name=self._stream_key,
                groupname=self._group_name,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if not _is_busygroup_error(exc):
                raise
        self._group_ready = True

    async def _reclaim_expired(self) -> QueueMessage | None:
        if self._visibility_timeout_seconds is None:
            return None
        min_idle_ms = int(self._visibility_timeout_seconds * 1000)
        response = await self._client.xautoclaim(
            self._stream_key,
            self._group_name,
            self._consumer_name,
            min_idle_ms,
            "0-0",
            count=1,
        )
        entries = _claimed_entries(response)
        if not entries:
            return None
        entry_id, fields = entries[0]
        previous = await self._current_delivery_message(entry_id, fields)
        next_attempt = previous.attempt + 1
        source_handle = previous.handle or entry_id
        metadata = _next_delivery_metadata(
            previous,
            reason=_RETRY_REASON_VISIBILITY_TIMEOUT,
            source_handle=source_handle,
            next_attempt=next_attempt,
        )
        reclaimed = previous.model_copy(
            update={
                "attempt": next_attempt,
                "metadata": metadata,
                "handle": entry_id,
            }
        )
        if self._should_dead_letter(next_attempt):
            await self._dead_letter(
                reclaimed,
                reason=_RETRY_REASON_VISIBILITY_TIMEOUT,
                source_handle=source_handle,
                next_attempt=next_attempt,
            )
            await self._ack_and_delete(entry_id)
            return None
        return await self._issue_delivery(entry_id, reclaimed)

    async def _retry_or_dead_letter(
        self,
        message: QueueMessage,
        *,
        reason: str,
        source_handle: str,
    ) -> QueueMessage | None:
        next_attempt = message.attempt + 1
        metadata = _next_delivery_metadata(
            message,
            reason=reason,
            source_handle=source_handle,
            next_attempt=next_attempt,
        )
        next_delivery = QueueMessage(
            session_id=message.session_id,
            attempt=next_attempt,
            metadata=metadata,
        )
        if self._should_dead_letter(next_attempt):
            await self._dead_letter(
                next_delivery,
                reason=reason,
                source_handle=source_handle,
                next_attempt=next_attempt,
            )
            return None
        entry_id = _to_text(
            await self._client.xadd(
                self._stream_key,
                _message_fields(next_delivery),
            )
        )
        return next_delivery.model_copy(update={"handle": entry_id})

    async def _issue_delivery(
        self,
        entry_id: str,
        message: QueueMessage,
    ) -> QueueMessage:
        token = f"redis-{uuid.uuid4().hex[:12]}"
        metadata = dict(message.metadata)
        metadata.update(
            {
                "redis_stream": self._stream_key,
                "redis_stream_id": entry_id,
                "redis_consumer_group": self._group_name,
                "redis_consumer": self._consumer_name,
            }
        )
        await self._write_delivery_state(
            entry_id,
            token=token,
            attempt=message.attempt,
            metadata=metadata,
        )
        return message.model_copy(
            update={
                "handle": _join_handle(entry_id, token),
                "metadata": metadata,
            }
        )

    async def _delivery_token_matches(self, entry_id: str, token: str) -> bool:
        if not entry_id or not token:
            return False
        state = await self._read_delivery_state(entry_id)
        return state.get("token") == token

    async def _ack_and_delete(self, entry_id: str) -> None:
        await self._client.xack(self._stream_key, self._group_name, entry_id)
        await self._client.xdel(self._stream_key, entry_id)
        await self._client.hdel(self._delivery_state_hash_key, entry_id)

    async def _current_delivery_message(
        self,
        entry_id: str,
        fields: Mapping[Any, Any],
    ) -> QueueMessage:
        message = _message_from_fields(entry_id, fields)
        state = await self._read_delivery_state(entry_id)
        if not state:
            return message
        token = str(state.get("token", ""))
        handle = _join_handle(entry_id, token) if token else entry_id
        return message.model_copy(
            update={
                "attempt": int(state.get("attempt", message.attempt)),
                "metadata": dict(state.get("metadata", message.metadata)),
                "handle": handle,
            }
        )

    async def _dead_letter(
        self,
        message: QueueMessage,
        *,
        reason: str,
        source_handle: str,
        next_attempt: int,
    ) -> None:
        metadata = dict(message.metadata)
        metadata.update(
            {
                "dead_letter_reason": _DEAD_LETTER_REASON_MAX_ATTEMPTS,
                "dead_letter_source": reason,
                "dead_letter_handle": source_handle,
                "dead_letter_attempt": next_attempt,
            }
        )
        dead_message = message.model_copy(update={"metadata": metadata})
        await self._client.xadd(
            self._dead_letter_stream_key,
            _message_fields(dead_message),
        )

    async def _write_delivery_state(
        self,
        entry_id: str,
        *,
        token: str,
        attempt: int,
        metadata: dict[str, Any],
    ) -> None:
        await self._client.hset(
            self._delivery_state_hash_key,
            entry_id,
            json.dumps(
                {
                    "token": token,
                    "attempt": attempt,
                    "metadata": metadata,
                },
                sort_keys=True,
            ),
        )

    async def _read_delivery_state(self, entry_id: str) -> dict[str, Any]:
        raw = await self._client.hget(self._delivery_state_hash_key, entry_id)
        if raw is None:
            return {}
        state = _loads_metadata(_to_text(raw))
        metadata = state.get("metadata")
        if not isinstance(metadata, dict):
            state["metadata"] = {}
        return state

    def _should_dead_letter(self, attempt: int) -> bool:
        return (
            self._max_delivery_attempts is not None
            and attempt >= self._max_delivery_attempts
        )

    def _reject_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError(f"{self.name!r} queue is closed")


def _is_busygroup_error(exc: Exception) -> bool:
    """True only for a redis ``ResponseError`` saying the group already exists.

    The server error type is identified structurally (type name in the MRO plus
    the BUSYGROUP message prefix) so this check never imports the optional
    redis dependency. Substring matching on arbitrary exceptions previously
    swallowed unrelated errors whose text merely mentioned BUSYGROUP.
    """
    if not str(exc).startswith("BUSYGROUP"):
        return False
    return any(cls.__name__ == "ResponseError" for cls in type(exc).__mro__)


def _create_redis_client(*, url: str) -> Any:
    try:
        redis_asyncio = importlib.import_module("redis.asyncio")
    except ImportError as exc:
        raise RuntimeError(
            "RedisStreamQueueAdapter requires optional dependency hydramind[redis]"
        ) from exc
    redis_factory = cast(Any, redis_asyncio).Redis
    return redis_factory.from_url(url)


def _block_ms(timeout: float | None) -> int:
    if timeout is None:
        return 0
    return max(1, int(timeout * 1000))
