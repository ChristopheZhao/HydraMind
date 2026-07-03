"""Execution support helpers for ``OrchestratorAgent``."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from hydramind.control.control_plane import ControlPlane
from hydramind.control.models import AgentReport
from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ModelHint,
    ToolSpec,
)
from hydramind.harness.provider import ModelProvider
from hydramind.observability import (
    Emitter,
    ObservationEvent,
    ObservationEventKind,
    compact_text,
)
from hydramind.orchestration.execution_harness import (
    SubagentContext,
    SubagentSpawnRequest,
)
from hydramind.orchestration.subagent_spawn import SubagentSpawner

NodeInvocation = Callable[[], Awaitable[AgentReport]]


@dataclass(frozen=True)
class AgentExecutionRuntime:
    """Owns harness invocation telemetry and execution lease heartbeats."""

    provider: ModelProvider
    subagent_spawner: SubagentSpawner
    control: ControlPlane
    emitter: Emitter | None
    model_hint: ModelHint

    async def invoke_with_lease_heartbeat(
        self,
        invoke_node: NodeInvocation,
        *,
        session_id: str,
        execution_id: str,
        lease_token: str | None,
        lease_ttl_seconds: int,
        lease_heartbeat_interval_seconds: float | None,
    ) -> AgentReport:
        if lease_token is None:
            return await invoke_node()
        stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_execution_lease_until_stopped(
                session_id=session_id,
                execution_id=execution_id,
                lease_token=lease_token,
                ttl_seconds=lease_ttl_seconds,
                interval_seconds=resolve_lease_heartbeat_interval_seconds(
                    lease_ttl_seconds,
                    lease_heartbeat_interval_seconds,
                ),
                stop=stop,
            )
        )
        try:
            return await invoke_node()
        finally:
            stop.set()
            await heartbeat_task

    async def _heartbeat_execution_lease_until_stopped(
        self,
        *,
        session_id: str,
        execution_id: str,
        lease_token: str,
        ttl_seconds: int,
        interval_seconds: float,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                return
            except TimeoutError:
                await self.control.heartbeat_node_execution_lease(
                    session_id,
                    execution_id,
                    lease_token=lease_token,
                    ttl_seconds=ttl_seconds,
                )

    async def invoke_subagent(
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
    ) -> InvocationResult:
        # Thread the prior message context into the subagent so the agent can
        # read it (interaction seam, ADR-0007); stamp the speaker identity
        # (agent role) onto each message as Message.name so multi-speaker
        # transcripts carry agent identity through the seam (DEV-33). Single-agent
        # behavior is unchanged: the sent turn (messages[-1]) is still relayed.
        seed_messages = tuple(_with_speaker_name(message, role) for message in messages[:-1])
        subagent = await self.subagent_spawner.spawn(
            SubagentSpawnRequest(
                role=role,
                instructions=system,
                tools=tuple(tools or ()),
                parent_context=SubagentContext(
                    seed_messages=seed_messages,
                    metadata={"session_id": session_id, "node_key": node_key},
                ),
            )
        )
        await self.emit_trace(
            ObservationEventKind.MODEL_INVOKE_STARTED,
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={
                "role": role,
                "round": 0,
                "message_count": len(messages),
                "tool_count": len(tools or ()),
                "model_hint": self.model_hint.value,
                "execution_mode": "subagent",
                "subagent_id": subagent.id,
            },
        )
        result = await subagent.send(_with_speaker_name(messages[-1], role))
        summary = await subagent.close()
        content = result.content or summary
        invocation = result.model_copy(
            update={
                "content": content,
                "subagent_id": subagent.id,
                "subagent_summary": summary,
            }
        )
        await self.emit_trace(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={
                "role": role,
                "round": 0,
                "model_id": invocation.model_id,
                "stop_reason": invocation.stop_reason.value,
                "tool_call_count": len(invocation.tool_calls),
                "content_preview": compact_text(invocation.content),
                "usage": invocation.usage.model_dump(),
                "execution_mode": "subagent",
                "subagent_id": subagent.id,
            },
        )
        return invocation

    async def invoke_model(
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
    ) -> InvocationResult:
        await self.emit_trace(
            ObservationEventKind.MODEL_INVOKE_STARTED,
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={
                "role": role,
                "round": round_no,
                "message_count": len(messages),
                "tool_count": len(tools or ()),
                "model_hint": self.model_hint.value,
            },
        )
        result = await self.provider.complete(
            messages=messages,
            system=system,
            tools=tools,
            role=role,
            model_hint=self.model_hint,
        )
        await self.emit_trace(
            ObservationEventKind.MODEL_INVOKE_COMPLETED,
            session_id=session_id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={
                "role": role,
                "round": round_no,
                "model_id": result.model_id,
                "stop_reason": result.stop_reason.value,
                "tool_call_count": len(result.tool_calls),
                "content_preview": compact_text(result.content),
                "usage": result.usage.model_dump(),
            },
        )
        return result

    async def emit_trace(
        self,
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict[str, Any] | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None:
        if self.emitter is None:
            return
        await self.emitter.emit(
            ObservationEvent(
                kind=kind,
                session_id=session_id,
                node_key=node_key,
                trace_id=trace_id,
                execution_id=execution_id,
                source="orchestration",
                level=level,
                actor=actor,
                detail=detail or {},
            )
        )


def _with_speaker_name(message: Message, role: str) -> Message:
    """Stamp the acting agent's role as ``Message.name`` (speaker identity).

    Only fills an absent name and only for user/assistant turns (system/tool
    messages have no speaker identity). Preserves any name the producer already
    set, so peer-authored messages keep their own speaker identity (S94).
    """

    if not role or message.name is not None:
        return message
    if message.role not in (MessageRole.USER, MessageRole.ASSISTANT):
        return message
    return message.model_copy(update={"name": role})


def new_trace_id(session_id: str, node_key: str) -> str:
    return f"trace-{session_id}-{node_key}-{uuid.uuid4().hex[:8]}"


def resolve_lease_heartbeat_interval_seconds(
    ttl_seconds: int,
    requested: float | None,
) -> float:
    if ttl_seconds <= 0:
        raise ValueError("lease_ttl_seconds must be positive")
    if requested is not None:
        if requested <= 0:
            raise ValueError("lease_heartbeat_interval_seconds must be positive")
        return requested
    return max(1.0, min(float(ttl_seconds) / 3.0, 60.0))
