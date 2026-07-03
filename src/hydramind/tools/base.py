"""Tool registration and execution contracts."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from hydramind.harness import ToolSpec


class ToolExecutionResult(BaseModel):
    """Normalized result from a HydraMind tool call."""

    model_config = ConfigDict(frozen=True)

    success: bool
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls, result: Any, *, metadata: dict[str, Any] | None = None) -> ToolExecutionResult:
        return cls(success=True, result=result, metadata=metadata or {})

    @classmethod
    def fail(cls, error: str, *, metadata: dict[str, Any] | None = None) -> ToolExecutionResult:
        return cls(success=False, error=error, metadata=metadata or {})


class ToolRiskClass(StrEnum):
    READ_ONLY = "read_only"
    WRITE_ARTIFACT = "write_artifact"
    EXTERNAL_CALL = "external_call"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class ToolPolicy:
    """Minimal execution policy checked before a tool handler runs."""

    risk_class: ToolRiskClass = ToolRiskClass.READ_ONLY
    allowed_nodes: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ()
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolContext:
    """Per-run context available to tools.

    ``dry_run`` lets CI exercise registration and function-call plumbing without
    using live API keys or network.
    """

    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    artifact_root: Path = Path("artifacts")
    dry_run: bool = False
    node_key: str | None = None
    role: str | None = None
    approved_tools: tuple[str, ...] = ()
    allowed_env_keys: tuple[str, ...] = ()
    network_access: bool = False
    allowed_network_hosts: tuple[str, ...] = ()
    tool_timeout_seconds: float | None = None
    allowed_process_commands: tuple[str, ...] = ()
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...] = ()
    max_http_response_bytes: int = 50 * 1024 * 1024
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionEnvironment:
    """Per-run execution policy spine used to derive tool contexts."""

    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    artifact_root: Path = Path("artifacts")
    dry_run: bool = False
    approved_tools: tuple[str, ...] = ()
    allowed_env_keys: tuple[str, ...] = ()
    network_access: bool = False
    allowed_network_hosts: tuple[str, ...] = ()
    tool_timeout_seconds: float | None = None
    allowed_process_commands: tuple[str, ...] = ()
    allowed_process_argv_prefixes: tuple[tuple[str, ...], ...] = ()
    max_http_response_bytes: int = 50 * 1024 * 1024
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_tool_context(
        self,
        *,
        node_key: str | None = None,
        role: str | None = None,
    ) -> ToolContext:
        return ToolContext(
            env=self.env,
            artifact_root=self.artifact_root,
            dry_run=self.dry_run,
            node_key=node_key,
            role=role,
            approved_tools=self.approved_tools,
            allowed_env_keys=self.allowed_env_keys,
            network_access=self.network_access,
            allowed_network_hosts=self.allowed_network_hosts,
            tool_timeout_seconds=self.tool_timeout_seconds,
            allowed_process_commands=self.allowed_process_commands,
            allowed_process_argv_prefixes=self.allowed_process_argv_prefixes,
            max_http_response_bytes=self.max_http_response_bytes,
            metadata=self.metadata,
        )


ToolHandler: TypeAlias = Callable[
    [dict[str, Any], ToolContext],
    ToolExecutionResult | Awaitable[ToolExecutionResult],
]


@dataclass(frozen=True)
class RegisteredTool:
    """A registered tool exposed to the harness as a function ToolSpec."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    enabled: bool = True
    required_env: tuple[str, ...] = ()
    allowed_env: tuple[str, ...] = ()
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    manages_timeout: bool = False

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


@dataclass(frozen=True)
class ToolExecutionMetadata:
    """Typed, redaction-safe side-effect evidence computed before a handler runs.

    Returned by ``ToolRunner.tool_execution_metadata`` (F8). The fields are the
    stable contract; ``as_detail()`` renders a redaction-safe mapping for trace
    event ``detail`` so observability behavior is unchanged. The mapping is the
    only place this becomes an untyped dict; the structure itself stays typed.
    """

    tool_name: str
    dry_run: bool
    network_access: bool
    effect_fingerprint: str
    registered: bool
    enabled: bool
    risk_class: str
    side_effect_class: str
    # Present only for registered tools; ``None`` for unregistered ones.
    requires_approval: bool | None = None
    approval_present: bool | None = None
    node_scoped: bool | None = None
    role_scoped: bool | None = None
    idempotency_scope: str | None = None

    def as_detail(self) -> dict[str, Any]:
        """Render to the redaction-safe mapping used in trace event detail.

        Omits ``None`` optional fields so an unregistered tool produces exactly
        the historical key set (no ``requires_approval`` etc.).
        """

        detail: dict[str, Any] = {
            "tool_name": self.tool_name,
            "dry_run": self.dry_run,
            "network_access": self.network_access,
            "effect_fingerprint": self.effect_fingerprint,
            "registered": self.registered,
            "enabled": self.enabled,
            "risk_class": self.risk_class,
            "side_effect_class": self.side_effect_class,
        }
        if self.requires_approval is not None:
            detail["requires_approval"] = self.requires_approval
        if self.approval_present is not None:
            detail["approval_present"] = self.approval_present
        if self.node_scoped is not None:
            detail["node_scoped"] = self.node_scoped
        if self.role_scoped is not None:
            detail["role_scoped"] = self.role_scoped
        if self.idempotency_scope is not None:
            detail["idempotency_scope"] = self.idempotency_scope
        return detail


class ToolError(RuntimeError):
    """Tool registration or execution error."""
