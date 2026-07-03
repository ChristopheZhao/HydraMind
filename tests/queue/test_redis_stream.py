"""RedisStreamQueueAdapter tests without a live Redis service."""

from __future__ import annotations

import pytest

from hydramind.queue import QueueCapability, QueueMessage, RedisStreamQueueAdapter
from tests.queue.fake_redis_streams import FakeRedisStreams


class RedisPyShapeFake(FakeRedisStreams):
    """Fake Redis Streams client that returns redis-py style list responses."""

    async def xreadgroup(self, *args, **kwargs):
        response = await super().xreadgroup(*args, **kwargs)
        return [
            [stream, [[entry_id, fields] for entry_id, fields in entries]]
            for stream, entries in response
        ]

    async def xautoclaim(self, *args, **kwargs):
        next_id, entries, deleted = await super().xautoclaim(*args, **kwargs)
        return [next_id, [[entry_id, fields] for entry_id, fields in entries], deleted]

    async def xrange(self, *args, **kwargs):
        entries = await super().xrange(*args, **kwargs)
        return [[entry_id, fields] for entry_id, fields in entries]


def test_redis_stream_adapter_declares_pollable_delivery_capabilities() -> None:
    capabilities = RedisStreamQueueAdapter.capabilities

    assert capabilities.supports_pollable_delivery
    assert capabilities.supports(
        QueueCapability.ENQUEUE,
        QueueCapability.DEQUEUE,
        QueueCapability.ACK,
        QueueCapability.NACK,
        QueueCapability.PENDING,
        QueueCapability.CLOSE,
    )


def test_redis_stream_adapter_requires_client_or_url() -> None:
    with pytest.raises(ValueError, match="redis_client or url"):
        RedisStreamQueueAdapter()


@pytest.mark.asyncio
async def test_enqueue_dequeue_ack_round_trip() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)

    enqueued = await queue.enqueue("sess-1", metadata={"workflow": "demo"})
    assert enqueued.handle == "1-0"
    assert await queue.pending() == 1

    delivery = await queue.dequeue(timeout=0.01)

    assert isinstance(delivery, QueueMessage)
    assert delivery.session_id == "sess-1"
    assert delivery.attempt == 0
    assert delivery.handle.startswith("1-0|redis-")
    assert delivery.metadata["workflow"] == "demo"
    assert delivery.metadata["redis_stream"] == "hydramind:sessions"
    assert delivery.metadata["redis_stream_id"] == "1-0"
    assert await queue.pending() == 0
    assert await queue.in_flight() == 1

    await queue.ack(delivery)

    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
    assert await queue.dequeue(timeout=0.01) is None


@pytest.mark.asyncio
async def test_adapter_accepts_redis_py_list_response_shapes() -> None:
    redis = RedisPyShapeFake()
    queue = RedisStreamQueueAdapter(
        redis_client=redis,
        visibility_timeout_seconds=0.01,
        max_delivery_attempts=1,
    )
    await queue.enqueue("sess-redis-py")
    first = await queue.dequeue(timeout=0.01)
    assert first is not None
    assert first.session_id == "sess-redis-py"

    redis.advance(0.02)
    assert await queue.dequeue(timeout=0.01) is None
    dead_letters = await queue.dead_letters(limit=1)

    assert len(dead_letters) == 1
    assert dead_letters[0].session_id == "sess-redis-py"


@pytest.mark.asyncio
async def test_nack_retry_requeues_with_delivery_metadata() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)
    await queue.enqueue("sess-1", metadata={"kind": "retry"})
    first = await queue.dequeue(timeout=0.01)
    assert first is not None

    await queue.nack(first, retry=True)

    retried = await queue.dequeue(timeout=0.01)
    assert retried is not None
    assert retried.session_id == "sess-1"
    assert retried.attempt == 1
    assert retried.handle.startswith("2-0|redis-")
    assert retried.metadata["kind"] == "retry"
    assert retried.metadata["last_delivery_reason"] == "nack"
    assert retried.metadata["last_delivery_handle"] == first.handle
    assert retried.metadata["last_delivery_attempt"] == 0
    assert retried.metadata["delivery_attempt"] == 1
    assert await queue.dead_letters() == ()


@pytest.mark.asyncio
async def test_nack_without_retry_drops_delivery() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)
    await queue.enqueue("sess-1")
    delivery = await queue.dequeue(timeout=0.01)
    assert delivery is not None

    await queue.nack(delivery, retry=False)

    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
    assert await queue.dequeue(timeout=0.01) is None


@pytest.mark.asyncio
async def test_nack_retry_dead_letters_after_max_delivery_attempts() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=1)
    await queue.enqueue("sess-1", metadata={"kind": "poison"})
    delivery = await queue.dequeue(timeout=0.01)
    assert delivery is not None

    await queue.nack(delivery, retry=True)

    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
    dead_letters = await queue.dead_letters()
    assert len(dead_letters) == 1
    dead = dead_letters[0]
    assert dead.session_id == "sess-1"
    assert dead.attempt == 1
    assert dead.metadata["kind"] == "poison"
    assert dead.metadata["last_delivery_reason"] == "nack"
    assert dead.metadata["last_delivery_handle"] == delivery.handle
    assert dead.metadata["delivery_attempt"] == 1
    assert dead.metadata["dead_letter_reason"] == "max_delivery_attempts_exceeded"
    assert dead.metadata["dead_letter_source"] == "nack"
    assert dead.metadata["dead_letter_handle"] == delivery.handle
    assert dead.metadata["dead_letter_attempt"] == 1


@pytest.mark.asyncio
async def test_max_delivery_attempts_allows_exactly_n_deliveries() -> None:
    """``max_delivery_attempts=N`` is a budget of N deliveries total.

    Boundary pin for PLAN-20260612-001 D4: ``attempt`` is the 0-indexed
    delivery ordinal, so with N=2 the message is delivered at attempt 0 and
    attempt 1; the second failure dead-letters with ``dead_letter_attempt=2``
    and no third delivery ever happens.
    """
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=2)
    await queue.enqueue("sess-1", metadata={"kind": "poison"})

    # Delivery 1 (attempt 0) fails → retried, not dead-lettered.
    first = await queue.dequeue(timeout=0.01)
    assert first is not None
    assert first.attempt == 0
    await queue.nack(first, retry=True)
    assert not await queue.dead_letters()
    assert await queue.pending() == 1

    # Delivery 2 (attempt 1) fails → budget exhausted, dead-lettered.
    second = await queue.dequeue(timeout=0.01)
    assert second is not None
    assert second.attempt == 1
    await queue.nack(second, retry=True)

    assert await queue.pending() == 0
    assert await queue.in_flight() == 0
    dead_letters = await queue.dead_letters()
    assert len(dead_letters) == 1
    dead = dead_letters[0]
    assert dead.metadata["dead_letter_reason"] == "max_delivery_attempts_exceeded"
    assert dead.metadata["dead_letter_attempt"] == 2
    # No third delivery is available.
    assert await queue.dequeue(timeout=0.01) is None


@pytest.mark.asyncio
async def test_visibility_timeout_reclaims_with_new_handle() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(
        redis_client=redis,
        visibility_timeout_seconds=0.01,
    )
    await queue.enqueue("sess-1")
    first = await queue.dequeue(timeout=0.01)
    assert first is not None

    redis.advance(0.02)
    second = await queue.dequeue(timeout=0.01)

    assert second is not None
    assert second.session_id == "sess-1"
    assert second.attempt == 1
    assert second.handle.startswith("1-0|redis-")
    assert second.handle != first.handle
    assert second.metadata["last_delivery_reason"] == "visibility_timeout"
    assert second.metadata["last_delivery_handle"] == first.handle
    assert second.metadata["last_delivery_attempt"] == 0
    assert second.metadata["delivery_attempt"] == 1
    assert await queue.in_flight() == 1


@pytest.mark.asyncio
async def test_visibility_timeout_stale_ack_does_not_drop_redelivery() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(
        redis_client=redis,
        visibility_timeout_seconds=0.01,
    )
    await queue.enqueue("sess-1")
    first = await queue.dequeue(timeout=0.01)
    assert first is not None
    redis.advance(0.02)
    second = await queue.dequeue(timeout=0.01)
    assert second is not None

    await queue.ack(first)
    assert await queue.in_flight() == 1
    await queue.ack(second)
    assert await queue.in_flight() == 0
    assert await queue.pending() == 0


@pytest.mark.asyncio
async def test_visibility_timeout_dead_letters_after_max_delivery_attempts() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(
        redis_client=redis,
        visibility_timeout_seconds=0.01,
        max_delivery_attempts=1,
    )
    await queue.enqueue("sess-1")
    first = await queue.dequeue(timeout=0.01)
    assert first is not None

    redis.advance(0.02)

    assert await queue.dequeue(timeout=0.01) is None
    assert await queue.in_flight() == 0
    dead_letters = await queue.dead_letters()
    assert len(dead_letters) == 1
    dead = dead_letters[0]
    assert dead.session_id == "sess-1"
    assert dead.attempt == 1
    assert dead.metadata["last_delivery_reason"] == "visibility_timeout"
    assert dead.metadata["last_delivery_handle"] == first.handle
    assert dead.metadata["delivery_attempt"] == 1
    assert dead.metadata["dead_letter_reason"] == "max_delivery_attempts_exceeded"
    assert dead.metadata["dead_letter_source"] == "visibility_timeout"
    assert dead.metadata["dead_letter_handle"] == first.handle
    assert dead.metadata["dead_letter_attempt"] == 1


@pytest.mark.asyncio
async def test_dead_letters_support_bounded_inspection() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=1)
    first = await _dead_letter_by_nack(queue, "sess-1")
    second = await _dead_letter_by_nack(queue, "sess-2")

    one = await queue.dead_letters(limit=1)
    all_letters = await queue.dead_letters()

    assert [message.session_id for message in one] == [first.session_id]
    assert [message.session_id for message in all_letters] == [
        first.session_id,
        second.session_id,
    ]
    with pytest.raises(ValueError, match="limit must be positive"):
        await queue.dead_letters(limit=0)


@pytest.mark.asyncio
async def test_replay_dead_letter_requeues_and_removes_dead_letter() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=1)
    dead = await _dead_letter_by_nack(queue, "sess-1", metadata={"kind": "poison"})

    replayed = await queue.replay_dead_letter(
        dead,
        metadata={"operator": "retry-once"},
    )

    assert replayed.session_id == "sess-1"
    assert replayed.attempt == 0
    assert replayed.handle == "3-0"
    assert replayed.metadata["kind"] == "poison"
    assert replayed.metadata["operator"] == "retry-once"
    assert replayed.metadata["replay_source"] == "dead_letter"
    assert replayed.metadata["replay_dead_letter_stream"] == "hydramind:sessions:dead"
    assert replayed.metadata["replay_dead_letter_handle"] == dead.handle
    assert replayed.metadata["replay_original_attempt"] == 1
    assert replayed.metadata["replay_attempt_reset"] is True
    assert replayed.metadata["replay_count"] == 1
    assert "redis_stream_id" not in replayed.metadata
    assert await queue.dead_letters() == ()
    assert await queue.pending() == 1

    delivery = await queue.dequeue(timeout=0.01)

    assert delivery is not None
    assert delivery.session_id == "sess-1"
    assert delivery.attempt == 0
    assert delivery.handle.startswith("3-0|redis-")
    assert delivery.metadata["redis_stream_id"] == "3-0"
    assert delivery.metadata["replay_dead_letter_handle"] == dead.handle


@pytest.mark.asyncio
async def test_replay_dead_letter_can_preserve_attempt_and_retain_dead_letter() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=1)
    dead = await _dead_letter_by_nack(queue, "sess-1")

    replayed = await queue.replay_dead_letter(
        dead,
        reset_attempt=False,
        remove_from_dead_letters=False,
    )

    assert replayed.attempt == dead.attempt
    assert replayed.metadata["replay_attempt_reset"] is False
    assert await queue.pending() == 1
    retained = await queue.dead_letters()
    assert len(retained) == 1
    assert retained[0].handle == dead.handle


@pytest.mark.asyncio
async def test_replay_dead_letters_requeues_bounded_batch_in_order() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis, max_delivery_attempts=1)
    first = await _dead_letter_by_nack(queue, "sess-1")
    second = await _dead_letter_by_nack(queue, "sess-2")

    replayed = await queue.replay_dead_letters(limit=1)

    assert [message.session_id for message in replayed] == [first.session_id]
    assert replayed[0].metadata["replay_dead_letter_handle"] == first.handle
    assert await queue.pending() == 1
    remaining = await queue.dead_letters()
    assert [message.session_id for message in remaining] == [second.session_id]


@pytest.mark.asyncio
async def test_replay_dead_letters_rejects_non_positive_limit() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)

    with pytest.raises(ValueError, match="limit must be positive"):
        await queue.replay_dead_letters(limit=0)


@pytest.mark.asyncio
async def test_replay_dead_letter_requires_dead_letter_handle() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)

    with pytest.raises(ValueError, match="dead-letter message handle"):
        await queue.replay_dead_letter(QueueMessage(session_id="sess-1"))


@pytest.mark.asyncio
async def test_close_rejects_new_work_and_closes_client() -> None:
    redis = FakeRedisStreams()
    queue = RedisStreamQueueAdapter(redis_client=redis)

    await queue.close()

    assert redis.closed
    with pytest.raises(RuntimeError, match="closed"):
        await queue.enqueue("sess-1")


@pytest.mark.asyncio
async def test_ensure_group_suppresses_only_busygroup_response_error() -> None:
    """Pre-existing consumer groups are detected via ResponseError + prefix.

    Regression for PLAN-20260612-001 D3: two adapters sharing one stream must
    both come ready when the second ``xgroup_create`` answers BUSYGROUP.
    """
    redis = FakeRedisStreams()
    first = RedisStreamQueueAdapter(redis_client=redis)
    second = RedisStreamQueueAdapter(redis_client=redis)

    await first.enqueue("sess-1")
    # Second adapter hits ResponseError("BUSYGROUP ...") and must suppress it.
    await second.enqueue("sess-2")

    assert await second.pending() == 2


@pytest.mark.asyncio
async def test_ensure_group_propagates_non_response_error_with_busygroup_text() -> None:
    """A non-ResponseError mentioning BUSYGROUP must not be swallowed (D3)."""

    class FlakyRedis(FakeRedisStreams):
        async def xgroup_create(self, **kwargs: object) -> None:
            raise ConnectionResetError(
                "connection reset while creating group BUSYGROUP-like"
            )

    queue = RedisStreamQueueAdapter(redis_client=FlakyRedis())

    with pytest.raises(ConnectionResetError):
        await queue.enqueue("sess-1")


@pytest.mark.asyncio
async def test_ensure_group_propagates_other_response_errors() -> None:
    """ResponseError without the BUSYGROUP prefix must propagate (D3)."""
    from tests.queue.fake_redis_streams import ResponseError

    class UnauthorizedRedis(FakeRedisStreams):
        async def xgroup_create(self, **kwargs: object) -> None:
            raise ResponseError("NOAUTH Authentication required.")

    queue = RedisStreamQueueAdapter(redis_client=UnauthorizedRedis())

    with pytest.raises(ResponseError, match="NOAUTH"):
        await queue.enqueue("sess-1")


async def _dead_letter_by_nack(
    queue: RedisStreamQueueAdapter,
    session_id: str,
    *,
    metadata: dict[str, object] | None = None,
) -> QueueMessage:
    await queue.enqueue(session_id, metadata=metadata)
    delivery = await queue.dequeue(timeout=0.01)
    assert delivery is not None
    await queue.nack(delivery, retry=True)
    dead_letters = await queue.dead_letters()
    return dead_letters[-1]
