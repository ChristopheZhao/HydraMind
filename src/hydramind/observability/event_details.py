"""Typed, versioned contracts for load-bearing observation-event detail.

``ObservationEvent.detail`` is an open ``dict[str, Any]`` used by many emit
sites as a free-form debug/diagnostic payload. A subset of those details,
however, is *consumed* (not merely logged) by durable, runtime-influencing
state — today the episodic/agent memory projectors read specific keys out of
``AGENT_MESSAGE_SENT``, ``AGENT_TURN_*``, and ``TOOL_CALL_STARTED`` details and
write them into ``MemoryStore`` records that later feed prompts. Those keys are
therefore a cross-layer evidence contract, not debug payload (see
``docs/architecture/95-execution-harness-correction.md`` §3).

This module gives those load-bearing details a typed Pydantic model with a
``schema_version`` so consumers read through a stable, validated surface instead
of bare ``detail.get(...)`` calls.

Versioning choice: the ``schema_version`` lives on the *detail payload*, not on
``ObservationEvent`` itself. The contract being stabilized is the detail wire
shape consumed by memory/gates/recovery; ``ObservationEvent`` is an open
envelope whose ``detail`` varies per kind, so per-detail versioning is the
correct granularity. Each typed model carries its own ``schema_version`` default
and validates incoming versions explicitly in ``from_event_detail``.

The models are ``frozen`` and ``extra="allow"`` so that (a) emit sites may add
extra keys without breaking parsing, and (b) unknown future keys do not crash
older consumers. Existing detail keys are never removed — these models *add*
typing + ``schema_version`` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict

EVENT_DETAIL_SCHEMA_VERSION = 1
"""Current schema version for load-bearing consumed event-detail payloads."""


class _VersionedEventDetail(BaseModel):
    """Base for typed, versioned, load-bearing event-detail contracts."""

    model_config = ConfigDict(frozen=True, extra="allow")

    schema_version: int = EVENT_DETAIL_SCHEMA_VERSION

    @classmethod
    def from_event_detail(cls, detail: Mapping[str, Any]) -> Self:
        """Validate ``detail`` into the typed contract.

        Accepts any payload whose ``schema_version`` is at or below the current
        ``EVENT_DETAIL_SCHEMA_VERSION`` (a missing version is treated as the
        current version for back-compat with pre-S3a emit sites). A *newer*
        version is rejected loudly rather than silently mis-read — a future
        upgrade must add an explicit migration branch here before relaxing this.
        """

        version = detail.get("schema_version", EVENT_DETAIL_SCHEMA_VERSION)
        if isinstance(version, int) and version > EVENT_DETAIL_SCHEMA_VERSION:
            raise ValueError(
                f"{cls.__name__}: unsupported event-detail schema_version "
                f"{version} (max supported {EVENT_DETAIL_SCHEMA_VERSION}); "
                "add an explicit migration before consuming newer payloads"
            )
        # Future: branch per `version` here to upgrade older payloads.
        return cls.model_validate(dict(detail))


class AgentMessageSentDetail(_VersionedEventDetail):
    """Typed contract for ``AGENT_MESSAGE_SENT`` detail consumed by memory.

    Read by ``AgentTurnMemoryObserver`` to write per-agent turn records.
    """

    interaction_id: str | None = None
    turn_index: int | None = None
    team_id: str | None = None
    role: str | None = None
    topology: str | None = None
    workspace_id: str | None = None
    content_preview: str | None = None


class AgentTurnDetail(_VersionedEventDetail):
    """Typed contract for the per-turn detail base (``team_turn_detail``).

    Read by ``build_episode_summary`` / ``_agent_dimension`` for the ordered
    agent/turn dimension (``turn_index`` per acting member).
    """

    interaction_id: str | None = None
    turn_index: int | None = None
    team_id: str | None = None
    role: str | None = None
    topology: str | None = None
    workspace_id: str | None = None


class ToolCallStartedDetail(_VersionedEventDetail):
    """Typed contract for ``TOOL_CALL_STARTED`` detail consumed by memory.

    Read by ``build_episode_summary`` for the episode ``tool_names`` dimension.
    """

    tool_name: str | None = None
    round: int | None = None
    tool_call_id: str | None = None


__all__ = [
    "EVENT_DETAIL_SCHEMA_VERSION",
    "AgentMessageSentDetail",
    "AgentTurnDetail",
    "ToolCallStartedDetail",
]
