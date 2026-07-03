"""Goal session plan-state adapter."""

from __future__ import annotations

from typing import Any

from hydramind.control import ControlPlane, WorkflowRevision
from hydramind.orchestration.planning import ExecutionPlan, PlanDelta


class GoalSessionState:
    """Owns goal-session metadata and plan revision persistence mechanics."""

    def __init__(self, control: ControlPlane) -> None:
        self._control = control

    def session_metadata(
        self,
        plan: ExecutionPlan,
        *,
        metadata: dict[str, object] | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        session_metadata: dict[str, object] = {}
        session_metadata.update(plan.as_session_metadata())
        if metadata:
            session_metadata.update(metadata)
        if runtime_overrides:
            existing = session_metadata.get("runtime_overrides")
            merged: dict[str, Any] = {}
            if isinstance(existing, dict):
                merged.update(existing)
            merged.update(runtime_overrides)
            session_metadata["runtime_overrides"] = merged
        return session_metadata

    async def plan_for_session(self, session_id: str) -> ExecutionPlan:
        session = await self._control.service.get_session(session_id)
        raw_plan = session.metadata.get("execution_plan")
        if not isinstance(raw_plan, dict):
            raise RuntimeError(
                f"session {session_id} does not contain goal execution_plan metadata"
            )
        return ExecutionPlan.model_validate(raw_plan)

    async def apply_plan_delta(
        self,
        session_id: str,
        delta: PlanDelta,
    ) -> ExecutionPlan:
        current = await self.plan_for_session(session_id)
        updated = current.apply_delta(delta)
        await self._control.apply_workflow_revision(
            session_id,
            WorkflowRevision(
                current_blueprint=current.to_blueprint(),
                revised_blueprint=updated.to_blueprint(),
                changed_node_keys=tuple(task.key for task in delta.update_tasks),
                reason=delta.rationale,
                feedback_refs=delta.feedback_refs,
                metadata=updated.as_session_metadata(),
            ),
        )
        return updated


__all__ = ["GoalSessionState"]
