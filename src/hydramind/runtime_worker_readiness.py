"""Distributed worker readiness preflight helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hydramind.queue import QueueAdapter


class WorkerReadinessSnapshot(BaseModel):
    """Read-only worker launch suitability preflight."""

    model_config = ConfigDict(frozen=True)

    worker_id: str
    queue_name: str
    queue_capabilities: tuple[str, ...]
    queue_pollable: bool
    queue_distribution: str
    session_store_kind: str
    session_store_path: str | None = None
    session_store_durability: str
    session_store_persistent: bool
    session_store_cas_capable: bool
    local_worker_ready: bool
    distributed_worker_ready: bool
    ready: bool
    reasons: tuple[str, ...]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def worker_readiness(
    queue: QueueAdapter,
    *,
    session_store_kind: str = "sqlite",
    store_path: str | Path | None = None,
    worker_id: str = "worker-readiness",
) -> WorkerReadinessSnapshot:
    """Return a read-only distributed worker preflight snapshot."""

    queue_capabilities = tuple(
        sorted(feature.value for feature in queue.capabilities.features)
    )
    queue_pollable = queue.capabilities.supports_pollable_delivery
    queue_distribution = _queue_distribution(queue)
    store = _session_store_readiness(session_store_kind, store_path)
    local_worker_ready = queue_pollable and store.local_ready
    distributed_worker_ready = (
        queue_pollable
        and queue_distribution == "broker"
        and store.distributed_ready
    )
    reasons = _readiness_reasons(
        queue_pollable=queue_pollable,
        queue_distribution=queue_distribution,
        store=store,
        local_worker_ready=local_worker_ready,
        distributed_worker_ready=distributed_worker_ready,
    )
    return WorkerReadinessSnapshot(
        worker_id=worker_id,
        queue_name=queue.name,
        queue_capabilities=queue_capabilities,
        queue_pollable=queue_pollable,
        queue_distribution=queue_distribution,
        session_store_kind=store.kind,
        session_store_path=store.path,
        session_store_durability=store.durability,
        session_store_persistent=store.persistent,
        session_store_cas_capable=store.cas_capable,
        local_worker_ready=local_worker_ready,
        distributed_worker_ready=distributed_worker_ready,
        ready=distributed_worker_ready,
        reasons=reasons,
    )


def _queue_distribution(queue: QueueAdapter) -> str:
    if queue.name == "redis-stream":
        return "broker"
    if queue.name == "in-memory":
        return "process_local"
    return "unknown"


@dataclass(frozen=True)
class _SessionStoreReadiness:
    kind: str
    path: str | None
    durability: str
    persistent: bool
    cas_capable: bool
    local_ready: bool
    distributed_ready: bool
    reason: str


def _session_store_readiness(
    kind: str,
    path: str | Path | None,
) -> _SessionStoreReadiness:
    normalized = kind.lower().replace("-", "_")
    path_value = None if path is None else str(path)
    if normalized in {"memory", "in_memory"}:
        return _SessionStoreReadiness(
            kind=normalized,
            path=path_value,
            durability="process_local",
            persistent=False,
            cas_capable=False,
            local_ready=True,
            distributed_ready=False,
            reason="in_memory_session_store_is_process_local",
        )
    if normalized == "sqlite":
        if path_value is None:
            return _SessionStoreReadiness(
                kind=normalized,
                path=None,
                durability="missing_path",
                persistent=False,
                cas_capable=False,
                local_ready=False,
                distributed_ready=False,
                reason="sqlite_session_store_requires_store_path",
            )
        if path_value == ":memory:":
            return _SessionStoreReadiness(
                kind=normalized,
                path=path_value,
                durability="process_local",
                persistent=False,
                cas_capable=False,
                local_ready=True,
                distributed_ready=False,
                reason="sqlite_memory_database_is_process_local",
            )
        return _SessionStoreReadiness(
            kind=normalized,
            path=path_value,
            durability="persistent",
            persistent=True,
            cas_capable=True,
            local_ready=True,
            distributed_ready=True,
            reason="sqlite_session_store_is_persistent_cas_capable",
        )
    return _SessionStoreReadiness(
        kind=normalized,
        path=path_value,
        durability="unknown",
        persistent=False,
        cas_capable=False,
        local_ready=False,
        distributed_ready=False,
        reason="unknown_session_store_kind",
    )


def _readiness_reasons(
    *,
    queue_pollable: bool,
    queue_distribution: str,
    store: _SessionStoreReadiness,
    local_worker_ready: bool,
    distributed_worker_ready: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if queue_pollable:
        reasons.append("queue_supports_pollable_delivery")
    else:
        reasons.append("queue_requires_dequeue_ack_nack")
    if queue_distribution == "broker":
        reasons.append("queue_is_broker_backed")
    elif queue_distribution == "process_local":
        reasons.append("queue_is_process_local")
    else:
        reasons.append("queue_distribution_is_unknown")
    reasons.append(store.reason)
    if local_worker_ready:
        reasons.append("local_worker_preflight_ready")
    if distributed_worker_ready:
        reasons.append("distributed_worker_preflight_ready")
    else:
        reasons.append("distributed_worker_preflight_not_ready")
    return tuple(reasons)


__all__ = ["WorkerReadinessSnapshot", "worker_readiness"]
