"""Model-provider substrate and vendor-agnostic wire types."""

from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    StopReason,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
    Usage,
)
from hydramind.harness.claude_sdk import ClaudeAgentSDKProvider
from hydramind.harness.factory import create_model_provider_from_env
from hydramind.harness.openai_compatible import OpenAICompatibleProvider
from hydramind.harness.provider import LLMProvider, ModelProvider
from hydramind.harness.routing import ModelRouter, ProviderProfile, RoleRoute, RouteRole

__all__ = [
    "ClaudeAgentSDKProvider",
    "InvocationResult",
    "LLMProvider",
    "Message",
    "MessageRole",
    "ModelHint",
    "ModelProvider",
    "ModelRouter",
    "OpenAICompatibleProvider",
    "ProviderProfile",
    "RoleRoute",
    "RouteRole",
    "StopReason",
    "ToolCall",
    "ToolResultBlock",
    "ToolSpec",
    "Usage",
    "create_model_provider_from_env",
]
