"""Control-owned interaction log persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hydramind.control.models import InteractionLogRecord, RuntimeSession

INTERACTION_LOG_METADATA_KEY = "interaction_log"


def interaction_idempotency_key(record: InteractionLogRecord) -> str:
    """Stable dedupe key for one logical interaction event (S4c).

    Derived from the event's logical identity (not its random ``id``) so a
    replayed turn under duplicate delivery maps to the same key. The execution
    id is included so a legitimately retried node execution (a NEW execution
    after the prior attempt was aborted/recovered) is treated as a fresh turn,
    while a true re-delivery of the SAME execution is deduped.
    """

    turn = "-" if record.turn_index is None else str(record.turn_index)
    actor = record.actor or "-"
    return (
        "interaction:"
        f"{record.interaction_id}:{record.event_kind.value}:"
        f"{record.execution_id}:{turn}:{actor}"
    )


def append_interaction_log(
    session: RuntimeSession,
    record: InteractionLogRecord,
    *,
    now: datetime,
) -> InteractionLogRecord:
    """Append one interaction log record to session metadata."""

    if record.session_id != session.id:
        raise ValueError(
            f"interaction log session_id {record.session_id!r} does not match "
            f"session {session.id!r}"
        )

    metadata = dict(session.metadata)
    log = metadata.get(INTERACTION_LOG_METADATA_KEY)
    if log is None:
        entries: list[dict[str, Any]] = []
    elif isinstance(log, dict):
        raw_entries = log.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ValueError("interaction_log.entries must be a list")
        entries = list(raw_entries)
    else:
        raise ValueError("interaction_log metadata must be an object")

    entries.append(record.model_dump(mode="json"))
    metadata[INTERACTION_LOG_METADATA_KEY] = {"entries": entries}
    session.metadata = metadata
    session.updated_at = now
    return record


__all__ = [
    "INTERACTION_LOG_METADATA_KEY",
    "append_interaction_log",
    "interaction_idempotency_key",
]
