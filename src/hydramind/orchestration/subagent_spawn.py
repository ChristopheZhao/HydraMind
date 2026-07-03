"""Orchestration-owned sub-agent spawn act (ADR-0012 narrow-harness Phase-2).

Per ADR-0012 / ``96-agent-layering-and-harness-synthesis.md`` §2/§3/§4.2 the
harness is a data-plane executor: it runs the single-agent loop, calls the model,
dispatches tools, and EMITS a typed **delegation request** (its sub-agent
*configuration* — :class:`SubagentSpawnRequest`) plus a capability policy. It never
spawns. **Orchestration owns the spawn ACT** — the instantiation of the sub-agent's
execution unit — recorded durably via Control (durable interaction + turn-lease).

This module homes that spawn act. :class:`SubagentSpawner` consults the harness's
declared capability policy (``ExecutionHarnessCapabilities``) and instantiates a
provider-backed :class:`SubagentHandle`. It performs NO durable-state writes; only
Control mutates ``RuntimeSession``. The spawned sub-agent runs its own
provider-backed loop (recursive two-scale, ``96`` §5): each ``send`` is one model
turn and ``close`` returns its summary.
"""

from __future__ import annotations

from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    ToolSpec,
)
from hydramind.harness.provider import ModelProvider
from hydramind.orchestration.execution_harness import (
    ExecutionHarnessCapabilities,
    ExecutionHarnessFeature,
    ProviderExecutionHarnessRuntime,
    SubagentContext,
    SubagentHandle,
    SubagentSpawnRequest,
)


class SubagentSpawner:
    """Orchestration-owned spawn act for harness-emitted delegation requests.

    The harness owns the sub-agent *configuration* (the typed
    :class:`SubagentSpawnRequest` it emits) and the *capability policy*
    (:class:`ExecutionHarnessCapabilities`); this orchestration spawner owns the
    instantiation. Keeping the spawn act here — not on the harness surface — is the
    ADR-0012 ``config vs instantiation`` reconciliation: the data-plane harness
    proposes/requests, orchestration spawns.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        capabilities: ExecutionHarnessCapabilities,
    ) -> None:
        self._provider = provider
        self._capabilities = capabilities

    @classmethod
    def from_runtime(
        cls, runtime: ProviderExecutionHarnessRuntime
    ) -> SubagentSpawner:
        """Build a spawner from a provider-backed harness runtime.

        Convenience for the common case: the spawn act uses the provider the
        harness wraps and the capability policy the harness declares. Capability
        policy stays owned by the harness (ADR-0012); orchestration only owns the
        spawn act.
        """

        return cls(provider=runtime.provider, capabilities=runtime.capabilities)

    async def spawn(self, request: SubagentSpawnRequest) -> SubagentHandle:
        """Instantiate a sub-agent for a harness-emitted delegation request.

        Honors the harness's declared ``SUBAGENTS`` capability policy before
        instantiating; raises ``ExecutionHarnessCapabilityError`` if the harness
        does not support sub-agents. No durable state is written here.
        """

        self._capabilities.require(
            ExecutionHarnessFeature.SUBAGENTS,
            harness_name=self._provider.name,
            operation="spawn",
        )
        return _ProviderSubagentHandle(
            provider=self._provider,
            role=request.role,
            instructions=request.instructions,
            tools=list(request.tools),
            parent_context=request.parent_context,
        )


class _ProviderSubagentHandle(SubagentHandle):
    def __init__(
        self,
        *,
        provider: ModelProvider,
        role: str,
        instructions: str,
        tools: list[ToolSpec],
        parent_context: SubagentContext | None,
    ) -> None:
        self.id = f"provider-sub-{role}"
        self.role = role
        self._provider = provider
        self._instructions = instructions
        self._tools = tools
        self._messages = list(parent_context.seed_messages if parent_context else ())
        self._last: InvocationResult | None = None
        self._closed = False

    @property
    def messages(self) -> list[Message]:
        """The subagent's threaded message context (seed messages + sends)."""

        return list(self._messages)

    async def send(self, message: Message) -> InvocationResult:
        if self._closed:
            raise RuntimeError(f"subagent {self.id} already closed")
        self._messages.append(message)
        result = await self._provider.complete(
            self._messages,
            tools=self._tools,
            system=self._instructions,
            role=self.role,
        )
        self._messages.append(
            Message(
                role=MessageRole.ASSISTANT,
                content=result.content,
                tool_calls=result.tool_calls,
                reasoning_content=result.reasoning_content,
            )
        )
        self._last = result
        return result

    async def close(self, *, return_summary: bool = True) -> str:
        self._closed = True
        if not return_summary or self._last is None:
            return ""
        return self._last.content


__all__ = ["SubagentSpawner"]
