"""Provider replay and harness-owned interaction primitive contract tests.

Covers the honest ``parent_context.seed_messages`` seam through the execution
harness runtime, ``Message.name`` end-to-end carriage, the input-keyed
``MockProvider`` record/replay backbone (vs the preserved FIFO scripted path),
and provider purity.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.harness import (
    InvocationResult,
    Message,
    MessageRole,
    StopReason,
)
from hydramind.harness.claude_sdk import ClaudeAgentSDKProvider, _compose_prompt
from hydramind.harness.openai_compatible import OpenAICompatibleProvider
from hydramind.harness.routing import ModelRouter
from hydramind.orchestration.execution_harness import (
    ExecutionHarnessFeature,
    ProviderExecutionHarnessRuntime,
    SubagentContext,
    SubagentSpawnRequest,
)
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.testing import MockProvider, ScriptedTurn, invocation_fingerprint

SEED = (
    Message(role=MessageRole.USER, content="prior peer message", name="planner"),
    Message(role=MessageRole.ASSISTANT, content="peer reply", name="researcher"),
)


def _env() -> dict[str, str]:
    return {
        "DEEPSEEK_API_KEY": "ds-key",
        "KIMI_API_KEY": "kimi-key",
        "GLM_API_KEY": "glm-key",
    }


# --- seed_messages honoring through the harness runtime ----------------------


@pytest.mark.asyncio
async def test_mock_subagent_threads_seed_messages() -> None:
    provider = MockProvider()
    spawner = SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider))
    sub = await spawner.spawn(
        SubagentSpawnRequest(
            role="executor",
            instructions="do work",
            parent_context=SubagentContext(seed_messages=SEED),
        )
    )
    # honesty regression guard: the handle must expose the threaded seed messages.
    assert sub.messages == list(SEED)  # type: ignore[attr-defined]
    await sub.send(Message(role=MessageRole.USER, content="go"))
    assert sub.messages[-2].content == "go"  # type: ignore[attr-defined]
    assert sub.messages[-1].content == "mock-echo: go"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_openai_compatible_subagent_threads_seed_messages() -> None:
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
        router=ModelRouter.from_env(_env()),
        transport=fake_transport,
    )
    spawner = SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider))
    sub = await spawner.spawn(
        SubagentSpawnRequest(
            role="executor",
            instructions="do work",
            parent_context=SubagentContext(seed_messages=SEED),
        )
    )
    await sub.send(Message(role=MessageRole.USER, content="go"))
    # The seeded peer messages must be on the wire, in order, before the send.
    wire = calls[0]["messages"]
    contents = [m.get("content") for m in wire]
    assert "prior peer message" in contents
    assert "peer reply" in contents
    # Message.name carried end-to-end through the seam.
    by_content = {m.get("content"): m for m in wire}
    assert by_content["prior peer message"]["name"] == "planner"
    assert by_content["peer reply"]["name"] == "researcher"


@pytest.mark.asyncio
async def test_claude_subagent_threads_seed_messages() -> None:
    provider = ClaudeAgentSDKProvider()

    class _FakeSdk:
        async def query(self, *, prompt: str):
            self.last_prompt = prompt
            yield "answer"

    fake = _FakeSdk()
    provider._sdk = fake  # inject stub, no real SDK
    spawner = SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(provider))
    sub = await spawner.spawn(
        SubagentSpawnRequest(
            role="executor",
            instructions="do work",
            parent_context=SubagentContext(seed_messages=SEED),
        )
    )
    assert sub.messages == list(SEED)  # type: ignore[attr-defined]
    await sub.send(Message(role=MessageRole.USER, content="go"))
    # full transcript composed into the prompt, with speaker names + system.
    assert "[system] do work" in fake.last_prompt
    assert "[planner] prior peer message" in fake.last_prompt
    assert "[researcher] peer reply" in fake.last_prompt
    assert "go" in fake.last_prompt


# --- provider purity + runtime capability honesty ---------------------------


def test_providers_do_not_expose_harness_runtime_surface() -> None:
    providers = (
        MockProvider(),
        OpenAICompatibleProvider(router=ModelRouter.from_env(_env())),
        ClaudeAgentSDKProvider(),
    )
    for provider in providers:
        assert not hasattr(provider, "spawn_subagent")
        assert not hasattr(provider, "compact_context")
        assert not hasattr(provider, "capabilities")


def test_provider_runtime_declares_interaction_truthfully() -> None:
    runtime = ProviderExecutionHarnessRuntime(MockProvider())

    assert runtime.capabilities.supports(ExecutionHarnessFeature.SUBAGENTS)
    assert runtime.capabilities.supports(ExecutionHarnessFeature.TEAM_INTERACTION)
    assert not runtime.capabilities.supports(ExecutionHarnessFeature.COMPACTION)

@pytest.mark.asyncio
async def test_claude_complete_passes_system_and_full_history() -> None:
    provider = ClaudeAgentSDKProvider()

    class _FakeSdk:
        async def query(self, *, prompt: str):
            self.last_prompt = prompt
            yield "done"

    fake = _FakeSdk()
    provider._sdk = fake
    result = await provider.complete(
        [
            Message(role=MessageRole.USER, content="first"),
            Message(role=MessageRole.ASSISTANT, content="second"),
            Message(role=MessageRole.USER, content="third"),
        ],
        system="be helpful",
    )
    assert "[system] be helpful" in fake.last_prompt
    assert "first" in fake.last_prompt
    assert "second" in fake.last_prompt
    assert "third" in fake.last_prompt
    assert result.tool_calls == ()


# --- record / replay backbone ------------------------------------------------


@pytest.mark.asyncio
async def test_record_replay_is_input_keyed_and_order_independent(tmp_path) -> None:
    recorder = MockProvider(
        scripted=[
            ScriptedTurn(content="resA"),
            ScriptedTurn(content="resB"),
            ScriptedTurn(content="resC"),
        ],
        record=True,
    )
    inputs = [
        [Message(role=MessageRole.USER, content="alpha")],
        [Message(role=MessageRole.USER, content="beta")],
        [Message(role=MessageRole.USER, content="gamma")],
    ]
    recorded = [(await recorder.complete(m)).content for m in inputs]
    assert recorded == ["resA", "resB", "resC"]

    fixture = tmp_path / "decisions.json"
    recorder.save_fixture(fixture)

    replay = MockProvider.from_fixture(fixture)
    # Replay in a DIFFERENT order than recorded — proves input-keyed, not FIFO.
    assert (await replay.complete(inputs[2])).content == "resC"
    assert (await replay.complete(inputs[0])).content == "resA"
    assert (await replay.complete(inputs[1])).content == "resB"
    # Repeat a key — still returns the same recorded result.
    assert (await replay.complete(inputs[2])).content == "resC"


@pytest.mark.asyncio
async def test_replay_miss_falls_through_to_echo() -> None:
    known = [Message(role=MessageRole.USER, content="known")]
    key = invocation_fingerprint(known)
    replay = MockProvider(
        replay={key: InvocationResult(content="hit", stop_reason=StopReason.END_TURN)}
    )
    assert (await replay.complete(known)).content == "hit"
    miss = await replay.complete([Message(role=MessageRole.USER, content="unknown")])
    assert miss.content == "mock-echo: unknown"


@pytest.mark.asyncio
async def test_fifo_scripted_path_unchanged() -> None:
    backend = MockProvider(scripted=[ScriptedTurn(content="one"), ScriptedTurn(content="two")])
    first = await backend.complete([Message(role=MessageRole.USER, content="x")])
    second = await backend.complete([Message(role=MessageRole.USER, content="y")])
    assert (first.content, second.content) == ("one", "two")


def test_fingerprint_is_stable_and_input_sensitive() -> None:
    base = [Message(role=MessageRole.USER, content="hi", name="planner")]
    assert invocation_fingerprint(base) == invocation_fingerprint(base)
    assert invocation_fingerprint(base, system="s") != invocation_fingerprint(base)
    other = [Message(role=MessageRole.USER, content="hi", name="researcher")]
    assert invocation_fingerprint(base) != invocation_fingerprint(other)


def test_compose_prompt_drops_nothing() -> None:
    prompt = _compose_prompt(
        [
            Message(role=MessageRole.USER, content="u1", name="planner"),
            Message(role=MessageRole.ASSISTANT, content="a1"),
        ],
        system="sys",
    )
    assert "[system] sys" in prompt
    assert "[planner] u1" in prompt
    assert "[assistant] a1" in prompt
