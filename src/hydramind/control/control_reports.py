"""Agent-report decision helpers for ``ControlPlane``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from hydramind.control.control_apply import ApplyIntentExecutor, coerce_apply_intent
from hydramind.control.control_decisions import RuntimeDecision
from hydramind.control.models import (
    AgentReport,
    ApplyIntent,
    Gate,
    NodeAttempt,
    NodeState,
    RuntimeSession,
)
from hydramind.control.session_service import ExecutionLeaseError, SessionService
from hydramind.control.states import AttemptStatus, GateOutcome, NodeStatus, SessionStatus

GateFn = Callable[
    [RuntimeSession, NodeState, AgentReport],
    Awaitable[Gate | None],
]
"""Gate evaluator signature. Return ``None`` for "no gate applies"."""


class ApplyDeriver(Protocol):
    """Converts an AgentReport into the ``ApplyIntent`` the node should execute.

    Default implementation just promotes the report's ``output`` to the
    node's ``COMPLETED`` state. Users override to add domain logic.
    """

    async def __call__(
        self, session: RuntimeSession, node: NodeState, report: AgentReport
    ) -> ApplyIntent: ...


async def default_apply_deriver(
    session: RuntimeSession, node: NodeState, report: AgentReport
) -> ApplyIntent:
    """Record the agent-reported NODE outcome.

    The node ran and reported success, so its output is persisted and the node
    is marked complete (node-level COMPLETE-on-success is mechanism). This is a
    RECORD of what the agent reported; control never invents SESSION completion
    (ADR-0008).
    """

    return ApplyIntent.complete(
        node.key,
        output=report.persisted_output(),
        authorization={"source": "default_apply_deriver"},
    )


class ReportDecisionExecutor:
    """Consumes agent reports and records their authorized control transition."""

    def __init__(
        self,
        service: SessionService,
        apply_executor: ApplyIntentExecutor,
        *,
        gate_fn: GateFn | None = None,
        apply_deriver: ApplyDeriver | None = None,
    ) -> None:
        self._service = service
        self._apply_executor = apply_executor
        self._gate_fn = gate_fn
        self._apply_deriver = apply_deriver or default_apply_deriver

    async def open_runtime_decision(
        self,
        session_id: str,
        report: AgentReport,
    ) -> RuntimeDecision:
        session = await self._service.get_session(session_id)
        if session.status not in {SessionStatus.RUNNING, SessionStatus.RESUMING}:
            if session.status is SessionStatus.QUEUED:
                await self._service.mark_session_running(session_id)
                session = await self._service.get_session(session_id)
            else:
                raise RuntimeError(
                    f"session {session_id} status {session.status} cannot accept reports"
                )

        node = session.nodes.get(report.node_key)
        if node is None:
            raise RuntimeError(f"unknown node {report.node_key} in session {session_id}")

        # A report implies the node is being executed; auto-advance QUEUED -> RUNNING.
        if node.status is NodeStatus.QUEUED:
            await self._service.start_node(session_id, report.node_key)
            session = await self._service.get_session(session_id)
            node = session.nodes[report.node_key]

        await self._assert_report_execution_lease(session_id, node, report)

        if report.error:
            return await self._apply_executor.apply_node_intent(
                session_id,
                ApplyIntent.fail(
                    report.node_key,
                    error=report.error,
                    authorization={"source": "agent_report_error"},
                ),
                report=report,
            )

        latest = node.latest_attempt()
        if latest is None or latest.status is not AttemptStatus.RUNNING:
            await self._service.start_attempt(
                session_id,
                report.node_key,
                trace_id=report.trace_id,
            )
        await self._service.consume_agent_report(session_id, report)

        # Re-read so we see the consumed report.
        session = await self._service.get_session(session_id)
        node = session.nodes[report.node_key]

        gate = await self._gate_fn(session, node, report) if self._gate_fn else None
        if gate is not None and gate.outcome is GateOutcome.REQUIRES_DECISION:
            return await self._apply_executor.apply_node_intent(
                session_id,
                ApplyIntent.pause(
                    report.node_key,
                    gate=gate,
                    authorization={
                        "source": "gate_result",
                        "gate_name": gate.name,
                        "gate_outcome": gate.outcome.value,
                    },
                ),
                report=report,
            )

        if gate is not None and gate.outcome is GateOutcome.BLOCK:
            error = f"gate {gate.name} BLOCK"
            return await self._apply_executor.apply_node_intent(
                session_id,
                ApplyIntent.fail(
                    report.node_key,
                    error=error,
                    gate=gate,
                    authorization={
                        "source": "gate_result",
                        "gate_name": gate.name,
                        "gate_outcome": gate.outcome.value,
                    },
                ),
                report=report,
            )

        raw_intent = await self._apply_deriver(session, node, report)
        intent = coerce_apply_intent(
            raw_intent,
            default_node_key=report.node_key,
            gate=gate,
            authorization={
                "source": "gate_result" if gate is not None else "control_apply_deriver",
                "gate_name": gate.name if gate is not None else None,
                "gate_outcome": gate.outcome.value if gate is not None else None,
            },
        )
        return await self._apply_executor.apply_node_intent(session_id, intent, report=report)

    async def _assert_report_execution_lease(
        self,
        session_id: str,
        node: NodeState,
        report: AgentReport,
    ) -> None:
        latest = node.latest_attempt()
        if latest is None or not _has_execution_lease_metadata(latest):
            return
        if report.execution_id != latest.id:
            raise ExecutionLeaseError(
                f"agent report execution_id {report.execution_id!r} does not match "
                f"leased execution {latest.id!r}"
            )
        if report.lease_token is None:
            raise ExecutionLeaseError(
                f"agent report for execution {latest.id!r} requires a lease token"
            )
        await self._service.assert_execution_lease(
            session_id,
            latest.id,
            report.lease_token,
        )


def _has_execution_lease_metadata(attempt: NodeAttempt) -> bool:
    return bool(attempt.lease_token or attempt.lease_owner or attempt.lease_expires_at is not None)


__all__ = [
    "ApplyDeriver",
    "GateFn",
    "ReportDecisionExecutor",
    "default_apply_deriver",
]
