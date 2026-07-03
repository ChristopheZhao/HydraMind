"""Contract tests for the ModelProvider boundary (S1, ADR-0010).

The ModelProvider contract exposes ONLY model-access surface. It must not carry
any harness surface (subagent/compaction/capabilities), and role-route must win
over ModelHint for model selection (the single F10 precedence rule).
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.harness import (
    InvocationResult,
    LLMProvider,
    Message,
    MessageRole,
    ModelHint,
    ModelProvider,
    OpenAICompatibleProvider,
    StopReason,
    Usage,
)
from hydramind.harness.routing import ModelRouter


def _env() -> dict[str, str]:
    return {
        "DEEPSEEK_API_KEY": "ds-key",
        "DEEPSEEK_MODEL": "deepseek-v4-pro",
        "KIMI_API_KEY": "kimi-key",
        "KIMI_MODEL": "kimi-k2.6",
        "GLM_API_KEY": "glm-key",
        "GLM_MODEL": "glm-5.1",
    }


def test_llm_provider_is_alias_of_model_provider() -> None:
    assert LLMProvider is ModelProvider


def test_provider_exposes_no_harness_surface() -> None:
    provider = OpenAICompatibleProvider(router=ModelRouter.from_env(_env()))

    assert not hasattr(provider, "spawn_subagent")
    assert not hasattr(provider, "compact_context")
    assert not hasattr(provider, "capabilities")
    # The abstract contract itself declares no harness surface either.
    for name in ("spawn_subagent", "compact_context", "capabilities"):
        assert not hasattr(ModelProvider, name)


def test_provider_can_be_constructed_without_harness_machinery() -> None:
    # A provider needs no harness/subagent wiring to be usable.
    provider = OpenAICompatibleProvider(router=ModelRouter.from_env(_env()))
    assert isinstance(provider, ModelProvider)
    assert provider.name == "openai-compatible"


@pytest.mark.asyncio
async def test_provider_complete_returns_invocation_result_with_model_and_usage() -> None:
    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        return {
            "model": payload["model"],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    result = await provider.complete(
        [Message(role=MessageRole.USER, content="hello")],
        role="planner",
    )

    assert isinstance(result, InvocationResult)
    assert result.stop_reason is StopReason.END_TURN
    assert result.model_id == "kimi-k2.6"
    assert isinstance(result.usage, Usage)
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 3


def test_route_pinned_model_wins_over_model_hint() -> None:
    # Pin a concrete model X on the executor role; the hint must not override it.
    env = {**_env(), "HYDRAMIND_EXECUTOR_MODEL": "glm-pinned-X"}
    provider = OpenAICompatibleProvider(router=ModelRouter.from_env(env))

    assert (
        provider.resolve_model("executor", model_hint=ModelHint.POWERFUL)
        == "glm-pinned-X"
    )
    assert (
        provider.resolve_model("executor", model_hint=ModelHint.FAST)
        == "glm-pinned-X"
    )


def test_context_limit_is_part_of_the_contract() -> None:
    provider = OpenAICompatibleProvider(router=ModelRouter.from_env(_env()))

    # planner -> kimi (262_000 default context tokens from routing.from_env)
    assert provider.context_limit("planner") == 262_000
    assert provider.context_limit("executor") == 200_000
