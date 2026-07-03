"""OpenAI-compatible wire payload and response helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from hydramind.harness.base import (
    InvocationResult,
    Message,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)


def chat_completions_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    return f"{stripped}/chat/completions"


def to_openai_messages(
    messages: list[Message],
    *,
    system: str | None,
    tool_name_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    names = tool_name_map or {}
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        if message.tool_results:
            for result in message.tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": result.content,
                    }
                )
            continue
        item: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.name:
            item["name"] = message.name
        if message.tool_calls:
            item["content"] = ""
            if message.reasoning_content:
                item["reasoning_content"] = message.reasoning_content
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": names.get(call.name, provider_tool_name(call.name)),
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        out.append(item)
    return out


def tool_to_openai(tool: ToolSpec, *, tool_name: str | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool_name or provider_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.input_schema
            or {"type": "object", "properties": {}, "additionalProperties": True},
        },
    }


def parse_completion(
    raw: dict[str, Any],
    *,
    provider: str,
    tool_name_map: dict[str, str] | None = None,
) -> InvocationResult:
    choices = raw.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    names = tool_name_map or {}
    tool_calls = tuple(
        parse_tool_call(c, tool_name_map=names)
        for c in message.get("tool_calls") or ()
    )
    finish_reason = str(choice.get("finish_reason") or "")
    content = str(message.get("content") or "")
    reasoning = str(message.get("reasoning_content") or "")
    if not content and not tool_calls and message.get("reasoning_content"):
        content = reasoning
    return InvocationResult(
        content=content,
        tool_calls=tool_calls,
        stop_reason=map_stop_reason(finish_reason, tool_calls),
        usage=parse_usage(raw.get("usage") or {}),
        model_id=str(raw.get("model") or ""),
        reasoning_content=reasoning or None,
        raw={
            "provider": provider,
            "response": raw,
        },
    )


def reasoning_content(result: InvocationResult) -> str | None:
    value = result.reasoning_content
    return value if isinstance(value, str) and value else None


def parse_tool_call(
    raw: dict[str, Any],
    *,
    tool_name_map: dict[str, str] | None = None,
) -> ToolCall:
    function = raw.get("function") or {}
    args = function.get("arguments") or {}
    if isinstance(args, str):
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            parsed = {"_raw": args}
    elif isinstance(args, dict):
        parsed = args
    else:
        parsed = {"_raw": args}
    provider_name = str(function.get("name") or "")
    internal_name = (tool_name_map or {}).get(provider_name, provider_name)
    return ToolCall(
        id=str(raw.get("id") or ""),
        name=internal_name,
        arguments=parsed,
    )


_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def provider_tool_name_map(tools: list[ToolSpec]) -> dict[str, str]:
    used: set[str] = set()
    names: dict[str, str] = {}
    for tool in tools:
        candidate = provider_tool_name(tool.name)
        unique = candidate
        suffix = 2
        while unique in used:
            unique = f"{candidate}_{suffix}"
            suffix += 1
        used.add(unique)
        names[tool.name] = unique
    return names


def reverse_tool_name_map(names: dict[str, str]) -> dict[str, str]:
    return {provider_name: internal_name for internal_name, provider_name in names.items()}


def provider_tool_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    if not cleaned:
        cleaned = "tool"
    if cleaned[0].isdigit():
        cleaned = f"tool_{cleaned}"
    if _OPENAI_TOOL_NAME_RE.fullmatch(cleaned):
        return cleaned
    return "tool"


def map_stop_reason(finish_reason: str, tool_calls: tuple[ToolCall, ...]) -> StopReason:
    if tool_calls:
        return StopReason.TOOL_USE
    if finish_reason in {"stop", "end_turn"}:
        return StopReason.END_TURN
    if finish_reason == "length":
        return StopReason.MAX_TOKENS
    if finish_reason:
        return StopReason.STOP_SEQUENCE
    return StopReason.END_TURN


def parse_usage(raw: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or raw.get("output_tokens") or 0),
        cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(raw.get("cache_read_input_tokens") or 0),
    )


__all__ = [
    "chat_completions_url",
    "parse_completion",
    "provider_tool_name_map",
    "reasoning_content",
    "reverse_tool_name_map",
    "to_openai_messages",
    "tool_to_openai",
]
