"""Goal repair runtime adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from hydramind.control import (
    ControlPlane,
    DecisionAction,
    Gate,
    GateDecisionInput,
    RuntimeDecision,
    RuntimeDecisionKind,
)
from hydramind.orchestration.goal_repair import (
    feedback_for_replan,
    gate_has_failed_verifiers,
    should_approve_current_after_replan,
)
from hydramind.orchestration.planning import PlanDelta


class SessionResumer(Protocol):
    """Minimal session-resume surface needed by repair runtime."""

    async def resume_session(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> RuntimeDecision: ...


PlanRevisionFn = Callable[[str, tuple[str, ...]], Awaitable[PlanDelta]]
AgentForSessionFn = Callable[[str], Awaitable[SessionResumer]]


class GoalRepairRuntime:
    """Owns goal repair-loop mechanics without deciding plan content."""

    def __init__(
        self,
        *,
        control: ControlPlane,
        revise_goal_session: PlanRevisionFn,
        agent_for_session: AgentForSessionFn,
        max_repair_attempts: int = 1,
    ) -> None:
        self._control = control
        self._revise_goal_session = revise_goal_session
        self._agent_for_session = agent_for_session
        self._max_repair_attempts = max(0, max_repair_attempts)

    async def prepare_gate_decision(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> GateDecisionInput:
        if decision.action is not DecisionAction.REPLAN:
            return decision
        gate = await self._gate_for_decision(session_id, decision)
        feedback = feedback_for_replan(gate, decision.payload)
        delta = await self._revise_goal_session(session_id, feedback)
        if should_approve_current_after_replan(decision, gate, delta):
            payload = {
                **decision.payload,
                "original_action": decision.action.value,
                "replan_applied_before_approval": True,
            }
            return decision.model_copy(
                update={
                    "action": DecisionAction.APPROVE,
                    "payload": payload,
                }
            )
        return decision

    async def maybe_auto_repair(
        self,
        session_id: str,
        decision: RuntimeDecision,
    ) -> RuntimeDecision:
        """Run bounded agent-driven verifier-failure repair."""

        while (
            self._max_repair_attempts > 0
            and decision.kind is RuntimeDecisionKind.AWAIT_GATE
        ):
            gate = decision.gate
            if gate is None or not gate_has_failed_verifiers(gate):
                return decision
            reserved = await self._control.service.reserve_auto_repair_attempt(
                session_id,
                max_attempts=self._max_repair_attempts,
            )
            if not reserved:
                return decision
            feedback = feedback_for_replan(gate, {})
            delta = await self._revise_goal_session(session_id, feedback)
            if not _delta_has_changes(delta):
                return decision
            repair = repair_decision_for_delta(gate, delta)
            agent = await self._agent_for_session(session_id)
            decision = await agent.resume_session(session_id, repair)
        return decision

    async def _gate_for_decision(
        self,
        session_id: str,
        decision: GateDecisionInput,
    ) -> Gate:
        session = await self._control.service.get_session(session_id)
        for node in session.nodes.values():
            for gate in node.gates:
                if gate.id == decision.gate_id:
                    return gate
        raise RuntimeError(f"gate {decision.gate_id!r} not found in session {session_id}")


def repair_decision_for_delta(
    gate: Gate,
    delta: PlanDelta,
) -> GateDecisionInput:
    """Build the gate decision that records an agent-applied repair."""

    if should_approve_current_after_replan(
        GateDecisionInput(
            gate_id=gate.id,
            action=DecisionAction.REPLAN,
            actor="orchestrator",
            payload={"approve_current_after_replan": True},
        ),
        gate,
        delta,
    ):
        return GateDecisionInput(
            gate_id=gate.id,
            action=DecisionAction.APPROVE,
            actor="orchestrator",
            rationale="agent-driven verifier feedback repair",
            payload={
                "original_action": DecisionAction.REPLAN.value,
                "replan_applied_before_approval": True,
            },
        )
    return GateDecisionInput(
        gate_id=gate.id,
        action=DecisionAction.REPLAN,
        actor="orchestrator",
        rationale="agent-driven verifier feedback repair",
        payload={"feedback": list(delta.feedback_refs)},
    )


def _delta_has_changes(delta: PlanDelta) -> bool:
    return bool(delta.add_tasks or delta.update_tasks or delta.remove_task_keys)


__all__ = [
    "GoalRepairRuntime",
    "repair_decision_for_delta",
]
