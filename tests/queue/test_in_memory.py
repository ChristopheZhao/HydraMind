"""InMemoryQueueAdapter tests — FIFO, ack/nack, retry, timeout, closure."""

from __future__ import annotations

import asyncio

import pytest

from hydramind.queue import InMemoryQueueAdapter, QueueCapability, QueueMessage


@pytest.fixture
async def queue() -> InMemoryQueueAdapter:
    q = InMemoryQueueAdapter()
    yield q
    await q.close()


def test_in_memory_adapter_declares_pollable_delivery_capabilities() -> None:
    capabilities = InMemoryQueueAdapter.capabilities

    assert capabilities.supports_pollable_delivery
    assert capabilities.supports(
        QueueCapability.ENQUEUE,
        QueueCapability.DEQUEUE,
        QueueCapability.ACK,
        QueueCapability.NACK,
        QueueCapability.PENDING,
        QueueCapability.CLOSE,
    )


@pytest.mark.asyncio
async def test_enqueue_then_dequeue_round_trip(queue: InMemoryQueueAdapter) -> None:
    await queue.enqueue("sess-1")
    msg = await queue.dequeue(timeout=1.0)
    assert isinstance(msg, QueueMessage)
    assert msg.session_id == "sess-1"
    assert msg.attempt == 0
    assert msg.handle


@pytest.mark.asyncio
async def test_dequeue_timeout_returns_none(queue: InMemoryQueueAdapter) -> None:
    assert await queue.dequeue(timeout=0.05) is None


@pytest.mark.asyncio
async def test_fifo_order(queue: InMemoryQueueAdapter) -> None:
    for sid in ["a", "b", "c"]:
        await queue.enqueue(sid)
    out = [(await queue.dequeue(timeout=1.0)).session_id for _ in range(3)]  # type: ignore[union-attr]
    assert out == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_ack_removes_from_in_flight(queue: InMemoryQueueAdapter) -> None:
    await queue.enqueue("sess-1")
    msg = await queue.dequeue(timeout=1.0)
    assert msg is not None
    assert await queue.in_flight() == 1
    await queue.ack(msg)
    assert await queue.in_flight() == 0


@pytest.mark.asyncio
async def test_nack_with_retry_requeues_with_incremented_attempt(
    queue: InMemoryQueueAdapter,
) -> None:
    await queue.enqueue("sess-1")
    first = await queue.dequeue(timeout=1.0)
    assert first is not None
    await queue.nack(first, retry=True)
    second = await queue.dequeue(timeout=1.0)
    assert second is not None
    assert second.session_id == "sess-1"
    assert second.attempt == 1
    assert second.handle != first.handle
    assert second.metadata["last_delivery_reason"] == "nack"
    assert second.metadata["last_delivery_handle"] == first.handle
    assert second.metadata["last_delivery_attempt"] == 0
    assert second.metadata["delivery_attempt"] == 1
    assert await queue.dead_letters() == ()


@pytest.mark.asyncio
async def test_nack_without_retry_drops(queue: InMemoryQueueAdapter) -> None:
    await queue.enqueue("sess-1")
    msg = await queue.dequeue(timeout=1.0)
    assert msg is not None
    await queue.nack(msg, retry=False)
    assert await queue.pending() == 0
    assert await queue.dequeue(timeout=0.05) is None


@pytest.mark.asyncio
async def test_nack_retry_dead_letters_after_max_delivery_attempts() -> None:
    q = InMemoryQueueAdapter(max_delivery_attempts=2)
    await q.enqueue("sess-1", metadata={"kind": "poison"})
    first = await q.dequeue(timeout=1.0)
    assert first is not None
    await q.nack(first, retry=True)
    second = await q.dequeue(timeout=1.0)
    assert second is not None
    assert second.attempt == 1

    await q.nack(second, retry=True)

    assert await q.pending() == 0
    assert await q.dequeue(timeout=0.01) is None
    dead_letters = await q.dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0].session_id == "sess-1"
    assert dead_letters[0].metadata["kind"] == "poison"
    assert dead_letters[0].metadata["last_delivery_reason"] == "nack"
    assert dead_letters[0].metadata["last_delivery_handle"] == second.handle
    assert dead_letters[0].metadata["last_delivery_attempt"] == 1
    assert dead_letters[0].metadata["delivery_attempt"] == 2
    assert (
        dead_letters[0].metadata["dead_letter_reason"]
        == "max_delivery_attempts_exceeded"
    )
    assert dead_letters[0].metadata["dead_letter_source"] == "nack"
    assert dead_letters[0].metadata["dead_letter_handle"] == second.handle
    assert dead_letters[0].metadata["dead_letter_attempt"] == 2
    assert dead_letters[0].attempt == 2
    await q.close()


@pytest.mark.asyncio
async def test_visibility_timeout_redelivers_unacked_message() -> None:
    q = InMemoryQueueAdapter(visibility_timeout_seconds=0.01)
    await q.enqueue("sess-1")
    first = await q.dequeue(timeout=1.0)
    assert first is not None
    assert await q.in_flight() == 1

    await asyncio.sleep(0.02)
    second = await q.dequeue(timeout=1.0)

    assert second is not None
    assert second.session_id == "sess-1"
    assert second.attempt == 1
    assert second.handle != first.handle
    assert second.metadata["last_delivery_reason"] == "visibility_timeout"
    assert second.metadata["last_delivery_handle"] == first.handle
    assert second.metadata["last_delivery_attempt"] == 0
    assert second.metadata["delivery_attempt"] == 1
    assert await q.in_flight() == 1
    await q.close()


@pytest.mark.asyncio
async def test_visibility_timeout_dead_letters_after_max_delivery_attempts() -> None:
    q = InMemoryQueueAdapter(
        visibility_timeout_seconds=0.01,
        max_delivery_attempts=1,
    )
    await q.enqueue("sess-1")
    first = await q.dequeue(timeout=1.0)
    assert first is not None

    await asyncio.sleep(0.02)

    assert await q.dequeue(timeout=0.01) is None
    assert await q.in_flight() == 0
    dead_letters = await q.dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0].session_id == "sess-1"
    assert dead_letters[0].attempt == 1
    assert dead_letters[0].metadata["last_delivery_reason"] == "visibility_timeout"
    assert dead_letters[0].metadata["last_delivery_handle"] == first.handle
    assert dead_letters[0].metadata["last_delivery_attempt"] == 0
    assert dead_letters[0].metadata["delivery_attempt"] == 1
    assert (
        dead_letters[0].metadata["dead_letter_reason"]
        == "max_delivery_attempts_exceeded"
    )
    assert dead_letters[0].metadata["dead_letter_source"] == "visibility_timeout"
    assert dead_letters[0].metadata["dead_letter_handle"] == first.handle
    assert dead_letters[0].metadata["dead_letter_attempt"] == 1
    await q.close()


@pytest.mark.asyncio
async def test_visibility_timeout_stale_ack_does_not_drop_redelivery() -> None:
    q = InMemoryQueueAdapter(visibility_timeout_seconds=0.01)
    await q.enqueue("sess-1")
    first = await q.dequeue(timeout=1.0)
    assert first is not None
    await asyncio.sleep(0.02)
    second = await q.dequeue(timeout=1.0)
    assert second is not None

    await q.ack(first)
    assert await q.in_flight() == 1
    await q.ack(second)
    assert await q.in_flight() == 0
    await q.close()


@pytest.mark.asyncio
async def test_visibility_timeout_is_disabled_by_default(
    queue: InMemoryQueueAdapter,
) -> None:
    await queue.enqueue("sess-1")
    first = await queue.dequeue(timeout=1.0)
    assert first is not None
    await asyncio.sleep(0.02)

    assert await queue.dequeue(timeout=0.01) is None
    assert await queue.in_flight() == 1


@pytest.mark.asyncio
async def test_close_rejects_further_enqueue(queue: InMemoryQueueAdapter) -> None:
    await queue.close()
    with pytest.raises(RuntimeError, match="closed"):
        await queue.enqueue("after-close")


@pytest.mark.asyncio
async def test_pending_counts_only_queued(queue: InMemoryQueueAdapter) -> None:
    await queue.enqueue("a")
    await queue.enqueue("b")
    assert await queue.pending() == 2
    msg = await queue.dequeue(timeout=1.0)
    assert msg is not None
    assert await queue.pending() == 1
    assert await queue.in_flight() == 1


@pytest.mark.asyncio
async def test_concurrent_producer_consumer() -> None:
    q = InMemoryQueueAdapter()
    consumed: list[str] = []

    async def producer() -> None:
        for i in range(10):
            await q.enqueue(f"s-{i}")
            await asyncio.sleep(0)

    async def consumer() -> None:
        while len(consumed) < 10:
            msg = await q.dequeue(timeout=1.0)
            if msg is None:
                continue
            consumed.append(msg.session_id)
            await q.ack(msg)

    await asyncio.gather(producer(), consumer())
    assert consumed == [f"s-{i}" for i in range(10)]
    await q.close()


@pytest.mark.asyncio
async def test_worker_loop_pattern_end_to_end() -> None:
    """Documented worker-loop pattern from 60-queue-adapter.md §5."""
    q = InMemoryQueueAdapter()
    await q.enqueue("sess-a")
    await q.enqueue("sess-b")

    handled: list[str] = []

    async def worker() -> None:
        for _ in range(2):
            msg = await q.dequeue(timeout=1.0)
            assert msg is not None
            try:
                handled.append(msg.session_id)
            except Exception:
                await q.nack(msg, retry=True)
                raise
            else:
                await q.ack(msg)

    await worker()
    assert handled == ["sess-a", "sess-b"]
    assert await q.pending() == 0
    await q.close()
