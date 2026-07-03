"""Tool-call draining for orchestrated agent executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from hydramind.control.control_plane import ControlPlane
from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from hydramind.observability import (
    ObservationEventKind,
    redact_value,
    redacted_tool_result_preview,
)
from hydramind.observability.event_details import EVENT_DETAIL_SCHEMA_VERSION
from hydramind.tools.base import ToolContext, ToolExecutionMetadata


@runtime_checkable
class ToolRunner(Protocol):
    """The real, typed contract for executing tool calls returned by the harness.

    The execution loop relies on all three methods (F8); there is no
    ``getattr``/``TypeError`` fallback. The sole production implementation is
    ``hydramind.tools.ToolRegistry``.
    """

    async def run_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        context: ToolContext | None = None,
    ) -> tuple[ToolResultBlock, ...]: ...

    def context_for_node(self, node_key: str, role: str) -> ToolContext: ...

    def tool_execution_metadata(
        self,
        call: ToolCall,
        *,
        context: ToolContext,
    ) -> ToolExecutionMetadata: ...


class TraceEmitterFn(Protocol):
    async def __call__(
        self,
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None: ...


class ModelInvokerFn(Protocol):
    async def __call__(
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
        round_no: int,
    ) -> InvocationResult: ...


@dataclass(frozen=True)
class AgentToolLoop:
    """Owns tool-call drain rounds, ledger writes, and tool trace evidence."""

    control: ControlPlane
    tool_runner: ToolRunner | None
    emit_trace: TraceEmitterFn
    invoke_model: ModelInvokerFn
    max_tool_rounds: int

    async def drain_tool_calls(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        invocation: InvocationResult,
        messages: list[Message],
        system: str,
        tools: list[ToolSpec],
        agent_role: str,
        tool_origin: dict[str, Any] | None = None,
    ) -> InvocationResult:
        if self.tool_runner is None:
            return invocation
        current = invocation
        successful_results_by_fingerprint: dict[str, tuple[str, str]] = {}
        for round_no in range(1, self.max_tool_rounds + 1):
            if not current.tool_calls:
                return current
            round_origin = tool_origin if round_no == 1 else None
            tool_context = self.tool_context_for(node_key, agent_role)
            tool_results = await self.execute_tool_round(
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                tool_calls=current.tool_calls,
                round_no=round_no,
                tools=tools,
                agent_role=agent_role,
                tool_context=tool_context,
                origin=round_origin,
                successful_results_by_fingerprint=successful_results_by_fingerprint,
            )
            messages.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=current.content,
                    tool_calls=current.tool_calls,
                    reasoning_content=_reasoning_content(current),
                )
            )
            messages.append(
                Message(role=MessageRole.TOOL, tool_results=tuple(tool_results))
            )
            current = await self.invoke_model(
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                messages=messages,
                system=system,
                tools=tools,
                role=agent_role,
                round_no=round_no,
            )
        return current

    async def execute_tool_round(
        self,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        tool_calls: tuple[ToolCall, ...],
        round_no: int,
        tools: list[ToolSpec],
        agent_role: str,
        tool_context: ToolContext,
        origin: dict[str, Any] | None,
        successful_results_by_fingerprint: dict[str, tuple[str, str]],
    ) -> list[ToolResultBlock]:
        await self.emit_trace(
            ObservationEventKind.TOOL_DRAIN_ROUND,
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={
                "round": round_no,
                "tool_call_count": len(tool_calls),
                "tool_names": [call.name for call in tool_calls],
                **_origin_detail(origin),
            },
        )
        allowed_names = {tool.name for tool in tools}
        unauthorized = [
            call.name
            for call in tool_calls
            if call.name not in allowed_names
        ]
        if unauthorized:
            raise RuntimeError(
                "unauthorized tool calls: "
                f"{', '.join(sorted(set(unauthorized)))}"
            )
        tool_results: list[ToolResultBlock] = []
        for call in tool_calls:
            typed_metadata = self.tool_execution_metadata(
                call,
                context=tool_context,
            )
            effect_fingerprint = _effect_fingerprint(typed_metadata)
            cached_result = (
                successful_results_by_fingerprint.get(effect_fingerprint)
                if effect_fingerprint is not None
                else None
            )
            # S4c: durable, control-owned tool side-effect dedupe across queue
            # deliveries. If a previous (possibly re-delivered) execution already
            # succeeded for this effect_fingerprint in this session, reuse its
            # recorded result instead of re-running the side effect.
            if cached_result is None and effect_fingerprint is not None:
                durable_result = await self.control.lookup_tool_effect_result(
                    session_id, effect_fingerprint
                )
                if durable_result is not None:
                    cached_result = durable_result
                    successful_results_by_fingerprint[effect_fingerprint] = (
                        durable_result
                    )
            execution_metadata = _execution_metadata_detail(
                typed_metadata,
                origin=origin,
                source_tool_call_id=(
                    cached_result[0] if cached_result is not None else None
                ),
            )
            await self.control.record_tool_execution_started(
                session_id,
                execution_id,
                tool_call_id=call.id,
                tool_name=call.name,
                round_no=round_no,
                arguments=call.arguments,
                trace_id=trace_id,
                metadata=execution_metadata,
            )
            await self.emit_trace(
                ObservationEventKind.TOOL_CALL_STARTED,
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                detail={
                    "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
                    "round": round_no,
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "arguments": redact_value(call.arguments),
                    "execution_metadata": execution_metadata,
                    **_origin_detail(origin),
                },
            )
            if cached_result is not None:
                result = ToolResultBlock(
                    tool_call_id=call.id,
                    content=cached_result[1],
                    is_error=False,
                )
            else:
                result_batch = await self.run_tool_calls(
                    (call,),
                    node_key=node_key,
                    agent_role=agent_role,
                    context=tool_context,
                )
                result = result_batch[0]
                if effect_fingerprint is not None and not result.is_error:
                    successful_results_by_fingerprint[effect_fingerprint] = (
                        call.id,
                        result.content,
                    )
                    # S4c: persist the successful effect result so a later
                    # duplicate delivery reuses it (durable, control-owned).
                    await self.control.record_tool_effect_result(
                        session_id,
                        effect_fingerprint,
                        tool_call_id=call.id,
                        content=result.content,
                    )
            tool_results.append(result)
            result_preview = redacted_tool_result_preview(result.content)
            await self.control.record_tool_execution_completed(
                session_id,
                execution_id,
                tool_call_id=result.tool_call_id,
                is_error=result.is_error,
                result_preview=result_preview,
                content_length=len(result.content),
                error=(
                    str(result_preview.get("content_preview"))
                    if result.is_error
                    else None
                ),
            )
            await self.emit_trace(
                ObservationEventKind.TOOL_CALL_COMPLETED,
                session_id=session_id,
                node_key=node_key,
                execution_id=execution_id,
                trace_id=trace_id,
                level="error" if result.is_error else "info",
                detail={
                    "round": round_no,
                    "tool_call_id": result.tool_call_id,
                    "is_error": result.is_error,
                    **result_preview,
                    "content_length": len(result.content),
                    "execution_metadata": execution_metadata,
                    **_origin_detail(origin),
                },
            )
        return tool_results

    async def run_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        node_key: str,
        agent_role: str,
        context: ToolContext | None = None,
    ) -> tuple[ToolResultBlock, ...]:
        if self.tool_runner is None:
            return ()
        context = context or self.tool_context_for(node_key, agent_role)
        return await self.tool_runner.run_tool_calls(tool_calls, context=context)

    def tool_context_for(self, node_key: str, agent_role: str) -> ToolContext:
        if self.tool_runner is None:
            return ToolContext(node_key=node_key, role=agent_role)
        return self.tool_runner.context_for_node(node_key, agent_role)

    def tool_execution_metadata(
        self,
        call: ToolCall,
        *,
        context: ToolContext,
    ) -> ToolExecutionMetadata:
        if self.tool_runner is None:
            raise RuntimeError("tool_execution_metadata requires a tool runner")
        return self.tool_runner.tool_execution_metadata(call, context=context)


def tool_call_names(tool_calls: tuple[ToolCall, ...]) -> str:
    return ", ".join(call.name for call in tool_calls) or "<unknown>"


def subagent_tool_origin(
    invocation: InvocationResult,
    role: str,
) -> dict[str, Any] | None:
    subagent_id = invocation.subagent_id
    if not isinstance(subagent_id, str) or not subagent_id:
        return None
    return {
        "execution_mode": "subagent",
        "subagent_id": subagent_id,
        "subagent_role": role,
    }


def _reasoning_content(invocation: InvocationResult) -> str | None:
    value = invocation.reasoning_content
    return value if isinstance(value, str) and value else None


def _origin_detail(origin: dict[str, Any] | None) -> dict[str, Any]:
    if not origin:
        return {}
    return {"origin": dict(origin)}


def _effect_fingerprint(metadata: ToolExecutionMetadata) -> str | None:
    value = metadata.effect_fingerprint
    if isinstance(value, str) and value:
        return value
    return None


def _execution_metadata_detail(
    metadata: ToolExecutionMetadata,
    *,
    origin: dict[str, Any] | None,
    source_tool_call_id: str | None,
) -> dict[str, Any]:
    """Render typed tool-execution metadata to the trace/ledger detail mapping.

    The typed object stays typed everywhere else; only this trace-emit point
    flattens it to a dict, merges ``origin`` keys, and stamps effect-reuse
    markers — preserving the historical ``execution_metadata`` detail shape.
    """

    detail = metadata.as_detail()
    if origin:
        detail.update(origin)
    if source_tool_call_id is None:
        return detail
    detail["reused_result"] = True
    detail["source_tool_call_id"] = source_tool_call_id
    if fingerprint := _effect_fingerprint(metadata):
        detail["source_effect_fingerprint"] = fingerprint
    return detail
