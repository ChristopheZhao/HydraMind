"""ControlPlane — gate/apply loop facade.

The orchestrator calls ``open_runtime_decision`` once per agent report; the
public control plane delegates report ingestion to an internal report executor
and applies authorized transitions via ``SessionService``.
"""

from __future__ import annotations

from typing import Any

from hydramind.control.control_apply import ApplyIntentExecutor
from hydramind.control.control_decisions import (
    RuntimeDecision,
    RuntimeDecisionKind,
)
from hydramind.control.control_gate_decisions import GateDecisionExecutor
from hydramind.control.control_reports import (
    ApplyDeriver,
    GateFn,
    ReportDecisionExecutor,
    default_apply_deriver,
)
from hydramind.control.interaction_state import DurableInteraction
from hydramind.control.interaction_turn_lease import RecoveredDurableTurn
from hydramind.control.models import (
    AgentReport,
    ApplyIntent,
    GateDecisionInput,
    InteractionLogRecord,
    NodeAttempt,
    RuntimeSession,
    ToolExecution,
    WorkflowRevision,
)
from hydramind.control.session_service import SessionService
from hydramind.control.states import (
    NodeStatus,
    SessionStatus,
)
from hydramind.kernel.contracts import (
    InteractionStatus,
    MessageRole,
    TurnStatus,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome

__all__ = [
    "ApplyDeriver",
    "ControlPlane",
    "GateFn",
    "RuntimeDecision",
    "RuntimeDecisionKind",
    "default_apply_deriver",
]


class ControlPlane:
    """Single entry-point for orchestrator → control transitions."""

    def __init__(
        self,
        service: SessionService,
        *,
        gate_fn: GateFn | None = None,
        apply_deriver: ApplyDeriver | None = None,
    ) -> None:
        self._service = service
        self._apply_executor = ApplyIntentExecutor(service)
        self._report_executor = ReportDecisionExecutor(
            service,
            self._apply_executor,
            gate_fn=gate_fn,
            apply_deriver=apply_deriver,
        )
        self._gate_decision_executor = GateDecisionExecutor(
            service,
            self._apply_executor,
        )

    @property
    def service(self) -> SessionService:
        """The underlying SessionService. Read-only — never reach around it."""
        return self._service

    async def open_node_execution(
        self,
        session_id: str,
        node_key: str,
        *,
        trace_id: str | None = None,
    ) -> NodeAttempt:
        """Open the control-owned execution envelope before runtime work starts."""

        session = await self._service.get_session(session_id)
        if session.status not in {SessionStatus.RUNNING, SessionStatus.RESUMING}:
            if session.status is SessionStatus.QUEUED:
                await self._service.mark_session_running(session_id)
                session = await self._service.get_session(session_id)
            else:
                raise RuntimeError(
                    f"session {session_id} status {session.status} cannot open execution"
                )
        node = session.nodes.get(node_key)
        if node is None:
            raise RuntimeError(f"unknown node {node_key} in session {session_id}")
        if node.status is NodeStatus.QUEUED:
            await self._service.start_node(session_id, node_key)
        return await self._service.start_node_execution(
            session_id,
            node_key,
            trace_id=trace_id,
        )

    async def grant_node_execution_lease(
        self,
        session_id: str,
        node_key: str,
        execution_id: str,
        *,
        owner: str,
        ttl_seconds: int = 300,
    ) -> NodeAttempt:
        """Grant worker/runtime ownership for one control-owned execution."""

        return await self._service.grant_execution_lease(
            session_id,
            node_key,
            execution_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
        )

    async def heartbeat_node_execution_lease(
        self,
        session_id: str,
        execution_id: str,
        *,
        lease_token: str,
        ttl_seconds: int = 300,
    ) -> NodeAttempt:
        """Refresh worker/runtime ownership for one control-owned execution."""

        return await self._service.heartbeat_execution_lease(
            session_id,
            execution_id,
            lease_token=lease_token,
            ttl_seconds=ttl_seconds,
        )

    async def recover_expired_node_executions(
        self,
        session_id: str,
        *,
        actor: str | None = None,
    ) -> tuple[NodeAttempt, ...]:
        """Recover expired leased executions before orchestration scheduling."""

        return await self._service.recover_expired_node_executions(
            session_id,
            actor=actor,
        )

    async def record_tool_execution_started(
        self,
        session_id: str,
        execution_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        round_no: int,
        arguments: dict[str, Any] | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolExecution:
        """Record the start of one tool call through the control layer."""

        return await self._service.record_tool_execution_started(
            session_id,
            execution_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            round_no=round_no,
            arguments=arguments,
            trace_id=trace_id,
            metadata=metadata,
        )

    async def record_tool_execution_completed(
        self,
        session_id: str,
        execution_id: str,
        *,
        tool_call_id: str,
        is_error: bool,
        result_preview: dict[str, Any] | None = None,
        content_length: int | None = None,
        error: str | None = None,
    ) -> ToolExecution:
        """Record the result of one tool call through the control layer."""

        return await self._service.record_tool_execution_completed(
            session_id,
            execution_id,
            tool_call_id=tool_call_id,
            is_error=is_error,
            result_preview=result_preview,
            content_length=content_length,
            error=error,
        )

    async def record_interaction_event(
        self,
        record: InteractionLogRecord,
    ) -> InteractionLogRecord:
        """Record one native MAS interaction event through the control layer."""

        return await self._service.record_interaction_event(
            record.session_id,
            record,
        )

    # ---- durable interaction state (authoritative, control-owned, S5a) -----

    async def start_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        node_key: str,
        execution_id: str,
        team_id: str,
        protocol_mode: str,
        topology: str,
        member_ids: tuple[str, ...] = (),
        workspace_id: str | None = None,
    ) -> DurableInteraction:
        """Create the authoritative durable interaction aggregate (S5a)."""

        return await self._service.start_interaction(
            session_id,
            interaction_id=interaction_id,
            node_key=node_key,
            execution_id=execution_id,
            team_id=team_id,
            protocol_mode=protocol_mode,
            topology=topology,
            member_ids=member_ids,
            workspace_id=workspace_id,
        )

    async def record_interaction_turn(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        agent_id: str,
        status: TurnStatus = TurnStatus.COMPLETED,
    ) -> DurableInteraction:
        """Record/advance an authoritative durable turn (S5a)."""

        return await self._service.record_interaction_turn(
            session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            agent_id=agent_id,
            status=status,
        )

    async def record_interaction_message(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        sender: str,
        content: str,
        role: MessageRole = MessageRole.AGENT,
        in_reply_to: str | None = None,
    ) -> DurableInteraction:
        """Record an authoritative durable message with FULL content (S5a)."""

        return await self._service.record_interaction_message(
            session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            sender=sender,
            content=content,
            role=role,
            in_reply_to=in_reply_to,
        )

    async def record_interaction_outcome(
        self,
        session_id: str,
        *,
        interaction_id: str,
        vote: VoteOutcome | None = None,
        coordinator: CoordinatorOutcome | None = None,
    ) -> DurableInteraction:
        """Record the typed protocol outcome on the durable interaction (S5a)."""

        return await self._service.record_interaction_outcome(
            session_id,
            interaction_id=interaction_id,
            vote=vote,
            coordinator=coordinator,
        )

    async def complete_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        status: InteractionStatus = InteractionStatus.COMPLETED,
        error: str | None = None,
    ) -> DurableInteraction:
        """Mark the durable interaction COMPLETED/FAILED (S5a)."""

        return await self._service.complete_interaction(
            session_id,
            interaction_id=interaction_id,
            status=status,
            error=error,
        )

    async def fail_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        error: str,
    ) -> DurableInteraction:
        """Mark the durable interaction FAILED with an error (S5a)."""

        return await self._service.fail_interaction(
            session_id,
            interaction_id=interaction_id,
            error=error,
        )

    async def get_durable_interaction(
        self, session_id: str, interaction_id: str
    ) -> DurableInteraction | None:
        """Read the authoritative durable interaction aggregate (S5a)."""

        return await self._service.get_durable_interaction(
            session_id, interaction_id
        )

    async def find_resumable_interaction(
        self, session_id: str, node_key: str
    ) -> DurableInteraction | None:
        """Find an in-progress durable interaction for ``(session, node)`` (S5b)."""

        return await self._service.find_resumable_interaction(
            session_id, node_key
        )

    async def mark_interaction_resumed(
        self,
        session_id: str,
        *,
        interaction_id: str,
        recovered_from_turn_index: int,
    ) -> DurableInteraction:
        """Stamp the recovery marker when resuming from durable state (S5b)."""

        return await self._service.mark_interaction_resumed(
            session_id,
            interaction_id=interaction_id,
            recovered_from_turn_index=recovered_from_turn_index,
        )

    # ---- durable turn lease (control-owned, S5b) --------------------------

    async def grant_turn_lease(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        agent_id: str,
        owner: str,
        ttl_seconds: int = 120,
        lease_token: str | None = None,
    ) -> DurableInteraction:
        """Grant a worker ownership of one durable interaction turn (S5b)."""

        return await self._service.grant_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            agent_id=agent_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            lease_token=lease_token,
        )

    async def heartbeat_turn_lease(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        lease_token: str,
        ttl_seconds: int = 120,
    ) -> DurableInteraction:
        """Refresh a worker's turn lease while it runs the turn (S5b)."""

        return await self._service.heartbeat_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            lease_token=lease_token,
            ttl_seconds=ttl_seconds,
        )

    async def release_turn_lease(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        lease_token: str,
        completed: bool = True,
    ) -> DurableInteraction:
        """Release a turn lease on success/handoff (S5b)."""

        return await self._service.release_turn_lease(
            session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            lease_token=lease_token,
            completed=completed,
        )

    async def recover_expired_turn_leases(
        self,
        session_id: str,
    ) -> tuple[RecoveredDurableTurn, ...]:
        """Recover expired turn leases and mark interactions resumable (S5b)."""

        return await self._service.recover_expired_turn_leases(session_id)

    async def reserve_auto_repair_attempt(
        self, session_id: str, *, max_attempts: int
    ) -> bool:
        """Reserve one durable, control-owned auto-repair attempt (S4b).

        Delegates to ``SessionService`` so the repair-budget cap is enforced
        from durable state and survives worker restart. Returns ``False`` when
        the budget is exhausted (or ``max_attempts <= 0``) without mutating.
        """

        return await self._service.reserve_auto_repair_attempt(
            session_id,
            max_attempts=max_attempts,
        )

    async def auto_repair_attempts_used(self, session_id: str) -> int:
        """Return the durable count of auto-repair attempts consumed (S4b)."""

        return await self._service.auto_repair_attempts_used(session_id)

    async def lookup_tool_effect_result(
        self, session_id: str, effect_fingerprint: str
    ) -> tuple[str, str] | None:
        """Return a prior successful tool effect result by fingerprint (S4c).

        Durable, control-owned tool side-effect dedupe across queue deliveries.
        """

        return await self._service.lookup_tool_effect_result(
            session_id, effect_fingerprint
        )

    async def record_tool_effect_result(
        self,
        session_id: str,
        effect_fingerprint: str,
        *,
        tool_call_id: str,
        content: str,
    ) -> None:
        """Durably record a successful tool effect result by fingerprint (S4c)."""

        await self._service.record_tool_effect_result(
            session_id,
            effect_fingerprint,
            tool_call_id=tool_call_id,
            content=content,
        )

    async def open_runtime_decision(self, session_id: str, report: AgentReport) -> RuntimeDecision:
        return await self._report_executor.open_runtime_decision(session_id, report)

    async def record_session_complete(
        self,
        session_id: str,
        *,
        summary: dict[str, Any] | None = None,
    ) -> RuntimeDecision:
        """Record that the orchestrator decided no work remains (ADR-0008).

        Decision authority for completion is the orchestrator agent's; control
        merely RECORDS the decision. This is the only path that transitions the
        session to ``COMPLETED`` — node-apply (``apply_node_intent`` COMPLETE)
        never scans node statuses to decide the session is done.
        """
        session = await self._service.get_session(session_id)
        if session.status is SessionStatus.RESUMING:
            await self._service.mark_session_running(session_id)
        await self._service.complete_session(session_id, summary=summary)
        completed = await self._service.get_session(session_id)
        node_status = (
            next(iter(completed.nodes.values())).status if completed.nodes else NodeStatus.COMPLETED
        )
        return RuntimeDecision(
            kind=RuntimeDecisionKind.COMPLETE,
            session_id=session_id,
            node_key="",
            node_status=node_status,
        )

    async def apply_decision(self, session_id: str, decision: GateDecisionInput) -> RuntimeDecision:
        """Apply an external gate decision (human or system) to a pending gate.

        Approving the FINAL gate here does NOT auto-complete the session: control
        records the node-level approval/completion only. Decision authority for
        session completion is the orchestrator (ADR-0008), which records it via
        ``record_session_complete``. The normal resume path (``resume_session``)
        chains ``run_session`` so the orchestrator still records completion.
        """
        return await self._gate_decision_executor.apply_decision(session_id, decision)

    async def apply_workflow_revision(
        self,
        session_id: str,
        revision: WorkflowRevision,
    ) -> RuntimeSession:
        """Apply a dynamic workflow revision through the control layer."""

        return await self._apply_workflow_revision_intent(
            session_id,
            ApplyIntent.graph_update(
                revision,
                authorization={"source": "control_api", "api": "apply_workflow_revision"},
            ),
        )

    async def _apply_node_intent(
        self,
        session_id: str,
        intent: ApplyIntent,
        *,
        report: AgentReport | None = None,
    ) -> RuntimeDecision:
        return await self._apply_executor.apply_node_intent(
            session_id,
            intent,
            report=report,
        )

    async def _apply_workflow_revision_intent(
        self,
        session_id: str,
        intent: ApplyIntent,
    ) -> RuntimeSession:
        return await self._apply_executor.apply_workflow_revision_intent(
            session_id,
            intent,
        )
