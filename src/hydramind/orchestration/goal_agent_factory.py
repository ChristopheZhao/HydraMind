"""Goal-plan agent factory and tool scope adapter."""

from __future__ import annotations

from typing import Protocol

from hydramind.control import ControlPlane, GateDecisionInput, RuntimeDecision
from hydramind.control.models import RuntimeSession
from hydramind.harness import ModelProvider, ToolSpec
from hydramind.observability import Emitter
from hydramind.orchestration.agent import (
    OrchestratorAgent,
    ReportBuilder,
    ToolProvider,
    ToolRunner,
)
from hydramind.orchestration.memory_context import MemoryContextRetriever
from hydramind.orchestration.planning import ExecutionPlan
from hydramind.orchestration.prompts import PromptLibrary
from hydramind.orchestration.verification import VerifierRunner


class GoalSessionAgent(Protocol):
    """Agent surface used by goal-driven runtime orchestration."""

    async def start_session(
        self,
        *,
        input_payload: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeSession: ...

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision: ...

    async def resume_session(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> RuntimeDecision: ...


class GoalAgentFactory:
    """Build executable workflow agents from goal-derived plans."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        control: ControlPlane,
        prompts: PromptLibrary | None = None,
        tool_provider: ToolProvider | None = None,
        tool_runner: ToolRunner | None = None,
        report_builder: ReportBuilder | None = None,
        verifier_runner: VerifierRunner | None = None,
        emitter: Emitter | None = None,
        max_tool_rounds: int = 4,
        max_steps: int = 100,
        memory_retriever: MemoryContextRetriever | None = None,
    ) -> None:
        self._provider = provider
        self._control = control
        self._prompts = prompts
        self._tool_provider = tool_provider
        self._tool_runner = tool_runner
        self._report_builder = report_builder
        self._verifier_runner = verifier_runner
        self._emitter = emitter
        self._max_tool_rounds = max_tool_rounds
        self._max_steps = max_steps
        self._memory_retriever = memory_retriever

    def build(self, plan: ExecutionPlan) -> GoalSessionAgent:
        tool_provider = self._tool_provider
        if tool_provider is not None:
            tool_provider = PlanScopedToolProvider(plan, tool_provider)
        return OrchestratorAgent(
            provider=self._provider,
            control=self._control,
            workflow=plan.to_blueprint(),
            prompts=self._prompts,
            tool_provider=tool_provider,
            tool_runner=self._tool_runner,
            report_builder=self._report_builder,
            verifier_runner=self._verifier_runner,
            emitter=self._emitter,
            max_tool_rounds=self._max_tool_rounds,
            max_steps=self._max_steps,
            memory_retriever=self._memory_retriever,
        )


class PlanScopedToolProvider:
    """Restrict node tools to the intersection of task and goal declarations."""

    def __init__(self, plan: ExecutionPlan, base: ToolProvider) -> None:
        self._base = base
        self._tools_by_node = {
            task.key: set(task.tools).intersection(plan.goal.available_tools)
            for task in plan.tasks
        }

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        allowed = self._tools_by_node.get(node_key)
        if allowed is None or not allowed:
            return []
        specs_for = getattr(self._base, "specs_for", None)
        if callable(specs_for):
            return list(specs_for(sorted(allowed)))
        return [
            spec
            for spec in self._base.tools_for(node_key)
            if spec.name in allowed
        ]


__all__ = [
    "GoalAgentFactory",
    "GoalSessionAgent",
    "PlanScopedToolProvider",
]
