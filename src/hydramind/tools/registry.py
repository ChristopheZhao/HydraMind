"""ToolRegistry — minimal function-call tool runtime."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from hydramind.harness import ToolCall, ToolResultBlock, ToolSpec
from hydramind.tools.base import (
    RegisteredTool,
    ToolContext,
    ToolError,
    ToolExecutionMetadata,
    ToolExecutionResult,
    ToolHandler,
    ToolPolicy,
)


class ToolRegistry:
    """Register tools, expose ToolSpec objects, and execute ToolCall objects."""

    def __init__(
        self,
        tools: Iterable[RegisteredTool] | None = None,
        *,
        default_context: ToolContext | None = None,
    ) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._default_context = default_context or ToolContext(dry_run=True)
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: RegisteredTool) -> None:
        name = tool.name.strip()
        if not name:
            raise ToolError("tool name must not be empty")
        if name in self._tools:
            raise ToolError(f"tool {name!r} is already registered")
        self._tools[name] = tool

    def register_function(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        enabled: bool = True,
        required_env: tuple[str, ...] = (),
        allowed_env: tuple[str, ...] = (),
        policy: ToolPolicy | None = None,
        manages_timeout: bool = False,
    ) -> None:
        self.register(
            RegisteredTool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
                enabled=enabled,
                required_env=required_env,
                allowed_env=allowed_env,
                policy=policy or ToolPolicy(),
                manages_timeout=manages_timeout,
            )
        )

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"tool {name!r} is not registered") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def stats(self) -> dict[str, Any]:
        enabled = [tool for tool in self._tools.values() if tool.enabled]
        return {
            "total_tools": len(self._tools),
            "enabled_tools": len(enabled),
            "disabled_tools": len(self._tools) - len(enabled),
            "names": self.names(),
        }

    def env_requirements(self, names: Iterable[str] | None = None) -> dict[str, tuple[str, ...]]:
        selected = list(names) if names is not None else list(self._tools)
        return {
            name: self.get(name).required_env
            for name in selected
            if self.get(name).enabled and self.get(name).required_env
        }

    async def health_check(self) -> dict[str, Any]:
        checks = []
        for name in self.names():
            tool = self._tools[name]
            schema_ok = isinstance(tool.input_schema, dict) and bool(tool.input_schema)
            checks.append(
                {
                    "name": name,
                    "enabled": tool.enabled,
                    "schema_ok": schema_ok,
                    "required_env": tool.required_env,
                    "healthy": tool.enabled and schema_ok,
                }
            )
        healthy = sum(1 for item in checks if item["healthy"])
        return {
            "healthy_tools": healthy,
            "unhealthy_tools": len(checks) - healthy,
            "total_tools": len(checks),
            "tools": checks,
        }

    def specs_for(self, names: Iterable[str] | None = None) -> list[ToolSpec]:
        selected = list(names) if names is not None else list(self._tools)
        specs: list[ToolSpec] = []
        for name in selected:
            tool = self.get(name)
            if tool.enabled:
                specs.append(tool.to_spec())
        return specs

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        """ToolProvider-compatible surface used by OrchestratorAgent."""

        return self.specs_for()

    def context_for_node(self, node_key: str, role: str | None = None) -> ToolContext:
        """Return the default tool context scoped to one runtime node."""

        return replace(self._default_context, node_key=node_key, role=role)

    def tool_execution_metadata(
        self,
        tool_call: ToolCall,
        *,
        context: ToolContext | None = None,
    ) -> ToolExecutionMetadata:
        """Return typed, redaction-safe side-effect evidence before a handler runs."""

        ctx = context or self._default_context
        fingerprint = _tool_effect_fingerprint(
            tool_call.name,
            tool_call.arguments,
            context=ctx,
        )
        try:
            tool = self.get(tool_call.name)
        except ToolError:
            return ToolExecutionMetadata(
                tool_name=tool_call.name,
                dry_run=ctx.dry_run,
                network_access=ctx.network_access,
                effect_fingerprint=fingerprint,
                registered=False,
                enabled=False,
                risk_class="unknown",
                side_effect_class="unknown",
            )
        risk_class = tool.policy.risk_class.value
        return ToolExecutionMetadata(
            tool_name=tool_call.name,
            dry_run=ctx.dry_run,
            network_access=ctx.network_access,
            effect_fingerprint=fingerprint,
            registered=True,
            enabled=tool.enabled,
            risk_class=risk_class,
            side_effect_class=_side_effect_class(risk_class),
            requires_approval=tool.policy.requires_approval,
            approval_present=tool_call.name in ctx.approved_tools,
            node_scoped=bool(tool.policy.allowed_nodes),
            role_scoped=bool(tool.policy.allowed_roles),
            idempotency_scope=_idempotency_scope(risk_class, ctx),
        )

    async def run_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        context: ToolContext | None = None,
    ) -> tuple[ToolResultBlock, ...]:
        ctx = context or self._default_context
        results = [await self.run_tool_call(call, context=ctx) for call in tool_calls]
        return tuple(results)

    async def run_tool_call(
        self,
        tool_call: ToolCall,
        *,
        context: ToolContext | None = None,
    ) -> ToolResultBlock:
        ctx = context or self._default_context
        try:
            tool = self.get(tool_call.name)
            if not tool.enabled:
                result = ToolExecutionResult.fail(
                    f"tool {tool_call.name!r} is disabled",
                    metadata={"tool": tool_call.name},
                )
            elif denial := _policy_denial(tool.policy, tool_call.name, ctx):
                result = ToolExecutionResult.fail(
                    denial,
                    metadata={
                        "tool": tool_call.name,
                        "policy_denied": True,
                        "risk_class": tool.policy.risk_class.value,
                    },
                )
            else:
                tool_context = _scope_tool_context(ctx, tool)
                raw = tool.handler(dict(tool_call.arguments), tool_context)
                result = (
                    await _await_tool_result(
                        raw,
                        tool_context,
                        manages_timeout=tool.manages_timeout,
                    )
                    if inspect.isawaitable(raw)
                    else raw
                )
        except TimeoutError:
            result = ToolExecutionResult.fail(
                f"tool {tool_call.name!r} timed out",
                metadata={
                    "tool": tool_call.name,
                    "error_type": "TimeoutError",
                    "timeout_seconds": ctx.tool_timeout_seconds,
                },
            )
        except Exception as exc:
            result = ToolExecutionResult.fail(
                str(exc),
                metadata={"tool": tool_call.name, "error_type": type(exc).__name__},
            )
        return ToolResultBlock(
            tool_call_id=tool_call.id,
            content=_tool_result_content(tool_call.name, result),
            is_error=not result.success,
        )


def _tool_result_content(tool_name: str, result: ToolExecutionResult) -> str:
    payload = {
        "tool": tool_name,
        "success": result.success,
        "result": result.result,
        "error": result.error,
        "metadata": result.metadata,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _tool_effect_fingerprint(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: ToolContext,
) -> str:
    payload = {
        "tool": tool_name,
        "arguments": _redact_for_fingerprint(arguments),
        "node_key": context.node_key,
        "role": context.role,
        "dry_run": context.dry_run,
        "artifact_root": str(context.artifact_root),
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _redact_for_fingerprint(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower()
            if any(
                part in lowered
                for part in ("api_key", "apikey", "authorization", "password", "secret", "token")
            ):
                redacted[text_key] = "<redacted>"
            else:
                redacted[text_key] = _redact_for_fingerprint(item)
        return redacted
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return "<redacted-url>"
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_redact_for_fingerprint(item) for item in value]
    return value


def _side_effect_class(risk_class: str) -> str:
    return {
        "read_only": "none",
        "write_artifact": "artifact_write",
        "external_call": "external_request",
        "destructive": "destructive_local",
    }.get(risk_class, "unknown")


def _idempotency_scope(risk_class: str, context: ToolContext) -> str:
    if context.dry_run:
        return "dry_run"
    return {
        "read_only": "read_only",
        "write_artifact": "artifact_root",
        "external_call": "external_provider",
        "destructive": "local_process",
    }.get(risk_class, "unknown")


def _policy_denial(
    policy: ToolPolicy,
    tool_name: str,
    context: ToolContext,
) -> str | None:
    if policy.allowed_nodes and context.node_key not in policy.allowed_nodes:
        return (
            f"tool {tool_name!r} is not allowed for node "
            f"{context.node_key!r}"
        )
    if policy.allowed_roles and context.role not in policy.allowed_roles:
        return (
            f"tool {tool_name!r} is not allowed for role "
            f"{context.role!r}"
        )
    if policy.requires_approval and tool_name not in context.approved_tools:
        return f"tool {tool_name!r} requires approval"
    return None


async def _await_tool_result(
    raw: Any,
    context: ToolContext,
    *,
    manages_timeout: bool = False,
) -> ToolExecutionResult:
    if context.tool_timeout_seconds is None or manages_timeout:
        return cast(ToolExecutionResult, await raw)
    return cast(
        ToolExecutionResult,
        await asyncio.wait_for(raw, timeout=context.tool_timeout_seconds),
    )


def _scope_tool_context(context: ToolContext, tool: RegisteredTool) -> ToolContext:
    env_keys = set(tool.required_env) | set(tool.allowed_env) | set(context.allowed_env_keys)
    scoped_env = {
        key: value
        for key, value in context.env.items()
        if key in env_keys
    }
    return replace(context, env=scoped_env)
