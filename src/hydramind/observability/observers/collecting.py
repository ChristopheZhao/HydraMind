"""ListObserver — collect events into an in-memory list (for tests)."""

from __future__ import annotations

from hydramind.observability.events import ObservationEvent


class ListObserver:
    """Append every received event to a list. Useful for assertions in tests."""

    def __init__(self) -> None:
        self.events: list[ObservationEvent] = []

    async def on_event(self, event: ObservationEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

    def kinds(self) -> list[str]:
        return [e.kind.value for e in self.events]
