"""LoggingObserver — emit one log line per event via stdlib ``logging``."""

from __future__ import annotations

import logging
from typing import Any

from hydramind.observability.events import ObservationEvent


class LoggingObserver:
    """Default observer that writes one structured log line per event.

    Uses stdlib ``logging`` by default; pass a structlog logger if you have it
    and the methods will work identically.
    """

    def __init__(
        self,
        logger: logging.Logger | Any | None = None,
        *,
        level: int = logging.INFO,
    ) -> None:
        self._logger = logger or logging.getLogger("hydramind.events")
        self._level = level

    async def on_event(self, event: ObservationEvent) -> None:
        self._logger.log(
            self._level,
            "hydramind.event",
            extra={
                "kind": event.kind.value,
                "session_id": event.session_id,
                "node_key": event.node_key,
                "actor": event.actor,
                "detail": event.detail,
                "created_at": event.created_at.isoformat(),
            },
        )
