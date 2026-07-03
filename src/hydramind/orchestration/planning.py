"""Goal-driven planning contracts for HydraMind orchestration."""

from __future__ import annotations

from hydramind.harness import ModelHint, ModelProvider
from hydramind.orchestration import planning_diagnostics as _diagnostics
from hydramind.orchestration import planning_invocation as _planning_invocation
from hydramind.orchestration.builtin_prompts import planner as _planner_prompts
from hydramind.orchestration.memory_context import (
    MemoryContext,
    MemoryContextRequest,
    MemoryContextRetriever,
)
from hydramind.orchestration.planning_contracts import (
    ExecutionPlan,
    GoalSpec,
    NodeExecutionMode,
    PlanDelta,
    PlannerProvider,
    PlanTaskSpec,
)
from hydramind.orchestration.planning_payloads import (
    execution_plan_from_payload as _execution_plan_from_payload,
)
from hydramind.orchestration.planning_payloads import (
    json_dump as _json_dump,
)
from hydramind.orchestration.planning_payloads import (
    plan_delta_from_payload as _plan_delta_from_payload,
)

__all__ = [
    "ExecutionPlan",
    "GoalSpec",
    "ModelGoalPlanner",
    "NodeExecutionMode",
    "PlanDelta",
    "PlanTaskSpec",
    "PlannerProvider",
]


class ModelGoalPlanner:
    """Planner that asks the configured model provider to produce plan JSON."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        role: str = "planner",
        model_hint: ModelHint = ModelHint.POWERFUL,
        max_tokens: int | None = 2048,
        max_json_repairs: int = 1,
        max_invoke_retries: int = 1,
        retry_backoff_seconds: float = 0.0,
        memory_retriever: MemoryContextRetriever | None = None,
    ) -> None:
        if max_json_repairs < 0:
            raise ValueError("max_json_repairs must be non-negative")
        if max_invoke_retries < 0:
            raise ValueError("max_invoke_retries must be non-negative")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        self._max_json_repairs = max_json_repairs
        self._max_invoke_retries = max_invoke_retries
        self._memory_retriever = memory_retriever
        self._invoker = _planning_invocation.PlannerJsonInvoker(
            provider,
            system=_planner_prompts.PLANNER_SYSTEM,
            role=role,
            model_hint=model_hint,
            max_tokens=max_tokens,
            max_json_repairs=max_json_repairs,
            max_invoke_retries=max_invoke_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    async def initial_plan(self, goal: GoalSpec) -> ExecutionPlan:
        diagnostics = _diagnostics.planner_diagnostics(
            operation="initial_plan",
            planner=type(self).__name__,
            max_invoke_retries=self._max_invoke_retries,
            max_json_repairs=self._max_json_repairs,
        )
        memory_context = await self._memory_context_for(
            goal,
            purpose="planner.initial_plan",
        )
        prompt = _planner_prompts.render_initial_plan_prompt(
            goal,
            memory_context=memory_context,
        )
        payload = await self._invoker.invoke_planner_json(
            prompt,
            repair_prompt=_planner_prompts.render_planner_json_repair_prompt,
            diagnostics=diagnostics,
            phase="initial",
        )
        try:
            return _diagnostics.with_planner_diagnostics(
                _execution_plan_from_payload(payload, goal),
                diagnostics,
            )
        except ValueError as first_error:
            repaired = await self._invoker.repair_planner_json(
                prompt,
                _json_dump(payload),
                reason=str(first_error),
                repair_prompt=_planner_prompts.render_planner_json_repair_prompt,
                diagnostics=diagnostics,
                phase="semantic_repair",
            )
            try:
                return _diagnostics.with_planner_diagnostics(
                    _execution_plan_from_payload(repaired, goal),
                    diagnostics,
                )
            except ValueError as repair_error:
                raise ValueError(
                    f"{first_error}; planner plan repair failed: {repair_error}"
                ) from repair_error

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        diagnostics = _diagnostics.planner_diagnostics(
            operation="revise_plan",
            planner=type(self).__name__,
            max_invoke_retries=self._max_invoke_retries,
            max_json_repairs=self._max_json_repairs,
        )
        memory_context = await self._memory_context_for(
            goal,
            purpose="planner.revise_plan",
            current_plan=current_plan,
            feedback=feedback,
        )
        prompt = _planner_prompts.render_revise_plan_prompt(
            goal,
            current_plan,
            feedback,
            memory_context=memory_context,
        )
        payload = await self._invoker.invoke_planner_json(
            prompt,
            repair_prompt=_planner_prompts.render_planner_json_repair_prompt,
            diagnostics=diagnostics,
            phase="revise",
        )
        try:
            delta = _plan_delta_from_payload(payload, goal, feedback)
            current_plan.apply_delta(delta)
            return _diagnostics.with_delta_diagnostics(delta, diagnostics)
        except ValueError as first_error:
            repaired = await self._invoker.repair_planner_json(
                prompt,
                _json_dump(payload),
                reason=str(first_error),
                repair_prompt=_planner_prompts.render_planner_json_repair_prompt,
                diagnostics=diagnostics,
                phase="semantic_repair",
            )
            try:
                delta = _plan_delta_from_payload(repaired, goal, feedback)
                current_plan.apply_delta(delta)
                return _diagnostics.with_delta_diagnostics(delta, diagnostics)
            except ValueError as repair_error:
                raise ValueError(
                    f"{first_error}; planner delta repair failed: {repair_error}"
                ) from repair_error

    async def _memory_context_for(
        self,
        goal: GoalSpec,
        *,
        purpose: str,
        current_plan: ExecutionPlan | None = None,
        feedback: tuple[str, ...] = (),
    ) -> MemoryContext | None:
        policy = goal.memory_context
        if (
            self._memory_retriever is None
            or policy is None
            or not policy.enabled
        ):
            return None
        return await self._memory_retriever.retrieve(
            MemoryContextRequest(
                policy=policy,
                purpose=purpose,
                workflow_name=current_plan.name if current_plan is not None else None,
                goal_objective=goal.objective,
                current_plan_name=(
                    current_plan.name if current_plan is not None else None
                ),
                feedback_refs=feedback,
            )
        )
