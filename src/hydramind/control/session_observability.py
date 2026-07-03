"""Observation helpers for ``SessionService``."""

from __future__ import annotations

from typing import Any

from hydramind.control.models import NodeAttempt, NodeState, RuntimeSession
from hydramind.control.states import NodeStatus, SessionStatus
from hydramind.observability.emitter import Emitter
from hydramind.observability.events import ObservationEvent, ObservationEventKind

SESSION_CREATED = ObservationEventKind.SESSION_CREATED
SESSION_RUNNING = ObservationEventKind.SESSION_RUNNING
SESSION_WAITING_GATE = ObservationEventKind.SESSION_WAITING_GATE
SESSION_RESUMING = ObservationEventKind.SESSION_RESUMING
SESSION_COMPLETED = ObservationEventKind.SESSION_COMPLETED
SESSION_FAILED = ObservationEventKind.SESSION_FAILED
SESSION_CANCELLED = ObservationEventKind.SESSION_CANCELLED
NODE_STARTED = ObservationEventKind.NODE_STARTED
NODE_PENDING_GATE = ObservationEventKind.NODE_PENDING_GATE
NODE_APPROVED = ObservationEventKind.NODE_APPROVED
NODE_NEEDS_REVISION = ObservationEventKind.NODE_NEEDS_REVISION
NODE_REVISED = ObservationEventKind.NODE_REVISED
NODE_COMPLETED = ObservationEventKind.NODE_COMPLETED
NODE_FAILED = ObservationEventKind.NODE_FAILED
ATTEMPT_STARTED = ObservationEventKind.ATTEMPT_STARTED
NODE_EXECUTION_STARTED = ObservationEventKind.NODE_EXECUTION_STARTED
NODE_EXECUTION_COMPLETED = ObservationEventKind.NODE_EXECUTION_COMPLETED
NODE_EXECUTION_FAILED = ObservationEventKind.NODE_EXECUTION_FAILED
NODE_EXECUTION_ABORTED = ObservationEventKind.NODE_EXECUTION_ABORTED
EXECUTION_LEASE_GRANTED = ObservationEventKind.EXECUTION_LEASE_GRANTED
EXECUTION_LEASE_HEARTBEAT = ObservationEventKind.EXECUTION_LEASE_HEARTBEAT
EXECUTION_LEASE_RELEASED = ObservationEventKind.EXECUTION_LEASE_RELEASED
GATE_RECORDED = ObservationEventKind.GATE_RECORDED
DECISION_APPLIED = ObservationEventKind.DECISION_APPLIED

_SESSION_STATUS_TO_EVENT_KIND: dict[SessionStatus, ObservationEventKind] = {
    SessionStatus.RUNNING: SESSION_RUNNING,
    SessionStatus.WAITING_GATE: SESSION_WAITING_GATE,
    SessionStatus.RESUMING: SESSION_RESUMING,
    SessionStatus.COMPLETED: SESSION_COMPLETED,
    SessionStatus.FAILED: SESSION_FAILED,
    SessionStatus.CANCELLED: SESSION_CANCELLED,
}

_NODE_STATUS_TO_EVENT_KIND: dict[NodeStatus, ObservationEventKind] = {
    NodeStatus.PENDING_GATE: NODE_PENDING_GATE,
    NodeStatus.APPROVED: NODE_APPROVED,
    NodeStatus.NEEDS_REVISION: NODE_NEEDS_REVISION,
    NodeStatus.QUEUED: NODE_REVISED,
}


class SessionEventEmitter:
    def __init__(self, emitter: Emitter | None) -> None:
        self._emitter = emitter

    async def emit(
        self,
        kind: ObservationEventKind,
        session_id: str,
        *,
        node_key: str | None = None,
        trace_id: str | None = None,
        execution_id: str | None = None,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._emitter is None:
            return
        await self._emitter.emit(
            ObservationEvent(
                kind=kind,
                session_id=session_id,
                node_key=node_key,
                trace_id=trace_id,
                execution_id=execution_id,
                actor=actor,
                detail=detail or {},
            )
        )


class SessionEventReporter:
    """Semantic event reporter for SessionService state changes."""

    def __init__(self, emitter: Emitter | None) -> None:
        self._events = SessionEventEmitter(emitter)

    async def session_created(
        self,
        session_id: str,
        *,
        workflow_name: str,
        node_count: int,
    ) -> None:
        await self._events.emit(
            SESSION_CREATED,
            session_id,
            detail=session_created_detail(
                workflow_name=workflow_name,
                node_count=node_count,
            ),
        )

    async def session_transition(
        self,
        session_id: str,
        *,
        session: RuntimeSession,
        status: SessionStatus,
    ) -> None:
        trace_id, execution_id = session_correlation(session)
        await self._events.emit(
            session_event_kind(status),
            session_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )

    async def node_started(self, session_id: str, *, node_key: str) -> None:
        await self._events.emit(NODE_STARTED, session_id, node_key=node_key)

    async def node_transition(
        self,
        session_id: str,
        *,
        node: NodeState,
        status: NodeStatus,
    ) -> None:
        event_kind = node_event_kind(status)
        if event_kind is None:
            return
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            event_kind,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
        )

    async def node_execution_completed(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        has_output: bool,
    ) -> None:
        await self._events.emit(
            NODE_EXECUTION_COMPLETED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            detail=node_execution_completed_detail(
                attempt,
                has_output=has_output,
            ),
        )

    async def node_completed(
        self,
        session_id: str,
        *,
        node: NodeState,
        has_output: bool,
    ) -> None:
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            NODE_COMPLETED,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
            detail=node_output_detail(has_output=has_output),
        )

    async def node_execution_failed(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        error: str,
    ) -> None:
        await self._events.emit(
            NODE_EXECUTION_FAILED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            detail=node_execution_failed_detail(attempt, error=error),
        )

    async def node_failed(
        self,
        session_id: str,
        *,
        node: NodeState,
        error: str,
    ) -> None:
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            NODE_FAILED,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
            detail=node_error_detail(error=error),
        )

    async def node_execution_aborted(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        reason: str,
        actor: str | None = None,
    ) -> None:
        await self._events.emit(
            NODE_EXECUTION_ABORTED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=actor,
            detail=node_execution_aborted_detail(attempt, reason=reason),
        )

    async def expired_node_execution_aborted(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        lease_owner: str | None,
        lease_expires_at: str | None,
        actor: str | None = None,
    ) -> None:
        await self._events.emit(
            NODE_EXECUTION_ABORTED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=actor,
            detail=expired_execution_aborted_detail(
                attempt,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            ),
        )

    async def node_revised(
        self,
        session_id: str,
        *,
        node: NodeState,
        reason: str,
        actor: str | None = None,
    ) -> None:
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            NODE_REVISED,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
            actor=actor,
            detail=node_revision_detail(reason=reason),
        )

    async def recovered_node_revised(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        actor: str | None = None,
    ) -> None:
        await self._events.emit(
            NODE_REVISED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=actor,
            detail=recovered_node_revised_detail(attempt),
        )

    async def attempt_started(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        trace_id: str | None,
    ) -> None:
        await self._events.emit(
            ATTEMPT_STARTED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            detail=attempt_started_detail(attempt, trace_id=trace_id),
        )

    async def node_execution_started(
        self,
        session_id: str,
        *,
        node_key: str,
        attempt: NodeAttempt,
        trace_id: str | None,
    ) -> None:
        await self._events.emit(
            NODE_EXECUTION_STARTED,
            session_id,
            node_key=node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            detail=node_execution_started_detail(attempt, trace_id=trace_id),
        )

    async def execution_lease_granted(
        self,
        session_id: str,
        *,
        attempt: NodeAttempt,
        lease_expires_at: str,
    ) -> None:
        await self._events.emit(
            EXECUTION_LEASE_GRANTED,
            session_id,
            node_key=attempt.node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=attempt.lease_owner,
            detail=execution_lease_detail(
                lease_owner=attempt.lease_owner,
                lease_expires_at=lease_expires_at,
            ),
        )

    async def execution_lease_heartbeat(
        self,
        session_id: str,
        *,
        attempt: NodeAttempt,
        lease_expires_at: str,
    ) -> None:
        await self._events.emit(
            EXECUTION_LEASE_HEARTBEAT,
            session_id,
            node_key=attempt.node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=attempt.lease_owner,
            detail=execution_lease_detail(
                lease_owner=attempt.lease_owner,
                lease_expires_at=lease_expires_at,
            ),
        )

    async def execution_lease_released(
        self,
        session_id: str,
        *,
        attempt: NodeAttempt,
        owner: str | None,
    ) -> None:
        await self._events.emit(
            EXECUTION_LEASE_RELEASED,
            session_id,
            node_key=attempt.node_key,
            trace_id=attempt.trace_id,
            execution_id=attempt.id,
            actor=owner,
            detail=execution_lease_released_detail(lease_owner=owner),
        )

    async def gate_recorded(
        self,
        session_id: str,
        *,
        node: NodeState,
        gate_id: str,
        gate_name: str,
        outcome: str,
    ) -> None:
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            GATE_RECORDED,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
            detail=gate_recorded_detail(
                gate_id=gate_id,
                gate_name=gate_name,
                outcome=outcome,
            ),
        )

    async def gate_decision_applied(
        self,
        session_id: str,
        *,
        node: NodeState,
        gate_id: str,
        action: str,
        actor: str,
        target_node_status: str,
    ) -> None:
        trace_id, execution_id = node_correlation(node)
        await self._events.emit(
            DECISION_APPLIED,
            session_id,
            node_key=node.key,
            trace_id=trace_id,
            execution_id=execution_id,
            actor=actor,
            detail=gate_decision_detail(
                gate_id=gate_id,
                action=action,
                target_node_status=target_node_status,
            ),
        )


def session_event_kind(status: SessionStatus) -> ObservationEventKind:
    return _SESSION_STATUS_TO_EVENT_KIND[status]


def node_event_kind(status: NodeStatus) -> ObservationEventKind | None:
    return _NODE_STATUS_TO_EVENT_KIND.get(status)


def session_created_detail(*, workflow_name: str, node_count: int) -> dict[str, Any]:
    return {"workflow_name": workflow_name, "node_count": node_count}


def node_output_detail(*, has_output: bool) -> dict[str, Any]:
    return {"has_output": has_output}


def node_error_detail(*, error: str) -> dict[str, Any]:
    return {"error": error}


def node_revision_detail(*, reason: str) -> dict[str, Any]:
    return {"reason": reason}


def attempt_started_detail(
    attempt: NodeAttempt,
    *,
    trace_id: str | None,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": trace_id,
    }


def node_execution_started_detail(
    attempt: NodeAttempt,
    *,
    trace_id: str | None,
) -> dict[str, Any]:
    return {
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": trace_id,
    }


def node_execution_completed_detail(
    attempt: NodeAttempt,
    *,
    has_output: bool,
) -> dict[str, Any]:
    return {
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": attempt.trace_id,
        "has_output": has_output,
    }


def node_execution_failed_detail(
    attempt: NodeAttempt,
    *,
    error: str,
) -> dict[str, Any]:
    return {
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": attempt.trace_id,
        "error": error,
    }


def node_execution_aborted_detail(
    attempt: NodeAttempt,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": attempt.trace_id,
        "reason": reason,
    }


def expired_execution_aborted_detail(
    attempt: NodeAttempt,
    *,
    lease_owner: str | None,
    lease_expires_at: str | None,
) -> dict[str, Any]:
    return {
        "execution_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "trace_id": attempt.trace_id,
        "reason": "execution_lease_expired",
        "lease_owner": lease_owner,
        "lease_expires_at": lease_expires_at,
    }


def recovered_node_revised_detail(attempt: NodeAttempt) -> dict[str, Any]:
    return {
        "reason": "expired_execution_lease_recovered",
        "execution_id": attempt.id,
    }


def execution_lease_detail(
    *,
    lease_owner: str | None,
    lease_expires_at: str,
) -> dict[str, Any]:
    return {
        "lease_owner": lease_owner,
        "lease_expires_at": lease_expires_at,
    }


def execution_lease_released_detail(*, lease_owner: str | None) -> dict[str, Any]:
    return {"lease_owner": lease_owner}


def gate_recorded_detail(
    *,
    gate_id: str,
    gate_name: str,
    outcome: str,
) -> dict[str, Any]:
    return {"gate_id": gate_id, "gate_name": gate_name, "outcome": outcome}


def gate_decision_detail(
    *,
    gate_id: str,
    action: str,
    target_node_status: str,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "action": action,
        "target_node_status": target_node_status,
    }


def session_correlation(session: RuntimeSession) -> tuple[str | None, str | None]:
    attempts: list[NodeAttempt] = []
    for node in session.nodes.values():
        attempt = node.latest_attempt()
        if attempt is not None:
            attempts.append(attempt)
    if not attempts:
        return None, None
    latest = max(
        attempts,
        key=lambda attempt: attempt.finished_at or attempt.started_at,
    )
    return latest.trace_id, latest.id


def node_correlation(node: NodeState) -> tuple[str | None, str | None]:
    latest = node.latest_attempt()
    if latest is None:
        return None, None
    return latest.trace_id, latest.id
