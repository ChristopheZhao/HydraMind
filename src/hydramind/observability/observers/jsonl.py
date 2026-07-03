"""JSONL observer for local trace artifacts."""

from __future__ import annotations

from pathlib import Path

from hydramind.observability.events import ObservationEvent


class JsonlObserver:
    """Append every event as one JSON line.

    This is intentionally simple and local-process oriented. It gives tests,
    smoke runs, and example consoles a durable trace artifact without making
    observability part of RuntimeSession's authoritative state.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def on_event(self, event: ObservationEvent) -> None:
        line = event.model_dump_json()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
