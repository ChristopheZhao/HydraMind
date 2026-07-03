"""Contract tests for OpenAI-compatible domestic provider routing."""

from __future__ import annotations

from typing import Any

import pytest

import hydramind.harness.openai_compatible as openai_compatible_module
from hydramind.harness import (
    Message,
    MessageRole,
    StopReason,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
    create_model_provider_from_env,
)
from hydramind.harness.openai_compatible import OpenAICompatibleProvider
from hydramind.harness.routing import ModelRouter, RouteRole
from hydramind.orchestration.execution_harness import (
    ProviderExecutionHarnessRuntime,
    SubagentSpawnRequest,
)
from hydramind.orchestration.subagent_spawn import SubagentSpawner


def _env() -> dict[str, str]:
    return {
        "DEEPSEEK_API_KEY": "ds-key",
        "DEEPSEEK_MODEL": "deepseek-v4-pro",
        "KIMI_API_KEY": "kimi-key",
        "KIMI_MODEL": "kimi-k2.6",
        "GLM_API_KEY": "glm-key",
        "GLM_MODEL": "glm-5.1",
    }


def test_model_router_maps_logical_roles_to_default_domestic_providers() -> None:
    router = ModelRouter.from_env(_env())

    orchestrator = router.resolve("orchestrator")
    planner = router.resolve("plan")
    executor = router.resolve("execute")
    reviewer = router.resolve("quality_checker")
    verifier = router.resolve("semantic_verifier")
    compactor = router.resolve("memory_compactor")

    assert orchestrator.role is RouteRole.ORCHESTRATOR
    assert orchestrator.provider.name == "deepseek"
    assert orchestrator.model == "deepseek-v4-pro"
    assert not orchestrator.thinking
    assert planner.role is RouteRole.PLANNER
    assert planner.provider.name == "kimi"
    assert planner.model == "kimi-k2.6"
    assert executor.role is RouteRole.EXECUTOR
    assert executor.provider.name == "glm"
    assert executor.model == "glm-5.1"
    assert reviewer.provider.name == "deepseek"
    assert verifier.role is RouteRole.REVIEWER
    assert verifier.provider.name == "deepseek"
    assert compactor.provider.name == "kimi"


def test_model_router_normalizes_kimi_k2_temperature() -> None:
    env = {
        **_env(),
        "HYDRAMIND_PLANNER_TEMPERATURE": "0.3",
    }
    router = ModelRouter.from_env(env)

    planner = router.resolve("planner")

    assert planner.provider.name == "kimi"
    assert planner.model == "kimi-k2.6"
    assert planner.temperature == 1.0


@pytest.mark.asyncio
async def test_default_transport_enforces_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_finishes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await openai_compatible_module.asyncio.sleep(1)
        return {}

    monkeypatch.setattr(openai_compatible_module.asyncio, "to_thread", never_finishes)

    with pytest.raises(TimeoutError, match=r"timed out after 0\.01s"):
        await openai_compatible_module._default_transport(
            "https://example.test/chat/completions",
            {},
            {},
            0.01,
        )


@pytest.mark.asyncio
async def test_openai_compatible_provider_suppresses_thinking_for_tool_turns() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        calls.append(payload)
        return {
            "model": payload["model"],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(
            {
                **_env(),
                "HYDRAMIND_EXECUTOR_THINKING": "1",
                "HYDRAMIND_EXECUTOR_REASONING_EFFORT": "high",
            }
        ),
        transport=fake_transport,
    )

    await provider.complete(
        [Message(role=MessageRole.USER, content="search")],
        role="executor",
        tools=[ToolSpec(name="search.web", description="search", input_schema={})],
    )
    await provider.complete(
        [Message(role=MessageRole.USER, content="answer")],
        role="executor",
    )

    assert "thinking" not in calls[0]
    assert "reasoning_effort" not in calls[0]
    assert calls[1]["thinking"] == {"type": "enabled"}
    assert calls[1]["reasoning_effort"] == "high"


def test_provider_factory_uses_hydramind_provider_env() -> None:
    openai_provider = create_model_provider_from_env(_env())

    assert isinstance(openai_provider, OpenAICompatibleProvider)


def test_provider_factory_rejects_mock_as_production_provider() -> None:
    # mock is replay/test support (hydramind.testing), not a production provider:
    # the production env factory must refuse it and point at hydramind.testing.
    with pytest.raises(ValueError, match=r"hydramind\.testing"):
        create_model_provider_from_env({"HYDRAMIND_PROVIDER": "mock"})


@pytest.mark.asyncio
async def test_openai_compatible_provider_uses_role_route_in_payload() -> None:
    calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((url, headers, payload, timeout))
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": "planned"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    result = await provider.complete(
        [Message(role=MessageRole.USER, content="draft a plan")],
        system="You are a planner.",
        role="planner",
        tools=[
            ToolSpec(
                name="search",
                description="searches sources",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        ],
    )

    assert result.content == "planned"
    assert result.stop_reason is StopReason.END_TURN
    assert result.model_id == "kimi-k2.6"
    url, headers, payload, timeout = calls[0]
    assert url == "https://api.moonshot.cn/v1/chat/completions"
    assert headers["Authorization"] == "Bearer kimi-key"
    assert timeout == 120.0
    assert payload["model"] == "kimi-k2.6"
    assert payload["messages"][0] == {"role": "system", "content": "You are a planner."}
    assert payload["messages"][1] == {"role": "user", "content": "draft a plan"}
    assert payload["tools"][0]["function"]["name"] == "search"
    assert "thinking" not in payload


@pytest.mark.asyncio
async def test_openai_compatible_provider_parses_tool_calls() -> None:
    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path": "x.txt", "content": "ok"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    result = await provider.complete(
        [Message(role=MessageRole.USER, content="write")],
        role="executor",
    )

    assert result.stop_reason is StopReason.TOOL_USE
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments == {"path": "x.txt", "content": "ok"}
    assert result.model_id == "glm-5.1"


@pytest.mark.asyncio
async def test_openai_compatible_provider_ignores_reasoning_content_for_tool_calls() -> None:
    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "private reasoning",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    result = await provider.complete(
        [Message(role=MessageRole.USER, content="write")],
        role="executor",
    )

    assert result.content == ""
    assert result.stop_reason is StopReason.TOOL_USE
    assert result.tool_calls[0].name == "write_file"
    assert result.reasoning_content == "private reasoning"


@pytest.mark.asyncio
async def test_openai_compatible_provider_maps_dotted_tool_names() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        calls.append(payload)
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-search",
                                "type": "function",
                                "function": {
                                    "name": "search_web",
                                    "arguments": '{"query": "HydraMind"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    result = await provider.complete(
        [Message(role=MessageRole.USER, content="search")],
        role="executor",
        tools=[
            ToolSpec(
                name="search.web",
                description="searches sources",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
        ],
    )

    assert calls[0]["tools"][0]["function"]["name"] == "search_web"
    assert result.tool_calls[0].name == "search.web"
    assert result.tool_calls[0].arguments == {"query": "HydraMind"}


@pytest.mark.asyncio
async def test_openai_compatible_provider_replays_safe_tool_names_in_messages() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        calls.append(payload)
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": '{"done": true}'},
                    "finish_reason": "stop",
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )

    await provider.complete(
        [
            Message(role=MessageRole.USER, content="search"),
            Message(
                role=MessageRole.ASSISTANT,
                reasoning_content="private reasoning",
                tool_calls=(
                    ToolCall(
                        id="call-search",
                        name="search.web",
                        arguments={"query": "HydraMind"},
                    ),
                ),
            ),
        ],
        role="executor",
        tools=[
            ToolSpec(
                name="search.web",
                description="searches sources",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
        ],
    )

    assistant = calls[0]["messages"][1]
    assert assistant["content"] == ""
    assert assistant["reasoning_content"] == "private reasoning"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_web"


@pytest.mark.asyncio
async def test_openai_compatible_subagent_preserves_tool_call_transcript() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        calls.append(payload)
        if len(calls) == 1:
            return {
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-search",
                                    "type": "function",
                                    "function": {
                                        "name": "search_web",
                                        "arguments": '{"query": "HydraMind"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": '{"done": true}'},
                    "finish_reason": "stop",
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )
    spawner = SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider))
    subagent = await spawner.spawn(
        SubagentSpawnRequest(
            role="executor",
            instructions="Use tools when needed.",
            tools=(
                ToolSpec(
                    name="search.web",
                    description="searches sources",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                ),
            ),
        )
    )

    first = await subagent.send(Message(role=MessageRole.USER, content="search"))
    second = await subagent.send(
        Message(
            role=MessageRole.TOOL,
            tool_results=(
                ToolResultBlock(
                    tool_call_id="call-search",
                    content='{"success": true}',
                ),
            ),
        )
    )

    assert first.stop_reason is StopReason.TOOL_USE
    assert second.content == '{"done": true}'
    assistant = calls[1]["messages"][2]
    tool_result = calls[1]["messages"][3]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-search"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_web"
    assert tool_result == {
        "role": "tool",
        "tool_call_id": "call-search",
        "content": '{"success": true}',
    }


@pytest.mark.asyncio
async def test_openai_compatible_provider_requires_provider_key() -> None:
    async def fake_transport(
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise AssertionError("transport should not be called without an API key")

    provider = OpenAICompatibleProvider(
        router=ModelRouter.from_env({}),
        transport=fake_transport,
    )

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        await provider.complete([Message(role=MessageRole.USER, content="go")], role="orchestrator")
