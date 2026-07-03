"""Observability — typed events emitted by the runtime, consumed by Observers.

See ``docs/architecture/50-memory-and-observability.md`` §2.
"""

from hydramind.observability.emitter import (
    CriticalObserver,
    CriticalObserverError,
    Emitter,
    Observer,
)
from hydramind.observability.events import (
    ObservationEvent,
    ObservationEventKind,
)
from hydramind.observability.observers.collecting import ListObserver
from hydramind.observability.observers.jsonl import JsonlObserver
from hydramind.observability.observers.logging_observer import LoggingObserver
from hydramind.observability.observers.otel import OpenTelemetryObserver
from hydramind.observability.redaction import (
    compact_text,
    redact_text,
    redact_value,
    redacted_tool_result_preview,
)

__all__ = [
    "CriticalObserver",
    "CriticalObserverError",
    "Emitter",
    "JsonlObserver",
    "ListObserver",
    "LoggingObserver",
    "ObservationEvent",
    "ObservationEventKind",
    "Observer",
    "OpenTelemetryObserver",
    "compact_text",
    "redact_text",
    "redact_value",
    "redacted_tool_result_preview",
]
