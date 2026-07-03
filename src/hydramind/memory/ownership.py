"""Memory write classification + control-owned prompt-affecting write authority.

S4a / F6 (PLAN-20260618-001 §5 S4, architecture/95 §F6, Phase 3): durable
runtime-influencing memory must have an owner, versioning/append consistency,
and crash/restart semantics — it cannot live as an unowned, swallowed observer
side effect.

``MemoryWriteClass`` classifies every memory write by how it can influence
later execution. Prompt-affecting writes (read back into later prompts via the
``StoreMemoryContextRetriever`` → ``AgentPromptContextBuilder`` path) are routed
through ``MemoryWriteAuthority`` instead of being written directly to the store
as a best-effort observer side effect. The authority:

- stamps an explicit ``write_class`` and a monotonically increasing per-key
  ``version`` (durable-append sequence) onto the stored entry metadata;
- appends durably (one ordered record per write);
- RAISES on store failure rather than swallowing it, so a failed
  prompt-affecting write surfaces to the runtime (the emitter promotes it to a
  ``CriticalObserverError``) instead of silently corrupting later prompts.

The authority owns memory write state only. It never mutates ``RuntimeSession``;
the control single-writer rule for runtime session state is unchanged (AGENTS.md
§3). Telemetry-only writes do not need this path and may stay best-effort.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from hydramind.memory.base import MemoryEntry, MemoryScope, MemoryStore


class MemoryWriteClass(StrEnum):
    """How a memory write can influence later execution.

    The classification, not the store, decides whether a write needs the
    control-owned authorization/versioning path.
    """

    TELEMETRY = "telemetry"            # observability only; never read back
    PROMPT_AFFECTING = "prompt_affecting"      # retrieved back into later prompts
    GATE_AFFECTING = "gate_affecting"          # consumed by gate decisions
    RECOVERY_AFFECTING = "recovery_affecting"  # consumed by recovery/resume


# Write classes whose failure must surface (not be swallowed as telemetry).
_RUNTIME_INFLUENCING: frozenset[MemoryWriteClass] = frozenset(
    {
        MemoryWriteClass.PROMPT_AFFECTING,
        MemoryWriteClass.GATE_AFFECTING,
        MemoryWriteClass.RECOVERY_AFFECTING,
    }
)


def is_runtime_influencing(write_class: MemoryWriteClass) -> bool:
    """True if a failed write of this class must surface, not be swallowed."""

    return write_class in _RUNTIME_INFLUENCING


class MemoryWriteError(RuntimeError):
    """A control-owned prompt/gate/recovery-affecting memory write failed.

    Raised (never swallowed) so the failure surfaces as runtime evidence
    instead of silently dropping a write that feeds later prompts/gates.
    """


class MemoryWriteAuthority:
    """Control-owned authorization + versioning for runtime-influencing writes.

    All prompt/gate/recovery-affecting memory appends go through here. The
    authority is the single owner of these writes: it stamps an explicit
    ``write_class`` and a monotonic per-key ``version`` into the entry metadata
    (durable-append sequence), appends durably, and re-raises any store failure
    as ``MemoryWriteError`` so it cannot be silently lost.

    It does not own or mutate ``RuntimeSession``; it owns only memory write
    state.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        # (scope, scope_id, key) -> last issued version. In-process monotonic
        # sequence; the durable record is the appended entry itself (which the
        # store orders), so a fresh authority re-derives ordering from the
        # store's own append index.
        self._versions: dict[tuple[MemoryScope, str, str], int] = {}

    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        write_class: MemoryWriteClass,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> MemoryEntry:
        """Authorize + durably append one runtime-influencing memory record.

        Stamps ``write_class`` and a monotonic ``memory_write_version`` into the
        entry metadata. Raises ``MemoryWriteError`` if the underlying store
        fails — the failure is never swallowed.

        S4c: when ``idempotency_key`` is supplied, the write is deduped against
        the durable store — a replayed write (duplicate queue delivery /
        re-delivery) whose key already exists under ``(scope, scope_id, key)``
        is a no-op and returns the existing entry, so a prompt-affecting memory
        record is never double-appended. The dedupe reads the durable store
        itself (not in-process state), so it holds across worker restart.
        """

        if not is_runtime_influencing(write_class):
            raise ValueError(
                f"{write_class} is not runtime-influencing; "
                "use the best-effort observer path for telemetry writes"
            )
        if idempotency_key is not None:
            existing = await self._find_by_idempotency_key(
                scope, scope_id, key, idempotency_key
            )
            if existing is not None:
                return existing
        version_key = (scope, scope_id, key)
        version = self._versions.get(version_key, -1) + 1
        merged: dict[str, Any] = dict(metadata or {})
        merged["memory_write_class"] = write_class.value
        merged["memory_write_version"] = version
        if idempotency_key is not None:
            merged["memory_idempotency_key"] = idempotency_key
        try:
            entry = await self._store.append(
                scope,
                scope_id,
                key,
                value,
                metadata=merged,
            )
        except Exception as exc:  # surface, do not swallow
            raise MemoryWriteError(
                f"prompt-affecting memory write failed "
                f"(scope={scope.value} scope_id={scope_id!r} key={key!r}): {exc}"
            ) from exc
        self._versions[version_key] = version
        return entry

    async def _find_by_idempotency_key(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        idempotency_key: str,
    ) -> MemoryEntry | None:
        """Return a prior entry written under ``idempotency_key`` if any (S4c)."""

        try:
            entries = await self._store.scan(scope, scope_id, key_prefix=key)
        except Exception as exc:  # surface, do not swallow
            raise MemoryWriteError(
                f"prompt-affecting memory dedupe scan failed "
                f"(scope={scope.value} scope_id={scope_id!r} key={key!r}): {exc}"
            ) from exc
        for entry in entries:
            if entry.metadata.get("memory_idempotency_key") == idempotency_key:
                return entry
        return None
