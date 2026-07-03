"""Node execution lifecycle helpers for ``SessionService``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import hydramind.control.session_execution as execution_bookkeeping
from hydramind.control.models import NodeAttempt, NodeState, RuntimeSession
from hydramind.control.states import AttemptStatus, NodeStatus

AttemptTransition = Callable[[NodeAttempt, AttemptStatus], None]
NodeTransition = Callable[[NodeState, NodeStatus], None]


@dataclass(frozen=True)
class RecoveredNodeExecution:
    node_key: str
    attempt: NodeAttempt
    lease_owner: str | None
    lease_expires_at: datetime | None


def complete_running_attempt(
    node: NodeState,
    *,
    output: dict[str, Any] | None,
    now: datetime,
    transition_attempt: AttemptTransition,
) -> NodeAttempt | None:
    attempt = node.latest_attempt()
    if attempt is None or attempt.status is not AttemptStatus.RUNNING:
        return None
    transition_attempt(attempt, AttemptStatus.SUCCEEDED)
    if output is not None:
        attempt.output = dict(output)
    attempt.finished_at = now
    execution_bookkeeping.clear_execution_lease(attempt)
    return attempt.model_copy(deep=True)


def fail_running_attempt(
    node: NodeState,
    *,
    error: str,
    now: datetime,
    transition_attempt: AttemptTransition,
) -> NodeAttempt | None:
    attempt = node.latest_attempt()
    if attempt is None or attempt.status is not AttemptStatus.RUNNING:
        return None
    transition_attempt(attempt, AttemptStatus.FAILED)
    attempt.error = error
    attempt.finished_at = now
    execution_bookkeeping.clear_execution_lease(attempt)
    return attempt.model_copy(deep=True)


def abort_running_attempt(
    node: NodeState,
    *,
    reason: str,
    now: datetime,
    transition_attempt: AttemptTransition,
) -> NodeAttempt | None:
    attempt = node.latest_attempt()
    if attempt is None or attempt.status is not AttemptStatus.RUNNING:
        return None
    transition_attempt(attempt, AttemptStatus.ABORTED)
    attempt.error = reason
    attempt.finished_at = now
    execution_bookkeeping.clear_execution_lease(attempt)
    return attempt.model_copy(deep=True)


def abort_and_requeue_node(
    node: NodeState,
    *,
    reason: str,
    now: datetime,
    transition_attempt: AttemptTransition,
    transition_node: NodeTransition,
) -> NodeAttempt | None:
    aborted = abort_running_attempt(
        node,
        reason=reason,
        now=now,
        transition_attempt=transition_attempt,
    )

    if node.status is NodeStatus.PENDING_GATE:
        transition_node(node, NodeStatus.NEEDS_REVISION)
    if node.status is not NodeStatus.QUEUED:
        transition_node(node, NodeStatus.QUEUED)
    node.updated_at = now
    return aborted


def recover_expired_node_execution_leases(
    session: RuntimeSession,
    *,
    now: datetime,
    transition_attempt: AttemptTransition,
    transition_node: NodeTransition,
) -> tuple[RecoveredNodeExecution, ...]:
    recovered: list[RecoveredNodeExecution] = []
    for node in session.nodes.values():
        if node.status is not NodeStatus.RUNNING:
            continue
        attempt = node.latest_attempt()
        if attempt is None or attempt.status is not AttemptStatus.RUNNING:
            continue
        if not execution_bookkeeping.has_execution_lease_metadata(attempt):
            continue
        if execution_bookkeeping.is_execution_lease_live(attempt, as_of=now):
            continue
        owner = attempt.lease_owner
        expires_at = attempt.lease_expires_at
        transition_attempt(attempt, AttemptStatus.ABORTED)
        attempt.error = "execution lease expired"
        attempt.finished_at = now
        execution_bookkeeping.clear_execution_lease(attempt)
        transition_node(node, NodeStatus.QUEUED)
        node.updated_at = now
        recovered.append(
            RecoveredNodeExecution(
                node_key=node.key,
                attempt=attempt.model_copy(deep=True),
                lease_owner=owner,
                lease_expires_at=expires_at,
            )
        )
    return tuple(recovered)


__all__ = [
    "RecoveredNodeExecution",
    "abort_and_requeue_node",
    "abort_running_attempt",
    "complete_running_attempt",
    "fail_running_attempt",
    "recover_expired_node_execution_leases",
]
