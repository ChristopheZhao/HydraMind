"""State enums and transition tables for the control plane.

The transition matrix is the single source of truth for legal moves;
``SessionService`` consults it before any write.
"""

from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_GATE = "waiting_gate"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PENDING_GATE = "pending_gate"
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


class ToolExecutionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GateOutcome(StrEnum):
    PASS = "pass"
    REQUIRES_DECISION = "requires_decision"
    BLOCK = "block"


class DecisionAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REPLAN = "replan"
    REJECT = "reject"


class ApplyIntentKind(StrEnum):
    COMPLETE = "complete"
    PAUSE = "pause"
    FAIL = "fail"
    REQUEUE = "requeue"
    WORKFLOW_REVISION = "workflow_revision"


_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.QUEUED: frozenset({SessionStatus.RUNNING, SessionStatus.CANCELLED}),
    SessionStatus.RUNNING: frozenset(
        {
            SessionStatus.WAITING_GATE,
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        }
    ),
    SessionStatus.WAITING_GATE: frozenset(
        {SessionStatus.RESUMING, SessionStatus.CANCELLED, SessionStatus.FAILED}
    ),
    SessionStatus.RESUMING: frozenset(
        {SessionStatus.RUNNING, SessionStatus.FAILED, SessionStatus.CANCELLED}
    ),
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.FAILED: frozenset(),
    SessionStatus.CANCELLED: frozenset(),
}


_NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.QUEUED: frozenset({NodeStatus.RUNNING, NodeStatus.STALE}),
    NodeStatus.RUNNING: frozenset(
        {
            NodeStatus.QUEUED,
            NodeStatus.PENDING_GATE,
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
        }
    ),
    NodeStatus.PENDING_GATE: frozenset(
        {NodeStatus.APPROVED, NodeStatus.NEEDS_REVISION, NodeStatus.FAILED}
    ),
    NodeStatus.APPROVED: frozenset({NodeStatus.COMPLETED, NodeStatus.STALE}),
    NodeStatus.NEEDS_REVISION: frozenset({NodeStatus.QUEUED, NodeStatus.FAILED}),
    NodeStatus.COMPLETED: frozenset({NodeStatus.STALE}),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.STALE: frozenset({NodeStatus.QUEUED}),
}


_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.RUNNING: frozenset(
        {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.ABORTED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.ABORTED: frozenset(),
}


def is_valid_session_transition(from_: SessionStatus, to_: SessionStatus) -> bool:
    return to_ in _SESSION_TRANSITIONS[from_]


def is_valid_node_transition(from_: NodeStatus, to_: NodeStatus) -> bool:
    return to_ in _NODE_TRANSITIONS[from_]


def is_valid_attempt_transition(from_: AttemptStatus, to_: AttemptStatus) -> bool:
    return to_ in _ATTEMPT_TRANSITIONS[from_]
