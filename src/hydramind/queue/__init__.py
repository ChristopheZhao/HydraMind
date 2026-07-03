"""Queue layer — agent-queue decoupling (Differentiation Anchor #3).

See ``docs/architecture/60-queue-adapter.md``.
"""

from hydramind.queue.base import (
    QueueAdapter,
    QueueCapabilities,
    QueueCapability,
    QueueCapabilityError,
    QueueMessage,
)
from hydramind.queue.celery_adapter import CeleryQueueAdapter
from hydramind.queue.in_memory import InMemoryQueueAdapter
from hydramind.queue.redis_stream import RedisStreamQueueAdapter

__all__ = [
    "CeleryQueueAdapter",
    "InMemoryQueueAdapter",
    "QueueAdapter",
    "QueueCapabilities",
    "QueueCapability",
    "QueueCapabilityError",
    "QueueMessage",
    "RedisStreamQueueAdapter",
]
