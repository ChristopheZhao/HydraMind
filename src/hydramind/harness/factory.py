"""Model provider construction helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping

from hydramind.harness.claude_sdk import ClaudeAgentSDKProvider
from hydramind.harness.openai_compatible import OpenAICompatibleProvider
from hydramind.harness.provider import ModelProvider
from hydramind.harness.routing import ModelRouter


def create_model_provider_from_env(
    env: Mapping[str, str] | None = None,
) -> ModelProvider:
    """Create the configured production model provider.

    ``HYDRAMIND_PROVIDER`` supports real model providers only:
    - ``openai_compatible`` / ``openai-compatible`` (default)
    - ``claude`` / ``claude_agent_sdk``
    """

    source = os.environ if env is None else env
    provider = _env(source, "HYDRAMIND_PROVIDER", "openai_compatible")
    normalized = provider.lower().replace("-", "_")
    if normalized in {"openai_compatible", "openai"}:
        return OpenAICompatibleProvider(router=ModelRouter.from_env(source))
    if normalized in {"claude", "claude_agent_sdk", "claude_sdk"}:
        return ClaudeAgentSDKProvider()
    if normalized == "mock":
        raise ValueError(
            "HYDRAMIND_PROVIDER='mock' is not a production provider. "
            "Deterministic replay/test support lives in hydramind.testing with "
            "non-agent semantics; offline replay paths construct it explicitly."
        )
    raise ValueError(f"unknown HYDRAMIND_PROVIDER {provider!r}")


def _env(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default
