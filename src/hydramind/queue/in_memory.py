"""InMemoryQueueAdapter — single-process FIFO with async lock and ack tracking."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from hydramind.queue.base import QueueCapabilities, QueueMessage

_RETRY_REASON_NACK = "nack"
_RETRY_REASON_VISIBILITY_TIMEOUT = "visibility_timeout"
_DEAD_LETTER_REASON_MAX_ATTEMPTS = "max_delivery_attempts_exceeded"


@dataclass(frozen=True)
class _InFlightMessage:
    message: QueueMessage
    expires_at: float | None = None


class InMemoryQueueAdapter:
    """Single-process queue. Suitable for tests and small in-process demos.

    Messages live in an asyncio.Queue. ``dequeue`` issues a handle that callers
    must return via ``ack`` (success) or ``nack`` (retry/drop). When configured,
    expired in-flight handles are redelivered with a new handle and incremented
    delivery attempt.
    """

    name = "in-memory"
    capabilities = QueueCapabilities.pollable_delivery()

    def __init__(
        self,
        *,
        maxsize: int = 0,
        visibility_timeout_seconds: float | None = None,
        max_delivery_attempts: int | None = None,
    ) -> None:
        if visibility_timeout_seconds is not None and visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        if max_delivery_attempts is not None and max_delivery_attempts <= 0:
            raise ValueError("max_delivery_attempts must be positive")
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue(maxsize=maxsize)
        self._in_flight: dict[str, _InFlightMessage] = {}
        self._dead_letters: list[QueueMessage] = []
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._max_delivery_attempts = max_delivery_attempts
        self._closed = False

    async def enqueue(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> QueueMessage:
        self._reject_if_closed()
        msg = QueueMessage(
            session_id=session_id,
            metadata=metadata or {},
            handle=f"mem-{uuid.uuid4().hex[:12]}",
        )
        await self._queue.put(msg)
        return msg

    async def dequeue(
        self, *, timeout: float | None = None
    ) -> QueueMessage | None:
        self._reject_if_closed()
        deadline = None if timeout is None else self._loop_time() + timeout
        while True:
            await self._redeliver_expired()
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                wait_timeout = self._dequeue_wait_timeout(deadline)
                if wait_timeout is not None and wait_timeout <= 0:
                    await self._redeliver_expired()
                    if self._queue.empty():
                        return None
                    continue
                try:
                    if wait_timeout is None:
                        msg = await self._queue.get()
                    else:
                        msg = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=wait_timeout,
                        )
                except TimeoutError:
                    continue
            self._track_in_flight(msg)
            return msg

    async def ack(self, message: QueueMessage) -> None:
        record = self._in_flight.pop(message.handle, None)
        if record is not None:
            self._task_done()

    async def nack(
        self, message: QueueMessage, *, retry: bool = True
    ) -> None:
        record = self._in_flight.pop(message.handle, None)
        if record is None:
            return
        self._task_done()
        if retry and not self._closed:
            await self._retry_or_dead_letter(
                message,
                reason=_RETRY_REASON_NACK,
            )

    async def pending(self) -> int:
        await self._redeliver_expired()
        return self._queue.qsize()

    async def in_flight(self) -> int:
        await self._redeliver_expired()
        return len(self._in_flight)

    async def dead_letters(self) -> tuple[QueueMessage, ...]:
        await self._redeliver_expired()
        return tuple(self._dead_letters)

    async def close(self) -> None:
        self._closed = True
        # Drain pending; nothing to do for in-memory.

    def _reject_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError(f"{self.name!r} queue is closed")

    def _track_in_flight(self, message: QueueMessage) -> None:
        expires_at = (
            self._loop_time() + self._visibility_timeout_seconds
            if self._visibility_timeout_seconds is not None
            else None
        )
        self._in_flight[message.handle] = _InFlightMessage(
            message=message,
            expires_at=expires_at,
        )

    async def _redeliver_expired(self) -> None:
        if self._visibility_timeout_seconds is None:
            return
        now = self._loop_time()
        expired = [
            (handle, record)
            for handle, record in self._in_flight.items()
            if record.expires_at is not None and record.expires_at <= now
        ]
        for handle, record in expired:
            if self._in_flight.pop(handle, None) is None:
                continue
            self._task_done()
            if not self._closed:
                await self._retry_or_dead_letter(
                    record.message,
                    reason=_RETRY_REASON_VISIBILITY_TIMEOUT,
                )

    async def _retry_or_dead_letter(
        self,
        message: QueueMessage,
        *,
        reason: str,
    ) -> None:
        next_attempt = message.attempt + 1
        metadata = dict(message.metadata)
        metadata.update(
            {
                "last_delivery_reason": reason,
                "last_delivery_handle": message.handle,
                "last_delivery_attempt": message.attempt,
                "delivery_attempt": next_attempt,
            }
        )
        if self._should_dead_letter(next_attempt):
            metadata.update(
                {
                    "dead_letter_reason": _DEAD_LETTER_REASON_MAX_ATTEMPTS,
                    "dead_letter_source": reason,
                    "dead_letter_handle": message.handle,
                    "dead_letter_attempt": next_attempt,
                }
            )
        next_delivery = message.model_copy(
            update={
                "attempt": next_attempt,
                "handle": f"mem-{uuid.uuid4().hex[:12]}",
                "metadata": metadata,
            }
        )
        if self._should_dead_letter(next_attempt):
            self._dead_letters.append(next_delivery)
            return
        await self._queue.put(next_delivery)

    def _should_dead_letter(self, attempt: int) -> bool:
        return (
            self._max_delivery_attempts is not None
            and attempt >= self._max_delivery_attempts
        )

    def _dequeue_wait_timeout(self, deadline: float | None) -> float | None:
        candidates: list[float] = []
        now = self._loop_time()
        if deadline is not None:
            candidates.append(max(0.0, deadline - now))
        next_expiry = self._next_expiry()
        if next_expiry is not None:
            candidates.append(max(0.0, next_expiry - now))
        return min(candidates) if candidates else None

    def _next_expiry(self) -> float | None:
        expiries = [
            record.expires_at
            for record in self._in_flight.values()
            if record.expires_at is not None
        ]
        return min(expiries) if expiries else None

    @staticmethod
    def _loop_time() -> float:
        return asyncio.get_running_loop().time()

    def _task_done(self) -> None:
        try:
            self._queue.task_done()
        except ValueError:
            pass
