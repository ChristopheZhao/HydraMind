"""CeleryQueueAdapter — enqueue RuntimeSession IDs through Celery (enqueue-only)."""

from __future__ import annotations

import importlib
import uuid
from typing import Any, cast

from hydramind.queue.base import QueueCapabilities, QueueMessage


class CeleryQueueAdapter:
    """Enqueue-only QueueAdapter implementation backed by Celery's task dispatcher.

    Celery follows a worker-side push model: the broker delivers each task to a
    Celery worker process that runs the registered task, and that worker performs
    dequeue/ack against the broker internally. HydraMind never polls Celery from
    the client side, so this adapter only supports the publish path
    (enqueue/nack/pending). It exposes the same surface used by HydraMind while
    keeping the celery dependency lazy and optional.

    Because there is no client-side poll loop, :meth:`dequeue` raises
    :class:`NotImplementedError` instead of silently returning ``None`` (which
    would make ``QueueExecutionHost`` idle-loop forever). For poll-based
    execution inside HydraMind, use the in-memory adapter (or another
    poll-capable adapter) with ``QueueExecutionHost``.
    """

    name = "celery"
    capabilities = QueueCapabilities.enqueue_only()

    def __init__(
        self,
        *,
        celery_app: Any | None = None,
        broker_url: str | None = None,
        result_backend: str | None = None,
        task_name: str = "hydramind.run_session",
        queue: str | None = None,
    ) -> None:
        self._app = celery_app or _create_celery_app(
            broker_url=broker_url,
            result_backend=result_backend,
        )
        self._task_name = task_name
        self._queue = queue
        self._closed = False

    async def enqueue(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> QueueMessage:
        self._reject_if_closed()
        return self._send(session_id, dict(metadata or {}), 0)

    async def dequeue(
        self, *, timeout: float | None = None
    ) -> QueueMessage | None:
        """Reject client-side polling: this adapter is enqueue-only.

        Celery delivers tasks to its own worker processes, which run the
        registered task and dequeue/ack against the broker internally. HydraMind
        never polls Celery from the client side, so returning ``None`` here would
        silently make ``QueueExecutionHost`` idle-loop forever. We fail loud
        instead.

        :raises NotImplementedError: always.
        """
        raise NotImplementedError(
            "CeleryQueueAdapter is enqueue-only: Celery uses worker-side push, so "
            "dequeue/ack happen inside the Celery worker process via the registered "
            "task, not on the HydraMind client. For poll-based execution with "
            "QueueExecutionHost, use the in-memory adapter (or another poll-based "
            "adapter) instead."
        )

    async def ack(self, message: QueueMessage) -> None:
        self._reject_if_closed()
        return

    async def nack(
        self, message: QueueMessage, *, retry: bool = True
    ) -> None:
        self._reject_if_closed()
        if retry:
            self._send(message.session_id, dict(message.metadata), message.attempt + 1)

    async def pending(self) -> int:
        self._reject_if_closed()
        return self._pending_sync()

    async def close(self) -> None:
        self._closed = True
        close = getattr(self._app, "close", None)
        if callable(close):
            close()

    def _send(
        self,
        session_id: str,
        metadata: dict[str, Any],
        attempt: int,
    ) -> QueueMessage:
        metadata = dict(metadata)
        metadata["attempt"] = attempt
        result = self._app.send_task(
            self._task_name,
            args=[session_id],
            kwargs={"metadata": metadata},
            queue=self._queue,
        )
        handle = str(getattr(result, "id", "") or f"celery-{uuid.uuid4().hex[:12]}")
        metadata["celery_task_id"] = handle
        return QueueMessage(
            session_id=session_id,
            attempt=attempt,
            metadata=metadata,
            handle=handle,
        )

    def _pending_sync(self) -> int:
        control = getattr(self._app, "control", None)
        inspect_factory = getattr(control, "inspect", None)
        if not callable(inspect_factory):
            return 0
        inspector = inspect_factory()
        total = 0
        for method_name in ("active", "reserved", "scheduled"):
            method = getattr(inspector, method_name, None)
            if not callable(method):
                continue
            snapshot = method() or {}
            if isinstance(snapshot, dict):
                total += sum(len(items) for items in snapshot.values() if isinstance(items, list))
        return total

    def _reject_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError(f"{self.name!r} queue is closed")


def _create_celery_app(
    *,
    broker_url: str | None,
    result_backend: str | None,
) -> Any:
    try:
        celery = importlib.import_module("celery")
    except ImportError as exc:
        raise RuntimeError(
            "CeleryQueueAdapter requires optional dependency hydramind[celery]"
        ) from exc
    celery_factory = cast(Any, celery).Celery
    return celery_factory("hydramind", broker=broker_url, backend=result_backend)
