"""SessionService — the only public API for mutating RuntimeSession.

See ``docs/architecture/20-control-plane.md`` §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import hydramind.control.interaction_turn_lease as turn_lease_bookkeeping
import hydramind.control.session_durable_interactions as durable_interaction_bookkeeping
import hydramind.control.session_execution as execution_bookkeeping
import hydramind.control.session_gates as gate_bookkeeping
import hydramind.control.session_interactions as interaction_bookkeeping
import hydramind.control.session_lifecycle as session_lifecycle
import hydramind.control.session_node_lifecycle as node_lifecycle
import hydramind.control.session_observability as session_observability
import hydramind.control.session_persistence as session_persistence
import hydramind.control.session_workflow as workflow_bookkeeping
from hydramind.control.interaction_state import (
    DurableInteraction,
    durable_interaction_id,
)
from hydramind.control.interaction_turn_lease import (
    RecoveredDurableTurn,
)
from hydramind.control.models import (
    AgentReport,
    Gate,
    GateDecisionInput,
    IdempotencyRecord,
    InteractionLogRecord,
    NodeAttempt,
    NodeState,
    RuntimeSession,
    ToolExecution,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    WorkflowRevision,
)
from hydramind.control.session_execution import ExecutionLeaseError
from hydramind.control.session_gates import GateNotFoundError
from hydramind.control.states import (
    AttemptStatus,
    GateOutcome,
    NodeStatus,
    SessionStatus,
    is_valid_attempt_transition,
    is_valid_node_transition,
)
from hydramind.control.store import SessionStore
from hydramind.kernel.contracts import (
    InteractionStatus,
    MessageRole,
    TurnStatus,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome
from hydramind.observability.emitter import Emitter


class InvalidTransitionError(RuntimeError):
    """Raised when a caller requests a state transition not on the matrix."""


class SessionNotFoundError(LookupError):
    """Raised when an operation targets an unknown session id."""


class NodeNotFoundError(LookupError):
    """Raised when an operation targets an unknown node within a session."""


def _now() -> datetime:
    return datetime.now(UTC)


def _tool_effect_key(session_id: str, effect_fingerprint: str) -> str:
    """Durable ledger key for one session-scoped tool effect (S4c)."""

    return f"tool-effect:{session_id}:{effect_fingerprint}"


__all__ = [
    "ExecutionLeaseError",
    "GateNotFoundError",
    "InvalidTransitionError",
    "NodeNotFoundError",
    "SessionNotFoundError",
    "SessionService",
]


class SessionService:
    """Public CRUD + state-transition surface over a SessionStore.

    P0 assumes single-writer per session id; the framework caller is responsible
    for that guarantee. Distributed-worker lease tokens are P1.
    """

    def __init__(
        self,
        store: SessionStore,
        *,
        emitter: Emitter | None = None,
    ) -> None:
        self._sessions = session_persistence.SessionRepository(store)
        self._events = session_observability.SessionEventReporter(emitter)

    # ---- lifecycle ---------------------------------------------------------

    async def create_session(
        self,
        blueprint: WorkflowBlueprint,
        *,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeSession:
        session = session_lifecycle.create_runtime_session(
            blueprint,
            input_payload=input_payload or {},
            metadata=metadata or {},
        )
        await self._sessions.put(session)
        await self._events.session_created(
            session.id,
            workflow_name=blueprint.name,
            node_count=len(session.nodes),
        )
        return session

    async def get_session(self, session_id: str) -> RuntimeSession:
        return await self._sessions.get_required(
            session_id,
            missing=SessionNotFoundError,
        )

    async def mark_session_running(self, session_id: str) -> RuntimeSession:
        return await self._session_transition(session_id, SessionStatus.RUNNING)

    async def mark_session_waiting_gate(self, session_id: str) -> RuntimeSession:
        return await self._session_transition(session_id, SessionStatus.WAITING_GATE)

    async def mark_session_resuming(self, session_id: str) -> RuntimeSession:
        return await self._session_transition(session_id, SessionStatus.RESUMING)

    async def complete_session(
        self, session_id: str, *, summary: dict[str, Any] | None = None
    ) -> RuntimeSession:
        session = await self._session_transition(session_id, SessionStatus.COMPLETED)
        if summary:
            session.summary_output = summary
            await self._sessions.put(session)
        return session

    async def fail_session(self, session_id: str, *, error: str) -> RuntimeSession:
        session = await self._session_transition(session_id, SessionStatus.FAILED)
        session.error_message = error
        await self._sessions.put(session)
        return session

    async def cancel_session(self, session_id: str) -> RuntimeSession:
        return await self._session_transition(session_id, SessionStatus.CANCELLED)

    async def update_session_metadata(
        self,
        session_id: str,
        metadata: dict[str, Any],
        *,
        merge: bool = True,
    ) -> RuntimeSession:
        session = await self.get_session(session_id)
        session_lifecycle.update_session_metadata(
            session,
            metadata,
            merge=merge,
            now=_now(),
        )
        await self._sessions.put(session)
        return session

    async def add_workflow_nodes(
        self,
        session_id: str,
        nodes: tuple[WorkflowNodeSpec, ...],
    ) -> RuntimeSession:
        session = await self.get_session(session_id)
        session_lifecycle.add_workflow_nodes(session, nodes, now=_now())
        await self._sessions.put(session)
        return session

    async def apply_workflow_revision(
        self,
        session_id: str,
        revision: WorkflowRevision,
    ) -> RuntimeSession:
        """Apply a workflow graph revision while preserving node history."""

        session = await self.get_session(session_id)
        workflow_bookkeeping.apply_workflow_revision(
            session,
            revision,
            now=_now(),
            transition_node=self._node_transition,
            invalid_transition=self._raise_invalid_transition,
        )
        await self._sessions.put(session)
        return session

    # ---- node --------------------------------------------------------------

    async def start_node(self, session_id: str, node_key: str) -> NodeState:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        self._node_transition(node, NodeStatus.RUNNING)
        await self._sessions.put(session)
        await self._events.node_started(session_id, node_key=node_key)
        return node

    async def mark_node_pending_gate(self, session_id: str, node_key: str) -> NodeState:
        return await self._node_only_transition(session_id, node_key, NodeStatus.PENDING_GATE)

    async def approve_node(self, session_id: str, node_key: str) -> NodeState:
        return await self._node_only_transition(session_id, node_key, NodeStatus.APPROVED)

    async def mark_node_needs_revision(
        self, session_id: str, node_key: str
    ) -> NodeState:
        return await self._node_only_transition(
            session_id, node_key, NodeStatus.NEEDS_REVISION
        )

    async def complete_node(
        self, session_id: str, node_key: str, *, output: dict[str, Any] | None = None
    ) -> NodeState:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        self._node_transition(node, NodeStatus.COMPLETED)
        completed = node_lifecycle.complete_running_attempt(
            node,
            output=output,
            now=_now(),
            transition_attempt=self._attempt_transition,
        )
        if completed is not None:
            await self._events.node_execution_completed(
                session_id,
                node_key=node_key,
                attempt=completed,
                has_output=output is not None,
            )
        await self._sessions.put(session)
        await self._events.node_completed(
            session_id,
            node=node,
            has_output=output is not None,
        )
        return node

    async def fail_node(
        self, session_id: str, node_key: str, *, error: str
    ) -> NodeState:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        self._node_transition(node, NodeStatus.FAILED)
        failed = node_lifecycle.fail_running_attempt(
            node,
            error=error,
            now=_now(),
            transition_attempt=self._attempt_transition,
        )
        if failed is not None:
            await self._events.node_execution_failed(
                session_id,
                node_key=node_key,
                attempt=failed,
                error=error,
            )
        await self._sessions.put(session)
        await self._events.node_failed(
            session_id,
            node=node,
            error=error,
        )
        return node

    async def revise_back_to_queued(self, session_id: str, node_key: str) -> NodeState:
        return await self.requeue_node_for_retry(
            session_id,
            node_key,
            reason="revision requested",
        )

    async def requeue_node_for_retry(
        self,
        session_id: str,
        node_key: str,
        *,
        reason: str = "retry requested",
        actor: str | None = None,
    ) -> NodeState:
        """Return a node to QUEUED and close any running execution envelope."""

        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        now = _now()
        aborted = node_lifecycle.abort_and_requeue_node(
            node,
            reason=reason,
            now=now,
            transition_attempt=self._attempt_transition,
            transition_node=self._node_transition,
        )
        session.updated_at = now
        await self._sessions.put(session)

        if aborted is not None:
            await self._events.node_execution_aborted(
                session_id,
                node_key=node_key,
                attempt=aborted,
                actor=actor,
                reason=reason,
            )
        await self._events.node_revised(
            session_id,
            node=node,
            actor=actor,
            reason=reason,
        )
        return node

    # ---- attempt -----------------------------------------------------------

    async def start_attempt(
        self,
        session_id: str,
        node_key: str,
        *,
        trace_id: str | None = None,
    ) -> NodeAttempt:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        attempt = NodeAttempt(
            node_key=node_key,
            attempt_no=len(node.attempts) + 1,
            trace_id=trace_id,
        )
        node.attempts.append(attempt)
        node.updated_at = _now()
        await self._sessions.put(session)
        await self._events.attempt_started(
            session_id,
            node_key=node_key,
            attempt=attempt,
            trace_id=trace_id,
        )
        return attempt

    async def start_node_execution(
        self,
        session_id: str,
        node_key: str,
        *,
        trace_id: str | None = None,
    ) -> NodeAttempt:
        execution = await self.start_attempt(session_id, node_key, trace_id=trace_id)
        await self._events.node_execution_started(
            session_id,
            node_key=node_key,
            attempt=execution,
            trace_id=trace_id,
        )
        return execution

    async def recover_expired_node_executions(
        self,
        session_id: str,
        *,
        as_of: datetime | None = None,
        actor: str | None = None,
    ) -> tuple[NodeAttempt, ...]:
        """Abort expired leased node executions and make their nodes schedulable."""

        session = await self.get_session(session_id)
        now = as_of or _now()
        recovered = node_lifecycle.recover_expired_node_execution_leases(
            session,
            now=now,
            transition_attempt=self._attempt_transition,
            transition_node=self._node_transition,
        )

        if not recovered:
            return ()

        session.updated_at = now
        await self._sessions.put(session)
        for recovered_execution in recovered:
            attempt = recovered_execution.attempt
            expires_at = recovered_execution.lease_expires_at
            await self._events.expired_node_execution_aborted(
                session_id,
                node_key=recovered_execution.node_key,
                attempt=attempt,
                actor=actor,
                lease_owner=recovered_execution.lease_owner,
                lease_expires_at=(
                    expires_at.isoformat() if expires_at is not None else None
                ),
            )
            await self._events.recovered_node_revised(
                session_id,
                node_key=recovered_execution.node_key,
                attempt=attempt,
                actor=actor,
            )
        return tuple(recovered_execution.attempt for recovered_execution in recovered)

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
        """Record that a tool call started inside a node execution envelope."""

        session = await self.get_session(session_id)
        now = _now()
        tool = execution_bookkeeping.record_tool_execution_started(
            session,
            execution_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            round_no=round_no,
            arguments=arguments,
            trace_id=trace_id,
            metadata=metadata,
            now=now,
        )
        await self._sessions.put(session)
        return tool

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
        """Record a tool result under the control-owned node execution."""

        session = await self.get_session(session_id)
        now = _now()
        tool = execution_bookkeeping.record_tool_execution_completed(
            session,
            execution_id,
            tool_call_id=tool_call_id,
            is_error=is_error,
            result_preview=result_preview,
            content_length=content_length,
            error=error,
            now=now,
        )
        await self._sessions.put(session)
        return tool

    async def record_interaction_event(
        self,
        session_id: str,
        record: InteractionLogRecord,
    ) -> InteractionLogRecord:
        """Append one native MAS interaction log record through the control layer.

        Idempotent under duplicate delivery (S4c): the record is deduped by a
        stable idempotency key derived from its logical identity
        (interaction/event-kind/turn/actor/execution). A replayed interaction
        turn finds its key already in the durable ledger and is NOT appended a
        second time, so a duplicate queue delivery cannot double-count turns.
        """

        session = await self.get_session(session_id)
        key = interaction_bookkeeping.interaction_idempotency_key(record)
        if key in session.processed_idempotency_keys:
            return record
        recorded = interaction_bookkeeping.append_interaction_log(
            session,
            record,
            now=_now(),
        )
        session.processed_idempotency_keys[key] = IdempotencyRecord(
            key=key,
            kind="interaction",
            detail={
                "interaction_id": record.interaction_id,
                "event_kind": record.event_kind.value,
            },
        )
        await self._sessions.put(session)
        return recorded

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
        """Create the authoritative durable interaction aggregate (S5a).

        This is the AUTHORITATIVE durable form of native MAS interaction state —
        distinct from the ``InteractionLogRecord`` preview projection. Only
        ``SessionService`` writes it (single-writer). Idempotent: a replayed
        start returns the existing aggregate without resetting it.
        """

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.start_durable_interaction(
            session,
            interaction_id=interaction_id,
            node_key=node_key,
            execution_id=execution_id,
            team_id=team_id,
            protocol_mode=protocol_mode,
            topology=topology,
            member_ids=member_ids,
            workspace_id=workspace_id,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def record_interaction_turn(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        agent_id: str,
        status: TurnStatus = TurnStatus.COMPLETED,
    ) -> DurableInteraction:
        """Record/advance an authoritative durable turn (S5a).

        Idempotent by ``turn_index`` + ``status``: a duplicate delivery of the
        same logical turn is not double-appended. Bumps the interaction's
        version/append sequence on a real change.
        """

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.record_durable_turn(
            session,
            interaction_id=interaction_id,
            turn_index=turn_index,
            agent_id=agent_id,
            status=status,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

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
        """Record an authoritative durable message with FULL content (S5a).

        ``content`` is the full authoritative message, not the bounded preview
        carried by ``InteractionLogRecord``. Idempotent by the stable per-turn
        sender message id, so duplicate delivery does not double-append.
        """

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.record_durable_message(
            session,
            interaction_id=interaction_id,
            turn_index=turn_index,
            sender=sender,
            content=content,
            role=role,
            in_reply_to=in_reply_to,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def record_interaction_outcome(
        self,
        session_id: str,
        *,
        interaction_id: str,
        vote: VoteOutcome | None = None,
        coordinator: CoordinatorOutcome | None = None,
    ) -> DurableInteraction:
        """Record the typed protocol outcome on the durable interaction (S5a).

        Reuses the S3b ``VoteOutcome``/``CoordinatorOutcome`` typed contracts as
        the payload. Idempotent: a pre-existing outcome is left in place.
        """

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.record_durable_outcome(
            session,
            interaction_id=interaction_id,
            vote=vote,
            coordinator=coordinator,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def complete_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        status: InteractionStatus = InteractionStatus.COMPLETED,
        error: str | None = None,
    ) -> DurableInteraction:
        """Mark the durable interaction COMPLETED/FAILED (S5a, idempotent)."""

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.complete_durable_interaction(
            session,
            interaction_id=interaction_id,
            status=status,
            error=error,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def fail_interaction(
        self,
        session_id: str,
        *,
        interaction_id: str,
        error: str,
    ) -> DurableInteraction:
        """Mark the durable interaction FAILED with an error (S5a, idempotent)."""

        return await self.complete_interaction(
            session_id,
            interaction_id=interaction_id,
            status=InteractionStatus.FAILED,
            error=error,
        )

    async def get_durable_interaction(
        self, session_id: str, interaction_id: str
    ) -> DurableInteraction | None:
        """Read the authoritative durable interaction aggregate (S5a)."""

        session = await self.get_session(session_id)
        return session.durable_interactions.get(interaction_id)

    async def list_durable_interactions(
        self, session_id: str
    ) -> tuple[DurableInteraction, ...]:
        """Read all authoritative durable interaction aggregates (S5a)."""

        session = await self.get_session(session_id)
        return tuple(session.durable_interactions.values())

    async def find_resumable_interaction(
        self, session_id: str, node_key: str
    ) -> DurableInteraction | None:
        """Find an in-progress durable interaction for ``(session, node)`` (S5b).

        The durable interaction belongs to the NODE, not to a single attempt:
        ``interaction_id`` is keyed by ``(session_id, node_key)``
        (see ``durable_interaction_id``), so a fresh node attempt (new
        ``execution_id``, after the prior attempt's node lease expired and the
        node requeued) can locate the prior interaction and CONTINUE it. Returns
        the interaction only when it is RUNNING with at least one completed turn
        (i.e. there is recorded progress worth resuming); otherwise ``None`` so
        the caller starts fresh.
        """

        session = await self.get_session(session_id)
        interaction_id = durable_interaction_id(session_id, node_key)
        interaction = session.durable_interactions.get(interaction_id)
        if interaction is None:
            return None
        if interaction.status is not InteractionStatus.RUNNING:
            return None
        has_completed = any(
            turn.status is TurnStatus.COMPLETED for turn in interaction.turns
        )
        if not has_completed:
            return None
        return interaction

    async def mark_interaction_resumed(
        self,
        session_id: str,
        *,
        interaction_id: str,
        recovered_from_turn_index: int,
    ) -> DurableInteraction:
        """Stamp the recovery marker when resuming from durable state (S5b)."""

        session = await self.get_session(session_id)
        interaction = durable_interaction_bookkeeping.mark_durable_interaction_resumed(
            session,
            interaction_id=interaction_id,
            recovered_from_turn_index=recovered_from_turn_index,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    # ---- durable turn lease (control-owned, S5b) ---------------------------

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
        """Grant a worker ownership of one durable interaction turn (S5b).

        Mirrors ``grant_execution_lease`` at turn granularity: a completed turn
        or one with a live lease cannot be re-granted.
        """

        session = await self.get_session(session_id)
        interaction = turn_lease_bookkeeping.grant_durable_turn_lease(
            session,
            interaction_id=interaction_id,
            turn_index=turn_index,
            agent_id=agent_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            lease_token=lease_token,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

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

        session = await self.get_session(session_id)
        interaction = turn_lease_bookkeeping.heartbeat_durable_turn_lease(
            session,
            interaction_id=interaction_id,
            turn_index=turn_index,
            lease_token=lease_token,
            ttl_seconds=ttl_seconds,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def release_turn_lease(
        self,
        session_id: str,
        *,
        interaction_id: str,
        turn_index: int,
        lease_token: str,
        completed: bool = True,
    ) -> DurableInteraction:
        """Release a turn lease on success/handoff (S5b).

        ``completed=True`` also marks the turn COMPLETED (the success path);
        ``completed=False`` only clears the lease.
        """

        session = await self.get_session(session_id)
        interaction = turn_lease_bookkeeping.release_durable_turn_lease(
            session,
            interaction_id=interaction_id,
            turn_index=turn_index,
            lease_token=lease_token,
            completed=completed,
            now=_now(),
        )
        await self._sessions.put(session)
        return interaction

    async def recover_expired_turn_leases(
        self,
        session_id: str,
        *,
        as_of: datetime | None = None,
    ) -> tuple[RecoveredDurableTurn, ...]:
        """Recover expired turn leases and mark interactions resumable (S5b).

        Mirrors ``recover_expired_node_executions``: an expired turn lease (a
        crashed worker) is reverted to PENDING and its interaction's
        ``recovered_from_turn_index`` is set to the lowest not-yet-completed turn
        index. A still-live lease is left untouched.
        """

        session = await self.get_session(session_id)
        now = as_of or _now()
        recovered = turn_lease_bookkeeping.recover_expired_durable_turn_leases(
            session,
            now=now,
        )
        if not recovered:
            return ()
        session.updated_at = now
        await self._sessions.put(session)
        return recovered

    # ---- execution lease ---------------------------------------------------

    async def grant_execution_lease(
        self,
        session_id: str,
        node_key: str,
        execution_id: str,
        *,
        owner: str,
        ttl_seconds: int = 300,
        lease_token: str | None = None,
    ) -> NodeAttempt:
        session = await self.get_session(session_id)
        now = _now()
        attempt = execution_bookkeeping.grant_execution_lease(
            session,
            node_key,
            execution_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            lease_token=lease_token,
            now=now,
        )
        await self._sessions.put(session)
        expires_at = attempt.lease_expires_at
        if expires_at is None:
            raise ExecutionLeaseError("execution lease expiry was not set")
        await self._events.execution_lease_granted(
            session_id,
            attempt=attempt,
            lease_expires_at=expires_at.isoformat(),
        )
        return attempt

    async def assert_execution_lease(
        self,
        session_id: str,
        execution_id: str,
        lease_token: str,
        *,
        allow_expired: bool = False,
    ) -> NodeAttempt:
        session = await self.get_session(session_id)
        attempt, _node = execution_bookkeeping.find_attempt_by_id(
            session, execution_id
        )
        execution_bookkeeping.assert_execution_lease(
            attempt,
            lease_token,
            as_of=_now(),
            allow_expired=allow_expired,
        )
        return attempt

    async def heartbeat_execution_lease(
        self,
        session_id: str,
        execution_id: str,
        *,
        lease_token: str,
        ttl_seconds: int = 300,
    ) -> NodeAttempt:
        session = await self.get_session(session_id)
        now = _now()
        attempt = execution_bookkeeping.heartbeat_execution_lease(
            session,
            execution_id,
            lease_token=lease_token,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        await self._sessions.put(session)
        expires_at = attempt.lease_expires_at
        if expires_at is None:
            raise ExecutionLeaseError("execution lease expiry was not set")
        await self._events.execution_lease_heartbeat(
            session_id,
            attempt=attempt,
            lease_expires_at=expires_at.isoformat(),
        )
        return attempt

    async def release_execution_lease(
        self,
        session_id: str,
        execution_id: str,
        *,
        lease_token: str,
    ) -> NodeAttempt:
        session = await self.get_session(session_id)
        attempt, owner = execution_bookkeeping.release_execution_lease(
            session,
            execution_id,
            lease_token=lease_token,
            now=_now(),
        )
        await self._sessions.put(session)
        await self._events.execution_lease_released(
            session_id,
            attempt=attempt,
            owner=owner,
        )
        return attempt

    # ---- gates -------------------------------------------------------------

    async def record_gate(
        self,
        session_id: str,
        node_key: str,
        *,
        name: str,
        outcome: GateOutcome,
        detail: dict[str, Any] | None = None,
    ) -> Gate:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        gate = gate_bookkeeping.record_gate(
            node,
            node_key=node_key,
            name=name,
            outcome=outcome,
            detail=detail,
            now=_now(),
        )
        await self._sessions.put(session)
        await self._events.gate_recorded(
            session_id,
            node=node,
            gate_id=gate.id,
            gate_name=name,
            outcome=outcome.value,
        )
        return gate

    async def apply_gate_decision(
        self, session_id: str, decision_input: GateDecisionInput
    ) -> Gate:
        session = await self.get_session(session_id)
        result = gate_bookkeeping.apply_gate_decision(
            session,
            decision_input,
            now=_now(),
            transition_node=self._node_transition,
        )
        await self._sessions.put(session)
        await self._events.gate_decision_applied(
            session_id,
            node=result.node,
            gate_id=result.gate.id,
            action=decision_input.action.value,
            actor=decision_input.actor,
            target_node_status=result.target_status.value,
        )
        return result.gate

    # ---- repair budget (durable, control-owned) ---------------------------

    async def reserve_auto_repair_attempt(
        self, session_id: str, *, max_attempts: int
    ) -> bool:
        """Atomically reserve one durable auto-repair attempt for a session.

        Reads the durable used-count for the session; if it is already at or
        above ``max_attempts`` (or ``max_attempts <= 0``), returns ``False``
        without mutating. Otherwise increments the durable used-count, persists
        the session (the store bumps ``version`` like other mutations), and
        returns ``True``.

        This is the runtime repair-budget authority: only SessionService mutates
        the counter, and it is persisted on ``RuntimeSession`` so the cap
        survives worker restart. It is intentionally separate from planner JSON
        diagnostics (this counts attempts only).
        """

        if max_attempts <= 0:
            return False
        session = await self.get_session(session_id)
        if session.auto_repair_attempts_used >= max_attempts:
            return False
        session.auto_repair_attempts_used += 1
        session.updated_at = _now()
        await self._sessions.put(session)
        return True

    async def auto_repair_attempts_used(self, session_id: str) -> int:
        """Return the durable count of auto-repair attempts consumed."""

        session = await self.get_session(session_id)
        return session.auto_repair_attempts_used

    # ---- idempotency ledger (durable, control-owned) ----------------------

    async def claim_idempotency_key(
        self,
        session_id: str,
        key: str,
        *,
        kind: str = "",
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically claim an idempotency ``key`` for a session (S4c).

        Returns ``True`` if the key was newly claimed — the caller OWNS the unit
        of side effect and should apply it. Returns ``False`` if the key was
        already processed — a duplicate delivery / re-delivery is replaying an
        effect that already happened, so the caller must SKIP it.

        The ledger is durable (persisted on ``RuntimeSession``) and control-owned
        (only ``SessionService`` writes it), so dedupe survives worker restart
        and respects the single-writer rule (AGENTS.md §3).
        """

        if not key:
            raise ValueError("idempotency key must not be empty")
        session = await self.get_session(session_id)
        if key in session.processed_idempotency_keys:
            return False
        session.processed_idempotency_keys[key] = IdempotencyRecord(
            key=key,
            kind=kind,
            detail=dict(detail or {}),
        )
        session.updated_at = _now()
        await self._sessions.put(session)
        return True

    async def is_idempotency_key_processed(
        self, session_id: str, key: str
    ) -> bool:
        """Return whether ``key`` has already been claimed for a session (S4c)."""

        session = await self.get_session(session_id)
        return key in session.processed_idempotency_keys

    async def lookup_tool_effect_result(
        self, session_id: str, effect_fingerprint: str
    ) -> tuple[str, str] | None:
        """Return a prior successful ``(tool_call_id, content)`` for an effect (S4c).

        Durable, session-scoped tool side-effect dedupe across deliveries: if a
        tool with this stable ``effect_fingerprint`` already SUCCEEDED for the
        session (recorded by ``record_tool_execution_completed``), return its
        recorded result so a re-delivered/retried attempt reuses it instead of
        re-executing the side effect. Returns ``None`` if not yet recorded.
        """

        if not effect_fingerprint:
            return None
        session = await self.get_session(session_id)
        record = session.processed_idempotency_keys.get(
            _tool_effect_key(session_id, effect_fingerprint)
        )
        if record is None:
            return None
        source_id = record.detail.get("tool_call_id")
        content = record.detail.get("content")
        if isinstance(source_id, str) and isinstance(content, str):
            return source_id, content
        return None

    async def record_tool_effect_result(
        self,
        session_id: str,
        effect_fingerprint: str,
        *,
        tool_call_id: str,
        content: str,
    ) -> None:
        """Durably record a successful tool effect result by fingerprint (S4c).

        Records the FIRST successful result for an ``effect_fingerprint`` into
        the control-owned ledger so a later duplicate delivery can reuse it
        (see ``lookup_tool_effect_result``). A no-op if already recorded.
        """

        if not effect_fingerprint:
            return
        key = _tool_effect_key(session_id, effect_fingerprint)
        session = await self.get_session(session_id)
        if key in session.processed_idempotency_keys:
            return
        session.processed_idempotency_keys[key] = IdempotencyRecord(
            key=key,
            kind="tool_effect",
            detail={"tool_call_id": tool_call_id, "content": content},
        )
        session.updated_at = _now()
        await self._sessions.put(session)

    # ---- query -------------------------------------------------------------

    async def get_node(self, session_id: str, node_key: str) -> NodeState:
        session = await self.get_session(session_id)
        return self._require_node(session, node_key)

    async def get_latest_gate(self, session_id: str, node_key: str) -> Gate | None:
        node = await self.get_node(session_id, node_key)
        return node.latest_gate()

    # ---- agent-report convenience -----------------------------------------

    async def consume_agent_report(self, session_id: str, report: AgentReport) -> None:
        """Append the agent's output onto the latest attempt (no state change).

        ControlPlane.open_runtime_decision is the proper consumer of reports;
        this method is exposed for callers that want to record telemetry
        without driving the gate/apply loop.
        """
        session = await self.get_session(session_id)
        node = self._require_node(session, report.node_key)
        attempt = self._find_running_attempt(node, report.execution_id)
        if attempt is None:
            attempt = NodeAttempt(
                node_key=report.node_key,
                attempt_no=len(node.attempts) + 1,
                trace_id=report.trace_id,
            )
            node.attempts.append(attempt)
        attempt.output = {**attempt.output, **report.persisted_output()}
        if report.error:
            attempt.error = report.error
        node.updated_at = _now()
        await self._sessions.put(session)

    # ---- internal helpers --------------------------------------------------

    async def _session_transition(
        self, session_id: str, to_: SessionStatus
    ) -> RuntimeSession:
        session = await self.get_session(session_id)
        session_lifecycle.transition_session(
            session,
            to_,
            now=_now(),
            invalid_transition=self._raise_invalid_transition,
        )
        await self._sessions.put(session)
        await self._events.session_transition(
            session_id,
            session=session,
            status=to_,
        )
        return session

    async def _node_only_transition(
        self, session_id: str, node_key: str, to_: NodeStatus
    ) -> NodeState:
        session = await self.get_session(session_id)
        node = self._require_node(session, node_key)
        self._node_transition(node, to_)
        await self._sessions.put(session)
        await self._events.node_transition(
            session_id,
            node=node,
            status=to_,
        )
        return node

    def _node_transition(self, node: NodeState, to_: NodeStatus) -> None:
        if not is_valid_node_transition(node.status, to_):
            raise InvalidTransitionError(
                f"node {node.key!r}: cannot move {node.status} → {to_}"
            )
        node.status = to_
        node.updated_at = _now()

    def _attempt_transition(self, attempt: NodeAttempt, to_: AttemptStatus) -> None:
        if not is_valid_attempt_transition(attempt.status, to_):
            raise InvalidTransitionError(
                f"attempt {attempt.id}: cannot move {attempt.status} → {to_}"
            )
        attempt.status = to_

    @staticmethod
    def _raise_invalid_transition(message: str) -> None:
        raise InvalidTransitionError(message)

    @staticmethod
    def _require_node(session: RuntimeSession, node_key: str) -> NodeState:
        node = session.nodes.get(node_key)
        if node is None:
            raise NodeNotFoundError(f"{session.id}/{node_key}")
        return node

    @staticmethod
    def _find_running_attempt(
        node: NodeState, execution_id: str | None
    ) -> NodeAttempt | None:
        if execution_id is not None:
            for attempt in node.attempts:
                if attempt.id == execution_id:
                    return attempt if attempt.status is AttemptStatus.RUNNING else None
            return None
        latest = node.latest_attempt()
        if latest is not None and latest.status is AttemptStatus.RUNNING:
            return latest
        return None

    @staticmethod
    def is_execution_lease_live(
        attempt: NodeAttempt,
        *,
        as_of: datetime | None = None,
    ) -> bool:
        return execution_bookkeeping.is_execution_lease_live(
            attempt, as_of=as_of or _now()
        )
