"""ClaudeAgentSDKProvider — wraps the official ``claude-agent-sdk`` package.

The SDK is an optional install (``pip install hydramind[claude]``). Imports are
lazy so this module is safely importable without the SDK present; the actual
``claude_agent_sdk`` import only fires on first ``complete``.

P0 scope: minimal viable wrapper. Streaming, hooks, and skills are P1+.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hydramind.harness.base import (
    InvocationResult,
    Message,
    ModelHint,
    StopReason,
    ToolSpec,
    Usage,
)
from hydramind.harness.provider import ModelProvider

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


_MODEL_MAP: dict[ModelHint, str] = {
    ModelHint.FAST: "claude-haiku-4-5",
    ModelHint.BALANCED: "claude-sonnet-4-6",
    ModelHint.POWERFUL: "claude-opus-4-7",
}


def _compose_prompt(messages: list[Message], *, system: str | None) -> str:
    """Compose a faithful prompt from system + the full transcript.

    The wrapped ``sdk.query`` only accepts ``prompt=`` (single string), so the
    full message history and the system prompt are flattened into one labeled
    transcript rather than silently dropped (DEV-35). Each message is prefixed by
    its speaker name (``Message.name``) when present, else its role.
    """

    parts: list[str] = []
    if system:
        parts.append(f"[system] {system}")
    for message in messages:
        speaker = message.name or message.role.value
        if message.tool_results:
            for result in message.tool_results:
                parts.append(f"[tool:{result.tool_call_id}] {result.content}")
            continue
        parts.append(f"[{speaker}] {message.content}")
    return "\n".join(parts)


def _require_sdk() -> Any:
    """Lazy import of the SDK; raises a friendly error if not installed."""
    try:
        import claude_agent_sdk as sdk  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "ClaudeAgentSDKProvider requires the optional dependency. "
            "Install with: pip install 'hydramind[claude]'"
        ) from exc
    return sdk


class ClaudeAgentSDKProvider(ModelProvider):
    """ModelProvider implementation over ``claude-agent-sdk``."""

    name = "claude-agent-sdk"

    def __init__(self, *, default_model_hint: ModelHint = ModelHint.BALANCED) -> None:
        self._default_hint = default_model_hint
        self._sdk: Any | None = None

    def _sdk_mod(self) -> Any:
        if self._sdk is None:
            self._sdk = _require_sdk()
        return self._sdk

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        del role
        return _MODEL_MAP[model_hint]

    def context_limit(self, role: str | None = None) -> int:
        del role
        return 200_000

    async def complete(  # pragma: no cover - needs SDK + network
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
    ) -> InvocationResult:
        del tools, role, max_tokens
        sdk = self._sdk_mod()
        prompt = _compose_prompt(messages, system=system)
        text_parts: list[str] = []
        async for chunk in sdk.query(prompt=prompt):
            text_parts.append(str(chunk))
        content = "".join(text_parts)
        return InvocationResult(
            content=content,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            model_id=_MODEL_MAP[model_hint],
            usage=Usage(),
        )

    async def close(self) -> None:
        self._sdk = None
