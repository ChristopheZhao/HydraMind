"""Goal-driven orchestration entrypoint."""

from __future__ import annotations

from typing import Any

from hydramind.control import (
    ControlPlane,
    GateDecisionInput,
    RuntimeDecision,
    RuntimeSession,
)
from hydramind.harness import ModelProvider
from hydramind.observability import Emitter
from hydramind.orchestration.agent import (
    ReportBuilder,
    ToolProvider,
    ToolRunner,
)
from hydramind.orchestration.goal_agent_factory import (
    GoalAgentFactory,
    GoalSessionAgent,
)
from hydramind.orchestration.goal_repair_runtime import GoalRepairRuntime
from hydramind.orchestration.goal_session_state import GoalSessionState
from hydramind.orchestration.memory_context import MemoryContextRetriever
from hydramind.orchestration.planning import (
    ExecutionPlan,
    GoalSpec,
    ModelGoalPlanner,
    PlanDelta,
    PlannerProvider,
)
from hydramind.orchestration.prompts import PromptLibrary
from hydramind.orchestration.verification import VerifierRunner

__all__ = [
    "GoalDrivenOrchestratorAgent",
]


class GoalDrivenOrchestratorAgent:
    """Starts runtime sessions from goals instead of static workflow files."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        control: ControlPlane,
        planner: PlannerProvider | None = None,
        prompts: PromptLibrary | None = None,
        tool_provider: ToolProvider | None = None,
        tool_runner: ToolRunner | None = None,
        report_builder: ReportBuilder | None = None,
        verifier_runner: VerifierRunner | None = None,
        max_repair_attempts: int = 1,
        emitter: Emitter | None = None,
        max_tool_rounds: int = 4,
        max_steps: int = 100,
        memory_retriever: MemoryContextRetriever | None = None,
    ) -> None:
        self._provider = provider
        self._control = control
        self._session_state = GoalSessionState(control)
        self._agent_factory = GoalAgentFactory(
            provider=provider,
            control=control,
            prompts=prompts,
            tool_provider=tool_provider,
            tool_runner=tool_runner,
            report_builder=report_builder,
            verifier_runner=verifier_runner,
            emitter=emitter,
            max_tool_rounds=max_tool_rounds,
            max_steps=max_steps,
            memory_retriever=memory_retriever,
        )
        self._planner = planner or ModelGoalPlanner(provider)
        self._agents: dict[str, GoalSessionAgent] = {}
        self._repair_runtime = GoalRepairRuntime(
            control=control,
            revise_goal_session=self.revise_goal_session,
            agent_for_session=self._agent_for_session,
            max_repair_attempts=max_repair_attempts,
        )

    async def start_goal(
        self,
        goal: GoalSpec,
        *,
        input_payload: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> RuntimeSession:
        plan = await self._planner.initial_plan(goal)
        agent = self._agent_factory.build(plan)
        session_metadata = self._session_state.session_metadata(
            plan,
            metadata=metadata,
            runtime_overrides=runtime_overrides,
        )
        payload: dict[str, object] = {"goal": goal.objective}
        if input_payload:
            payload.update(input_payload)
        session = await agent.start_session(
            input_payload=payload,
            metadata=session_metadata,
        )
        self._agents[session.id] = agent
        return session

    async def run_goal_session(self, session_id: str) -> RuntimeDecision:
        agent = await self._agent_for_session(session_id)
        decision = await agent.run_session(session_id)
        return await self._repair_runtime.maybe_auto_repair(session_id, decision)

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision:
        """Worker-compatible session runner for queued goal sessions."""

        agent = await self._agent_for_session(session_id)
        decision = await agent.run_session(
            session_id,
            execution_owner=execution_owner,
            lease_ttl_seconds=lease_ttl_seconds,
            lease_heartbeat_interval_seconds=lease_heartbeat_interval_seconds,
        )
        return await self._repair_runtime.maybe_auto_repair(session_id, decision)

    async def resume_goal_session(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> RuntimeDecision:
        decision = await self._repair_runtime.prepare_gate_decision(
            session_id,
            decision,
        )
        agent = await self._agent_for_session(session_id)
        return await agent.resume_session(session_id, decision)

    async def apply_plan_delta(
        self,
        session_id: str,
        delta: PlanDelta,
    ) -> ExecutionPlan:
        updated = await self._session_state.apply_plan_delta(session_id, delta)
        self._agents.pop(session_id, None)
        return updated

    async def revise_goal_session(
        self,
        session_id: str,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        current = await self._session_state.plan_for_session(session_id)
        delta = await self._planner.revise_plan(current.goal, current, feedback)
        if delta.add_tasks or delta.update_tasks or delta.remove_task_keys:
            await self.apply_plan_delta(session_id, delta)
        return delta

    async def run_goal(
        self,
        goal: GoalSpec,
        *,
        input_payload: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> RuntimeSession:
        session = await self.start_goal(
            goal,
            input_payload=input_payload,
            metadata=metadata,
            runtime_overrides=runtime_overrides,
        )
        await self.run_goal_session(session.id)
        return await self._control.service.get_session(session.id)

    async def _agent_for_session(self, session_id: str) -> GoalSessionAgent:
        existing = self._agents.get(session_id)
        if existing is not None:
            return existing
        plan = await self._session_state.plan_for_session(session_id)
        agent = self._agent_factory.build(plan)
        self._agents[session_id] = agent
        return agent
