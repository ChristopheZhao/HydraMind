"""Deterministic REPLAY / TEST support for HydraMind.

This module lives in ``hydramind.testing`` on purpose (ADR-0010 / PLAN-20260618-001
S6, basis ``docs/architecture/95-execution-harness-correction.md`` §F4 +
"Move Out of Normal Runtime"). It supplies a deterministic, in-process
``MockProvider`` (fingerprinted record/replay + FIFO scripted turns) for:

- contract tests;
- control/queue/tool/state plumbing tests;
- fixture/trace regression;
- explicitly offline examples where no live model is allowed.

Explicit non-agent semantics: ``MockProvider`` makes NO model decisions and NO
network calls. It is therefore NOT production model access and is NOT live-agent
or live-MAS acceptance evidence. Replaying a fixture cannot validate prompt
adherence, semantic output quality, live tool-choice, provider drift,
cost/latency, or real failure recovery. It is replay/test evidence only.

It is intentionally absent from the production provider factory. The explicitly
offline ``--provider mock`` / ``--mock-fixture`` replay path imports it from
here.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hydramind.harness.base import (
    InvocationResult,
    Message,
    ModelHint,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)
from hydramind.harness.provider import ModelProvider


def invocation_fingerprint(
    messages: list[Message],
    *,
    tools: list[ToolSpec] | None = None,
    system: str | None = None,
    role: str | None = None,
    model_hint: ModelHint = ModelHint.BALANCED,
    max_tokens: int | None = None,
) -> str:
    """Deterministic SHA-256 fingerprint of a provider completion input.

    Stable across processes (unlike Python's salted ``hash()``): the input is
    canonicalized to JSON with sorted keys, then hashed. Keys covered: each
    message's role/content/name/tool_calls/tool_results, the system prompt, the
    declared tools (name + schema), the routing role, the model hint, and
    max_tokens. This is the key for ``MockProvider`` record/replay.
    """

    canonical = {
        "messages": [_message_key(m) for m in messages],
        "system": system,
        "tools": [_tool_key(t) for t in (tools or ())],
        "role": role,
        "model_hint": model_hint.value,
        "max_tokens": max_tokens,
    }
    serialized = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _message_key(message: Message) -> dict[str, Any]:
    return {
        "role": message.role.value,
        "content": message.content,
        "name": message.name,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
        ],
        "tool_results": [
            {"tool_call_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
            for r in message.tool_results
        ],
    }


def _tool_key(tool: ToolSpec) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


class ScriptedTurn(BaseModel):
    """One pre-baked response for ``MockProvider.complete``.

    Tests prime ``MockProvider`` with a queue of these to assert workflows
    without any LLM in the loop.
    """

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: StopReason = StopReason.END_TURN
    usage: Usage = Field(default_factory=Usage)
    model_id: str = "mock-balanced"


class MockProvider(ModelProvider):
    """Deterministic replay/test double. Scripted responses, no network, no model.

    NON-AGENT semantics: this is testing/replay support (``hydramind.testing``),
    not production model access and not live-agent/live-MAS acceptance evidence.
    See the module docstring.
    """

    name = "mock"

    def __init__(
        self,
        scripted: Iterable[ScriptedTurn] | None = None,
        *,
        default_subagent_summary: str = "subagent finished",
        record: bool = False,
        replay: Mapping[str, InvocationResult] | None = None,
    ) -> None:
        """In-process provider.

        Three response strategies, checked in order on each ``complete``:

        1. **Replay** (``replay`` mapping): if the input fingerprint is present,
           return the recorded :class:`InvocationResult` — order-INDEPENDENT,
           keyed by :func:`invocation_fingerprint`. A replay miss falls through.
        2. **FIFO scripted** (``scripted`` deque): the legacy ordered path,
           consumed left-to-right via :meth:`queue`. Unchanged.
        3. **Echo fallback**: ``"mock-echo: <last user content>"``.

        Set ``record=True`` to capture every (input fingerprint → result) into
        :attr:`recordings`, then :meth:`save_fixture` / :meth:`from_fixture` to
        round-trip a deterministic replay corpus.
        """
        self._scripted: deque[ScriptedTurn] = deque(scripted or ())
        self._default_subagent_summary = default_subagent_summary
        self._invocations: list[dict[str, Any]] = []
        self._record = record
        self._replay: dict[str, InvocationResult] = dict(replay or {})
        self._recordings: dict[str, InvocationResult] = {}
        self._closed = False

    def queue(self, *turns: ScriptedTurn) -> None:
        """Append additional scripted turns after construction (FIFO path)."""
        self._scripted.extend(turns)

    @property
    def invocations(self) -> list[dict[str, Any]]:
        """Recorded calls to ``complete``. Useful for test assertions."""
        return list(self._invocations)

    @property
    def recordings(self) -> dict[str, InvocationResult]:
        """Captured (input fingerprint → result) pairs when ``record=True``."""
        return dict(self._recordings)

    def save_fixture(self, path: str | Path) -> None:
        """Serialize captured recordings to a JSON fixture (input-keyed)."""
        payload = {key: result.model_dump(mode="json") for key, result in self._recordings.items()}
        Path(path).write_text(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_fixture(cls, path: str | Path) -> MockProvider:
        """Load an input-keyed JSON fixture into a replay-mode ``MockProvider``."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        replay = {key: InvocationResult.model_validate(value) for key, value in raw.items()}
        return cls(replay=replay)

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
    ) -> InvocationResult:
        if self._closed:
            raise RuntimeError("MockProvider already closed")
        self._invocations.append(
            {
                "messages": list(messages),
                "tools": list(tools or ()),
                "system": system,
                "role": role,
                "model_hint": model_hint,
                "max_tokens": max_tokens,
            }
        )
        scripted = self._next_scripted_or_replay(
            messages,
            tools=tools,
            system=system,
            role=role,
            model_hint=model_hint,
            max_tokens=max_tokens,
            record=self._record,
        )
        if scripted is not None:
            return scripted
        result = self._echo_result(messages, model_hint)
        if self._record:
            fingerprint = invocation_fingerprint(
                messages,
                tools=tools,
                system=system,
                role=role,
                model_hint=model_hint,
                max_tokens=max_tokens,
            )
            self._recordings[fingerprint] = result
        return result

    def _next_scripted_or_replay(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
        record: bool = False,
    ) -> InvocationResult | None:
        """Replay-then-scripted lookup shared by provider and execution runtime.

        Returns the recorded :class:`InvocationResult` for the input fingerprint
        (replay, order-independent), else the next FIFO scripted turn, else
        ``None`` (the caller supplies its own echo fallback). This is the single
        record/replay backbone (S93/S101) consumed by direct calls and by
        harness-owned subagent sends, so they share one scripted deque / replay
        map.
        """

        if self._replay:
            fingerprint = invocation_fingerprint(
                messages,
                tools=tools,
                system=system,
                role=role,
                model_hint=model_hint,
                max_tokens=max_tokens,
            )
            if fingerprint in self._replay:
                return self._replay[fingerprint]
        if self._scripted:
            turn = self._scripted.popleft()
            result = InvocationResult(
                content=turn.content,
                tool_calls=turn.tool_calls,
                stop_reason=turn.stop_reason,
                usage=turn.usage,
                model_id=turn.model_id,
            )
            if record:
                fingerprint = invocation_fingerprint(
                    messages,
                    tools=tools,
                    system=system,
                    role=role,
                    model_hint=model_hint,
                    max_tokens=max_tokens,
                )
                self._recordings[fingerprint] = result
            return result
        return None

    def _echo_result(self, messages: list[Message], model_hint: ModelHint) -> InvocationResult:
        last_user = next(
            (m.content for m in reversed(messages) if m.role.value == "user"),
            "",
        )
        return InvocationResult(
            content=f"mock-echo: {last_user}",
            stop_reason=StopReason.END_TURN,
            model_id=f"mock-{model_hint.value}",
            usage=Usage(input_tokens=len(last_user), output_tokens=len(last_user)),
        )

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        del role
        return f"mock-{model_hint.value}"

    def context_limit(self, role: str | None = None) -> int:
        del role
        return 200_000

    async def close(self) -> None:
        self._closed = True
