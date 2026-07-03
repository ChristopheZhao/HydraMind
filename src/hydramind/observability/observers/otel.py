"""OpenTelemetryObserver — emit GenAI-semantic-convention spans (optional dep).

The ``opentelemetry`` packages are an extras install:
``pip install hydramind[otel]``. The import is lazy so this module is safely
importable when the extras are absent.
"""

from __future__ import annotations

from typing import Any

from hydramind.observability.events import ObservationEvent


def _require_otel() -> Any:
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "OpenTelemetryObserver requires the optional dependency. "
            "Install with: pip install 'hydramind[otel]'"
        ) from exc
    return trace


class OpenTelemetryObserver:
    """Emit one OTel span per event under tracer ``hydramind``.

    P0 emits zero-duration "event spans" (start + end immediately) since events
    are point-in-time. Span-pair tracing (start node → end node) lands in P1.
    """

    def __init__(self, tracer_name: str = "hydramind") -> None:
        self._tracer_name = tracer_name
        self._trace_mod: Any | None = None

    def _tracer(self) -> Any:
        if self._trace_mod is None:
            self._trace_mod = _require_otel()
        return self._trace_mod.get_tracer(self._tracer_name)

    async def on_event(self, event: ObservationEvent) -> None:  # pragma: no cover - needs OTel
        tracer = self._tracer()
        with tracer.start_as_current_span(event.kind.value) as span:
            span.set_attribute("hydramind.session_id", event.session_id)
            if event.node_key:
                span.set_attribute("hydramind.node_key", event.node_key)
            if event.actor:
                span.set_attribute("hydramind.actor", event.actor)
            for k, v in event.detail.items():
                # OTel attributes must be primitive; stringify everything else.
                if isinstance(v, str | int | float | bool):
                    span.set_attribute(f"hydramind.detail.{k}", v)
                else:
                    span.set_attribute(f"hydramind.detail.{k}", str(v))
