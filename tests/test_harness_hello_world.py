"""End-to-end smoke: a minimal tool loop using only ModelProvider.

This proves the provider contract is sufficient for an actor pattern without
pulling in the rest of the framework.
"""

from __future__ import annotations

import pytest

from hydramind.harness import (
    Message,
    MessageRole,
    StopReason,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.testing import MockProvider, ScriptedTurn


@pytest.mark.asyncio
async def test_tool_using_agent_minimal_loop() -> None:
    """Minimal agent loop: LLM picks tool → we run it → feed result back → final answer."""
    provider = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(id="t1", name="add", arguments={"a": 2, "b": 3}),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(
                content="The answer is 5.",
                stop_reason=StopReason.END_TURN,
            ),
        ]
    )

    tools = [
        ToolSpec(
            name="add",
            description="Add two integers.",
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        )
    ]

    conversation: list[Message] = [
        Message(role=MessageRole.USER, content="What is 2 + 3?")
    ]

    first = await provider.complete(conversation, tools=tools)
    assert first.stop_reason == StopReason.TOOL_USE
    assert first.tool_calls[0].name == "add"

    args = first.tool_calls[0].arguments
    tool_output = str(int(args["a"]) + int(args["b"]))

    conversation.append(
        Message(
            role=MessageRole.ASSISTANT,
            content=first.content,
            tool_calls=first.tool_calls,
        )
    )
    conversation.append(
        Message(
            role=MessageRole.TOOL,
            tool_results=(
                ToolResultBlock(tool_call_id="t1", content=tool_output),
            ),
        )
    )

    second = await provider.complete(conversation, tools=tools)
    assert second.stop_reason == StopReason.END_TURN
    assert "5" in second.content
    assert len(provider.invocations) == 2
