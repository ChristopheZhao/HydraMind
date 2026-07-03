"""CeleryQueueAdapter boundary tests without importing celery."""

from __future__ import annotations

import pytest

from hydramind.queue import CeleryQueueAdapter, QueueCapability


class _Result:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _Inspector:
    def active(self) -> dict[str, list[dict[str, str]]]:
        return {"w1": [{"id": "a"}]}

    def reserved(self) -> dict[str, list[dict[str, str]]]:
        return {"w1": [{"id": "r"}]}

    def scheduled(self) -> dict[str, list[dict[str, str]]]:
        return {"w1": []}


class _Control:
    def inspect(self) -> _Inspector:
        return _Inspector()


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.control = _Control()
        self.closed = False

    def send_task(
        self,
        task_name: str,
        *,
        args: list[str],
        kwargs: dict[str, object],
        queue: str | None,
    ) -> _Result:
        task_id = f"task-{len(self.sent) + 1}"
        self.sent.append(
            {"task_name": task_name, "args": args, "kwargs": kwargs, "queue": queue}
        )
        return _Result(task_id)

    def close(self) -> None:
        self.closed = True


def test_celery_adapter_declares_enqueue_only_capabilities() -> None:
    capabilities = CeleryQueueAdapter.capabilities

    assert not capabilities.supports_pollable_delivery
    assert capabilities.supports(
        QueueCapability.ENQUEUE,
        QueueCapability.PENDING,
        QueueCapability.CLOSE,
    )
    assert not capabilities.supports(
        QueueCapability.DEQUEUE,
        QueueCapability.ACK,
        QueueCapability.NACK,
    )


@pytest.mark.asyncio
async def test_celery_adapter_enqueues_and_retries_without_celery_dependency() -> None:
    app = _FakeCeleryApp()
    adapter = CeleryQueueAdapter(
        celery_app=app,
        task_name="hydramind.test",
        queue="hydramind",
    )

    msg = await adapter.enqueue("sess-1", metadata={"workflow": "demo"})
    assert msg.session_id == "sess-1"
    assert msg.handle == "task-1"
    assert app.sent[0]["task_name"] == "hydramind.test"
    assert app.sent[0]["queue"] == "hydramind"

    await adapter.nack(msg, retry=True)
    assert len(app.sent) == 2
    retry_kwargs = app.sent[1]["kwargs"]
    assert isinstance(retry_kwargs, dict)
    assert retry_kwargs["metadata"]["attempt"] == 1
    assert await adapter.pending() == 2

    await adapter.close()
    assert app.closed
    with pytest.raises(RuntimeError):
        await adapter.enqueue("sess-2")


@pytest.mark.asyncio
async def test_celery_adapter_dequeue_raises_not_implemented() -> None:
    app = _FakeCeleryApp()
    adapter = CeleryQueueAdapter(celery_app=app)

    with pytest.raises(NotImplementedError) as excinfo:
        await adapter.dequeue()

    message = str(excinfo.value)
    assert "enqueue-only" in message
    assert "in-memory adapter" in message
