"""Single-node invocation runtime for OrchestratorAgent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from hydramind.control.models import AgentReport, RuntimeSession
from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ToolSpec,
)
from hydramind.orchestration.agent_context import AgentPromptContextBuilder
from hydramind.orchestration.agent_execution import AgentExecutionRuntime
from hydramind.orchestration.agent_tools import AgentToolLoop
from hydramind.orchestration.agent_tools import (
    subagent_tool_origin as _subagent_tool_origin,
)
from hydramind.orchestration.agent_tools import (
    tool_call_names as _tool_call_names,
)
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.planning_contracts import (
    NodeExecutionMode,
    resolve_node_execution_mode,
)
from hydramind.orchestration.verification import VerifierRunner


@runtime_checkable
class ToolProvider(Protocol):
    """Returns the tools available to a given node."""

    def tools_for(self, node_key: str) -> list[ToolSpec]: ...


ReportBuilder = Callable[
    [str, str, InvocationResult],
    Awaitable[AgentReport] | AgentReport,
]
"""(node_key, agent_id, invocation_result) -> AgentReport."""


NodeInvocationHandler = Callable[..., Awaitable[InvocationResult]]
"""A node dispatch handler keyed on :class:`NodeExecutionMode`."""


async def default_report_builder(
    node_key: str, agent_id: str, result: InvocationResult
) -> AgentReport:
    if result.tool_calls:
        output: dict[str, Any] = {
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in result.tool_calls
            ]
        }
    else:
        text = (result.content or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                output = json.loads(text)
            except json.JSONDecodeError:
                output = {"text": text}
        else:
            output = {"text": text}
    return AgentReport(node_key=node_key, agent_id=agent_id, output=output)


class _NoToolProvider:
    def tools_for(self, node_key: str) -> list[ToolSpec]:
        return []


class AgentNodeInvoker:
    """Owns prompt, dispatch, tool-loop, report, and verifier wiring for one node."""

    def __init__(
        self,
        *,
        execution: AgentExecutionRuntime,
        context_builder: AgentPromptContextBuilder,
        tool_provider: ToolProvider | None,
        tool_loop: AgentToolLoop,
        collaboration: CollaborationExecutor,
        report_builder: ReportBuilder | None,
        verifier_runner: VerifierRunner | None,
    ) -> None:
        self._execution = execution
        self._context_builder = context_builder
        self._tools = tool_provider or _NoToolProvider()
        self._tool_loop = tool_loop
        self._collaboration = collaboration
        self._report_builder = report_builder or default_report_builder
        self._verifier_runner = verifier_runner

    def dispatch_table(
        self,
    ) -> dict[NodeExecutionMode, NodeInvocationHandler]:
        """Return typed node dispatch handlers for every execution mode."""

        return {
            NodeExecutionMode.DIRECT: self._dispatch_direct,
            NodeExecutionMode.SUBAGENT: self._dispatch_subagent,
            NodeExecutionMode.TEAM: self._dispatch_team,
        }

    async def invoke(
        self,
        *,
        session: RuntimeSession,
        node_key: str,
        agent_role: str,
        node_config: dict[str, Any],
        execution_id: str,
        trace_id: str,
        lease_token: str | None = None,
    ) -> AgentReport:
        system, user = await self._context_builder.compose_messages(
            session,
            node_key,
            agent_role,
        )
        tools = self._tools.tools_for(node_key) or None
        messages = [Message(role=MessageRole.USER, content=user)]
        mode = resolve_node_execution_mode(node_config)
        collaboration_request = CollaborationExecutionRequest(
            session_id=session.id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            messages=messages,
            system=system,
            tools=tools,
            agent_role=agent_role,
            node_config=node_config,
        )
        handler = self.dispatch_table()[mode]
        invocation = await handler(
            session_id=session.id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            messages=messages,
            system=system,
            tools=tools,
            role=agent_role,
            collaboration_request=collaboration_request,
        )
        if tools:
            tool_origin = (
                _subagent_tool_origin(invocation, agent_role)
                if mode is NodeExecutionMode.SUBAGENT
                else None
            )
            invocation = await self._tool_loop.drain_tool_calls(
                session_id=session.id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                invocation=invocation,
                messages=messages,
                system=system,
                tools=tools,
                agent_role=agent_role,
                tool_origin=tool_origin,
            )
        if invocation.tool_calls:
            raise RuntimeError(
                f"unresolved tool calls: {_tool_call_names(invocation.tool_calls)}"
            )
        built = self._report_builder(node_key, agent_role, invocation)
        report = built if isinstance(built, AgentReport) else await built
        report = report.model_copy(
            update={
                "execution_id": execution_id,
                "trace_id": trace_id,
                "lease_token": lease_token,
            }
        )
        if self._verifier_runner is not None:
            report = await self._verifier_runner.verify(
                session=session,
                node_config=node_config,
                report=report,
            )
        return report

    async def _dispatch_direct(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        messages: list[Message],
        system: str,
        tools: list[ToolSpec] | None,
        role: str,
        collaboration_request: CollaborationExecutionRequest,
    ) -> InvocationResult:
        del collaboration_request
        return await self._execution.invoke_model(
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            messages=messages,
            system=system,
            tools=tools,
            role=role,
            round_no=0,
        )

    async def _dispatch_subagent(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        messages: list[Message],
        system: str,
        tools: list[ToolSpec] | None,
        role: str,
        collaboration_request: CollaborationExecutionRequest,
    ) -> InvocationResult:
        del collaboration_request
        return await self._execution.invoke_subagent(
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            messages=messages,
            system=system,
            tools=tools,
            role=role,
        )

    async def _dispatch_team(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        messages: list[Message],
        system: str,
        tools: list[ToolSpec] | None,
        role: str,
        collaboration_request: CollaborationExecutionRequest,
    ) -> InvocationResult:
        del session_id, node_key, execution_id, trace_id, messages, system, tools, role
        return await self._collaboration.invoke_team(collaboration_request)


__all__ = [
    "AgentNodeInvoker",
    "NodeInvocationHandler",
    "ReportBuilder",
    "ToolProvider",
    "default_report_builder",
]
