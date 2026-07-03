"""Emitter — concurrent dispatch of ObservationEvents to a set of Observers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from hydramind.observability.events import ObservationEvent


@runtime_checkable
class Observer(Protocol):
    """Subscriber that consumes events. May be sync or async."""

    async def on_event(self, event: ObservationEvent) -> None: ...


@runtime_checkable
class CriticalObserver(Protocol):
    """An observer whose failure must surface, not be swallowed as telemetry.

    Observers that write runtime-influencing durable state (e.g. prompt-affecting
    memory, S4a/F6) set ``critical = True``. The emitter still runs every
    observer concurrently (telemetry isolation preserved), but if any critical
    observer raised, it re-raises an aggregated ``CriticalObserverError`` so the
    failure reaches the runtime instead of being silently dropped.
    """

    critical: bool

    async def on_event(self, event: ObservationEvent) -> None: ...


def _is_critical(observer: object) -> bool:
    return bool(getattr(observer, "critical", False))


class CriticalObserverError(RuntimeError):
    """One or more critical observers failed while handling an event.

    Surfaces critical-observer failures (e.g. a failed prompt-affecting memory
    write) to the runtime. Telemetry-only observer failures are not aggregated
    here — they stay swallowed/logged.
    """

    def __init__(self, event_kind: object, failures: list[BaseException]) -> None:
        self.event_kind = event_kind
        self.failures = failures
        detail = "; ".join(f"{type(e).__name__}: {e}" for e in failures)
        super().__init__(
            f"critical observer(s) failed on event {event_kind}: {detail}"
        )


_logger = logging.getLogger("hydramind.observability")


class Emitter:
    """Fan-out dispatcher.

    Observers run concurrently per event. A telemetry observer raising an
    exception is logged and isolated — it never blocks other observers or
    fails the caller (the runtime must continue even if telemetry breaks).

    Critical observers (``critical = True``, e.g. prompt-affecting memory
    writers, S4a/F6) are different: they still run alongside everything else,
    but if one raises, ``emit`` aggregates the critical failures and raises
    ``CriticalObserverError`` after the gather, so a failed runtime-influencing
    write surfaces instead of being silently swallowed.
    """

    def __init__(self, observers: Iterable[Observer] | None = None) -> None:
        self._observers: list[Observer] = list(observers or ())
        self._closed = False

    def add(self, observer: Observer) -> None:
        if self._closed:
            raise RuntimeError("Emitter is closed; cannot add observers")
        self._observers.append(observer)

    def observers(self) -> tuple[Observer, ...]:
        return tuple(self._observers)

    async def emit(self, event: ObservationEvent) -> None:
        if self._closed or not self._observers:
            return
        results = await asyncio.gather(
            *(self._safe_on_event(o, event) for o in self._observers),
            return_exceptions=True,
        )
        critical_failures: list[BaseException] = []
        for observer, result in zip(self._observers, results, strict=True):
            if isinstance(result, BaseException):
                _logger.exception(
                    "observer %r raised on event %s: %s",
                    type(observer).__name__,
                    event.kind,
                    result,
                )
                if _is_critical(observer):
                    critical_failures.append(result)
        if critical_failures:
            raise CriticalObserverError(event.kind, critical_failures)

    async def close(self) -> None:
        self._closed = True
        self._observers.clear()

    @staticmethod
    async def _safe_on_event(observer: Observer, event: ObservationEvent) -> None:
        await observer.on_event(event)
