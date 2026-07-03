"""Apply-intent execution helpers for ``ControlPlane``."""

from __future__ import annotations

from typing import Any

from hydramind.control.control_decisions import RuntimeDecision, RuntimeDecisionKind
from hydramind.control.models import (
    AgentReport,
    ApplyIntent,
    Gate,
    RuntimeSession,
)
from hydramind.control.session_service import SessionService
from hydramind.control.states import ApplyIntentKind, NodeStatus, SessionStatus


class ApplyIntentExecutor:
    """Executes control-plane apply intents through ``SessionService``."""

    def __init__(self, service: SessionService) -> None:
        self._service = service

    async def apply_node_intent(
        self,
        session_id: str,
        intent: ApplyIntent,
        *,
        report: AgentReport | None = None,
    ) -> RuntimeDecision:
        if intent.kind is ApplyIntentKind.WORKFLOW_REVISION:
            raise ValueError("workflow revision intent is not a node transition")
        node_key = _required_node_key(intent)
        execution_id = report.execution_id if report is not None else None

        if intent.kind is ApplyIntentKind.PAUSE:
            gate = _required_gate(intent)
            recorded = await self._service.record_gate(
                session_id,
                node_key,
                name=gate.name,
                outcome=gate.outcome,
                detail={
                    **gate.detail,
                    "_apply_intent": _intent_evidence(intent),
                },
            )
            if report is not None:
                await self._release_report_execution_lease_if_present(session_id, report)
            await self._service.mark_node_pending_gate(session_id, node_key)
            await self._service.mark_session_waiting_gate(session_id)
            return RuntimeDecision(
                kind=RuntimeDecisionKind.AWAIT_GATE,
                session_id=session_id,
                node_key=node_key,
                node_status=NodeStatus.PENDING_GATE,
                gate=recorded,
                execution_id=execution_id,
            )

        if intent.kind is ApplyIntentKind.FAIL:
            node = await self._service.get_node(session_id, node_key)
            error = intent.error or "control-plane fail intent"
            if node.status is not NodeStatus.FAILED:
                await self._service.fail_node(session_id, node_key, error=error)
            await self._service.fail_session(session_id, error=error)
            return RuntimeDecision(
                kind=RuntimeDecisionKind.FAIL,
                session_id=session_id,
                node_key=node_key,
                node_status=NodeStatus.FAILED,
                gate=intent.gate,
                error=error,
                execution_id=execution_id,
            )

        if intent.kind is ApplyIntentKind.REQUEUE:
            await self._resume_session_if_waiting(session_id)
            await self._service.requeue_node_for_retry(
                session_id,
                node_key,
                reason=intent.reason or "control-plane requeue intent",
                actor=_authorization_actor(intent),
            )
            await self._ensure_session_running(session_id)
            return RuntimeDecision(
                kind=RuntimeDecisionKind.CONTINUE,
                session_id=session_id,
                node_key=node_key,
                node_status=NodeStatus.QUEUED,
                gate=intent.gate,
                execution_id=execution_id,
            )

        if intent.kind is ApplyIntentKind.COMPLETE:
            # Records node completion only. Control does NOT decide the session is
            # done — it never scans node statuses to transition the SESSION to
            # terminal. Session completion is the orchestrator's decision, recorded
            # via ``ControlPlane.record_session_complete`` (ADR-0008).
            await self._resume_session_if_waiting(session_id)
            await self._service.complete_node(
                session_id,
                node_key,
                output=intent.output,
            )
            await self._ensure_session_running(session_id)
            return RuntimeDecision(
                kind=RuntimeDecisionKind.CONTINUE,
                session_id=session_id,
                node_key=node_key,
                node_status=NodeStatus.COMPLETED,
                gate=intent.gate,
                execution_id=execution_id,
            )

        raise ValueError(f"unsupported apply intent kind {intent.kind}")

    async def apply_workflow_revision_intent(
        self,
        session_id: str,
        intent: ApplyIntent,
    ) -> RuntimeSession:
        if intent.kind is not ApplyIntentKind.WORKFLOW_REVISION:
            raise ValueError("expected workflow revision intent")
        if intent.workflow_revision is None:
            raise ValueError("workflow revision intent requires workflow_revision")
        return await self._service.apply_workflow_revision(session_id, intent.workflow_revision)

    async def _resume_session_if_waiting(self, session_id: str) -> None:
        session = await self._service.get_session(session_id)
        if session.status is SessionStatus.WAITING_GATE:
            await self._service.mark_session_resuming(session_id)

    async def _ensure_session_running(self, session_id: str) -> None:
        session = await self._service.get_session(session_id)
        if session.status is SessionStatus.RESUMING:
            await self._service.mark_session_running(session_id)

    async def _release_report_execution_lease_if_present(
        self,
        session_id: str,
        report: AgentReport,
    ) -> None:
        if report.execution_id is None or report.lease_token is None:
            return
        await self._service.release_execution_lease(
            session_id,
            report.execution_id,
            lease_token=report.lease_token,
        )


def coerce_apply_intent(
    value: ApplyIntent,
    *,
    default_node_key: str,
    gate: Gate | None,
    authorization: dict[str, Any],
) -> ApplyIntent:
    if not isinstance(value, ApplyIntent):
        raise TypeError(f"apply deriver returned unsupported value {type(value)!r}")
    intent = value

    if intent.node_key is None and intent.kind is not ApplyIntentKind.WORKFLOW_REVISION:
        intent = intent.model_copy(update={"node_key": default_node_key})
    if gate is not None and intent.gate is None:
        intent = intent.model_copy(update={"gate": gate})
    if not intent.authorization:
        intent = intent.model_copy(update={"authorization": authorization})
    return intent


def _required_node_key(intent: ApplyIntent) -> str:
    if intent.node_key is None:
        raise ValueError(f"{intent.kind.value} intent requires node_key")
    return intent.node_key


def _required_gate(intent: ApplyIntent) -> Gate:
    if intent.gate is None:
        raise ValueError("pause intent requires gate")
    return intent.gate


def _intent_evidence(intent: ApplyIntent) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "kind": intent.kind.value,
        "authorization": dict(intent.authorization),
    }
    if intent.reason:
        evidence["reason"] = intent.reason
    if intent.metadata:
        evidence["metadata"] = dict(intent.metadata)
    return evidence


def _authorization_actor(intent: ApplyIntent) -> str | None:
    actor = intent.authorization.get("actor")
    if isinstance(actor, str) and actor:
        return actor
    source = intent.authorization.get("source")
    return source if isinstance(source, str) and source else None


__all__ = ["ApplyIntentExecutor", "coerce_apply_intent"]
