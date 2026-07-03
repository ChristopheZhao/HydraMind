"""Emitter + Observer tests."""

from __future__ import annotations

import pytest

from hydramind.observability import (
    Emitter,
    ListObserver,
    LoggingObserver,
    ObservationEvent,
    ObservationEventKind,
)


def _event(kind: ObservationEventKind = ObservationEventKind.SESSION_CREATED) -> ObservationEvent:
    return ObservationEvent(kind=kind, session_id="sess-1")


@pytest.mark.asyncio
async def test_emitter_with_no_observers_is_silent() -> None:
    e = Emitter()
    await e.emit(_event())  # must not raise


@pytest.mark.asyncio
async def test_list_observer_records_events() -> None:
    obs = ListObserver()
    e = Emitter([obs])
    await e.emit(_event(ObservationEventKind.SESSION_RUNNING))
    await e.emit(_event(ObservationEventKind.SESSION_COMPLETED))
    assert obs.kinds() == ["session_running", "session_completed"]


@pytest.mark.asyncio
async def test_observer_exception_does_not_propagate() -> None:
    class Boom:
        async def on_event(self, event: ObservationEvent) -> None:
            raise RuntimeError("boom")

    ok = ListObserver()
    e = Emitter([Boom(), ok])
    await e.emit(_event())  # must not raise even though one observer raised
    assert len(ok.events) == 1


@pytest.mark.asyncio
async def test_add_observer_after_construction() -> None:
    obs = ListObserver()
    e = Emitter()
    e.add(obs)
    await e.emit(_event())
    assert len(obs.events) == 1


@pytest.mark.asyncio
async def test_close_blocks_further_adds_and_emits() -> None:
    obs = ListObserver()
    e = Emitter([obs])
    await e.close()
    with pytest.raises(RuntimeError):
        e.add(obs)
    await e.emit(_event())
    assert obs.events == []


@pytest.mark.asyncio
async def test_logging_observer_logs_one_record_per_event(caplog) -> None:
    import logging

    obs = LoggingObserver(level=logging.INFO)
    with caplog.at_level(logging.INFO, logger="hydramind.events"):
        await obs.on_event(_event(ObservationEventKind.SESSION_RUNNING))
    assert any("hydramind.event" in r.message for r in caplog.records)
    record = next(r for r in caplog.records if "hydramind.event" in r.message)
    assert record.kind == "session_running"  # type: ignore[attr-defined]
    assert record.session_id == "sess-1"  # type: ignore[attr-defined]


def test_otel_observer_import_is_lazy() -> None:
    """Module must import even if opentelemetry is absent."""
    from hydramind.observability.observers import otel

    o = otel.OpenTelemetryObserver()
    assert o is not None
