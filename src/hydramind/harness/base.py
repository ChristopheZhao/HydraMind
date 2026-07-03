"""Provider/model wire types shared across HydraMind layers.

This module is intentionally small: it contains the vendor-agnostic messages,
tool-call/result records, model-routing hint, usage accounting, and completion
result used by ``ModelProvider`` and execution evidence. Execution-harness
policy types such as subagent handles, compaction requests, and capability
declarations live in ``hydramind.orchestration.execution_harness``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelHint(StrEnum):
    """Coarse selection signal. Concrete model id is provider-owned."""

    FAST = "fast"
    BALANCED = "balanced"
    POWERFUL = "powerful"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"


class ToolSpec(BaseModel):
    """Function-tool declaration handed to the model provider."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema draft-2020-12 for tool arguments.",
    )


class ToolCall(BaseModel):
    """A single tool invocation produced by the model."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultBlock(BaseModel):
    """A tool's output, threaded back into the next ``invoke`` call."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """Cross-layer message wire type. Vendor-agnostic by design."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResultBlock, ...] = ()
    name: str | None = None  # optional speaker name (for multi-agent transcripts)
    reasoning_content: str | None = None


class Usage(BaseModel):
    """Token / cost accounting reported by the model provider."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class InvocationResult(BaseModel):
    """Outcome of one provider completion turn.

    Runtime-load-bearing signals are **typed fields**, not ``raw`` keys
    (correction F7, PLAN-20260618-001 S2a). ``reasoning_content`` carries
    provider chain-of-thought forward across tool rounds; ``subagent_id`` and
    ``subagent_summary`` carry subagent-origin attribution. ``raw`` is debug-only
    and MUST NOT carry runtime-load-bearing keys.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: StopReason
    usage: Usage = Field(default_factory=Usage)
    model_id: str | None = None  # concrete model the provider chose
    reasoning_content: str | None = None
    subagent_id: str | None = None
    subagent_summary: str | None = None
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-specific payload for debugging ONLY; never relied on by "
            "callers and MUST NOT carry runtime-load-bearing keys "
            "(reasoning_content/subagent_id/subagent_summary are typed fields)."
        ),
    )
