"""QueueAdapter Protocol, capabilities, and QueueMessage wire type."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class QueueCapability(StrEnum):
    """Transport features declared by a queue adapter."""

    ENQUEUE = "enqueue"
    DEQUEUE = "dequeue"
    ACK = "ack"
    NACK = "nack"
    PENDING = "pending"
    CLOSE = "close"


_POLLABLE_DELIVERY_FEATURES = frozenset(
    {
        QueueCapability.DEQUEUE,
        QueueCapability.ACK,
        QueueCapability.NACK,
    }
)


@dataclass(frozen=True)
class QueueCapabilities:
    """Declared queue adapter feature set."""

    features: frozenset[QueueCapability]

    @classmethod
    def enqueue_only(cls) -> QueueCapabilities:
        return cls(
            frozenset(
                {
                    QueueCapability.ENQUEUE,
                    QueueCapability.PENDING,
                    QueueCapability.CLOSE,
                }
            )
        )

    @classmethod
    def pollable_delivery(cls) -> QueueCapabilities:
        return cls(
            frozenset(
                {
                    QueueCapability.ENQUEUE,
                    QueueCapability.DEQUEUE,
                    QueueCapability.ACK,
                    QueueCapability.NACK,
                    QueueCapability.PENDING,
                    QueueCapability.CLOSE,
                }
            )
        )

    def supports(self, *features: QueueCapability) -> bool:
        return all(feature in self.features for feature in features)

    @property
    def supports_pollable_delivery(self) -> bool:
        return _POLLABLE_DELIVERY_FEATURES.issubset(self.features)


class QueueCapabilityError(RuntimeError):
    """Raised when a queue adapter lacks a required declared capability."""


class QueueMessage(BaseModel):
    """One queued unit of work. Carries only the session_id — never payloads."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempt: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    handle: str = Field(
        default="",
        description="adapter-specific handle for ack/nack (e.g. message id, redis token)",
    )


@runtime_checkable
class QueueAdapter(Protocol):
    """Transport for session ids. Workflow state lives elsewhere (SessionStore)."""

    name: str
    capabilities: QueueCapabilities

    async def enqueue(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> QueueMessage: ...

    async def dequeue(
        self, *, timeout: float | None = None
    ) -> QueueMessage | None:
        """Block up to ``timeout`` seconds; return None on timeout."""

    async def ack(self, message: QueueMessage) -> None: ...

    async def nack(
        self, message: QueueMessage, *, retry: bool = True
    ) -> None: ...

    async def pending(self) -> int: ...

    async def close(self) -> None: ...
