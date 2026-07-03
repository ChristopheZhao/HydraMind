"""OpenAI-compatible model provider (DeepSeek/Kimi/GLM).

``OpenAICompatibleProvider`` implements the ``ModelProvider`` contract: it owns
the ``ModelRouter``, transport, ``/chat/completions`` serialization, response
parsing, and usage/model-id extraction. It carries NO harness surface.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from hydramind.harness.base import InvocationResult, Message, ModelHint, ToolSpec
from hydramind.harness.openai_compatible_support import (
    chat_completions_url,
    parse_completion,
    provider_tool_name_map,
    reverse_tool_name_map,
    to_openai_messages,
    tool_to_openai,
)
from hydramind.harness.provider import ModelProvider
from hydramind.harness.routing import ModelRouter, ResolvedRoute

ChatTransport: TypeAlias = Callable[
    [str, dict[str, str], dict[str, Any], float],
    Awaitable[dict[str, Any]],
]


class OpenAICompatibleProvider(ModelProvider):
    """ModelProvider over OpenAI-compatible ``/chat/completions`` APIs.

    Owns the ``ModelRouter`` and all transport/serialization/parse logic. The
    route-resolved model is authoritative; ``model_hint`` is ignored here because
    role routes already pin provider+model (see ``provider.py`` for the rule).
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        router: ModelRouter | None = None,
        transport: ChatTransport | None = None,
    ) -> None:
        self._router = router or ModelRouter.from_env()
        self._transport = transport or _default_transport

    @property
    def router(self) -> ModelRouter:
        return self._router

    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        # Route is authoritative: route.model (pinned) > provider.default_model.
        # model_hint is intentionally unused — no second selection axis (F10).
        del model_hint
        return self._router.resolve(role).model

    def context_limit(self, role: str | None = None) -> int:
        return self._router.resolve(role).provider.max_context_tokens

    @property
    def max_context_tokens(self) -> int:
        return max(
            (p.max_context_tokens for p in self._router.providers.values()),
            default=128_000,
        )

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
        del model_hint  # route-authoritative; see provider.py precedence rule
        route = self._router.resolve(role)
        if not route.api_key:
            raise RuntimeError(
                f"missing API key for provider {route.provider.name!r}; "
                f"set {route.provider.api_key_env}"
            )
        tool_name_map = provider_tool_name_map(tools or [])
        payload = self._build_payload(
            messages=messages,
            route=route,
            tools=tools,
            system=system,
            max_tokens=max_tokens,
            tool_name_map=tool_name_map,
        )
        endpoint = chat_completions_url(route.provider.base_url)
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        raw = await self._transport(
            endpoint, headers, payload, route.provider.timeout_seconds
        )
        return parse_completion(
            raw,
            provider=route.provider.name,
            tool_name_map=reverse_tool_name_map(tool_name_map),
        )

    @staticmethod
    def _build_payload(
        *,
        messages: list[Message],
        route: ResolvedRoute,
        tools: list[ToolSpec] | None,
        system: str | None,
        max_tokens: int | None,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        names = tool_name_map or {}
        payload: dict[str, Any] = {
            "model": route.model,
            "messages": to_openai_messages(
                messages,
                system=system,
                tool_name_map=names,
            ),
            "stream": False,
        }
        effective_max_tokens = max_tokens or route.max_tokens
        if effective_max_tokens is not None:
            payload["max_tokens"] = effective_max_tokens
        if route.temperature is not None:
            payload["temperature"] = route.temperature
        if tools:
            payload["tools"] = [tool_to_openai(t, tool_name=names[t.name]) for t in tools]
            payload["tool_choice"] = "auto"
        if route.thinking and not tools:
            payload["thinking"] = {"type": "enabled"}
        if route.reasoning_effort and not tools:
            payload["reasoning_effort"] = route.reasoning_effort
        return payload


async def _default_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_post_json_sync, url, headers, payload, timeout),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"OpenAI-compatible API request timed out after {timeout:g}s"
        ) from exc


def _post_json_sync(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible API error {exc.code}: {detail}") from exc
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI-compatible API returned a non-object JSON response")
    return parsed
