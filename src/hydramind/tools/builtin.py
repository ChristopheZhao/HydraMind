"""Built-in P0 tools for examples and smoke tests."""

from __future__ import annotations

from hydramind.tools.artifact_tools import (
    artifact_exists,
    list_artifacts,
    read_json_artifact,
    read_text_artifact,
    write_json_artifact,
    write_text_artifact,
)
from hydramind.tools.base import ToolContext, ToolPolicy, ToolRiskClass
from hydramind.tools.external_tools import (
    DEFAULT_BRAVE_SEARCH_HOST,
    DEFAULT_DOUBAO_IMAGE_ASSET_HOST,
    DEFAULT_DOUBAO_IMAGE_HOST,
    DEFAULT_DOUBAO_IMAGE_MODEL,
    DEFAULT_DOUBAO_IMAGE_SIZE,
    default_external_tool_hosts,
    generate_image,
    search_web,
)
from hydramind.tools.process_tools import (
    PROCESS_TIMEOUT_CLEANUP_SECONDS,
    run_process,
)
from hydramind.tools.registry import ToolRegistry
from hydramind.tools.time_tools import time_now

__all__ = [
    "DEFAULT_BRAVE_SEARCH_HOST",
    "DEFAULT_DOUBAO_IMAGE_ASSET_HOST",
    "DEFAULT_DOUBAO_IMAGE_HOST",
    "DEFAULT_DOUBAO_IMAGE_MODEL",
    "DEFAULT_DOUBAO_IMAGE_SIZE",
    "PROCESS_TIMEOUT_CLEANUP_SECONDS",
    "build_default_tool_registry",
    "default_external_tool_hosts",
    "register_builtin_tools",
]


def build_default_tool_registry(*, context: ToolContext | None = None) -> ToolRegistry:
    registry = ToolRegistry(default_context=context)
    register_builtin_tools(registry)
    return registry


def register_builtin_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register_function(
        name="search.web",
        description="Search the web through Brave Search and return top results.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_web,
        required_env=("BRAVE_SEARCH_API_KEY",),
        allowed_env=("BRAVE_SEARCH_TIMEOUT_SECONDS",),
        policy=ToolPolicy(risk_class=ToolRiskClass.EXTERNAL_CALL),
    )
    registry.register_function(
        name="image.generate",
        description=(
            "Generate an image through a Doubao-compatible image endpoint. "
            "Optionally download the result bytes and save them to a path under "
            "the run artifact directory via the `save_to` argument so that "
            "downstream verifiers can confirm local containment."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "size": {"type": "string", "default": DEFAULT_DOUBAO_IMAGE_SIZE},
                "model": {"type": "string"},
                "save_to": {
                    "type": "string",
                    "description": (
                        "Relative path under the run artifact_root to save the "
                        "generated image bytes (e.g. 'assets/arch.png'). When "
                        "set, the tool downloads the returned URL and writes "
                        "bytes there, then returns both the URL and the saved "
                        "path. Must stay inside artifact_root."
                    ),
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        handler=generate_image,
        required_env=("DOUBAO_API_KEY",),
        allowed_env=(
            "DOUBAO_IMAGE_API_URL",
            "DOUBAO_IMAGE_MODEL",
            "DOUBAO_IMAGE_TIMEOUT_SECONDS",
            "DOUBAO_IMAGE_ASSET_HOST",
        ),
        policy=ToolPolicy(risk_class=ToolRiskClass.EXTERNAL_CALL),
    )
    registry.register_function(
        name="artifact.write_json",
        description="Write a JSON artifact under the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "data": {"type": "object"},
            },
            "required": ["path", "data"],
            "additionalProperties": False,
        },
        handler=write_json_artifact,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    registry.register_function(
        name="artifact.read_json",
        description="Read a JSON artifact from the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=read_json_artifact,
    )
    registry.register_function(
        name="artifact.exists",
        description="Check whether an artifact path exists under the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=artifact_exists,
    )
    registry.register_function(
        name="artifact.list",
        description="List artifact files and directories under the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        handler=list_artifacts,
    )
    registry.register_function(
        name="artifact.write_text",
        description="Write a UTF-8 text artifact under the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=write_text_artifact,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    registry.register_function(
        name="artifact.read_text",
        description="Read a UTF-8 text artifact from the run artifact directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=read_text_artifact,
    )
    registry.register_function(
        name="time.now",
        description="Return the current UTC timestamp for traceable workflow artifacts.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=time_now,
    )
    registry.register_function(
        name="process.run",
        description="Run an allowlisted process under the run artifact directory without a shell.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "cwd": {"type": "string", "default": "."},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 600},
                "max_output_bytes": {"type": "integer", "minimum": 1, "maximum": 65536},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=run_process,
        policy=ToolPolicy(
            risk_class=ToolRiskClass.DESTRUCTIVE,
            requires_approval=True,
        ),
        manages_timeout=True,
    )
    return registry
