"""Session lifecycle mutation helpers for ``SessionService``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import hydramind.control.session_execution as execution_bookkeeping
from hydramind.control.models import (
    NodeState,
    RuntimeSession,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.control.states import (
    SessionStatus,
    is_valid_session_transition,
)

InvalidTransition = Callable[[str], None]

_TERMINAL_SESSION_STATUSES = {
    SessionStatus.COMPLETED,
    SessionStatus.FAILED,
    SessionStatus.CANCELLED,
}


def create_runtime_session(
    blueprint: WorkflowBlueprint,
    *,
    input_payload: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> RuntimeSession:
    nodes = {node.key: NodeState(key=node.key) for node in blueprint.nodes}
    return RuntimeSession(
        workflow_name=blueprint.name,
        workflow_version=blueprint.version,
        nodes=nodes,
        input_payload=input_payload or {},
        metadata=metadata or {},
    )


def update_session_metadata(
    session: RuntimeSession,
    metadata: dict[str, Any],
    *,
    merge: bool,
    now: datetime,
) -> None:
    session.metadata = (
        {**session.metadata, **metadata}
        if merge
        else dict(metadata)
    )
    session.updated_at = now


def add_workflow_nodes(
    session: RuntimeSession,
    nodes: tuple[WorkflowNodeSpec, ...],
    *,
    now: datetime,
) -> None:
    for spec in nodes:
        if spec.key in session.nodes:
            raise ValueError(
                f"session {session.id} already has node {spec.key!r}"
            )
    for spec in nodes:
        session.nodes[spec.key] = NodeState(key=spec.key)
    session.updated_at = now


def transition_session(
    session: RuntimeSession,
    to_: SessionStatus,
    *,
    now: datetime,
    invalid_transition: InvalidTransition,
) -> None:
    if not is_valid_session_transition(session.status, to_):
        invalid_transition(
            f"session {session.id}: cannot move {session.status} → {to_}"
        )
    session.status = to_
    session.updated_at = now
    if to_ in _TERMINAL_SESSION_STATUSES:
        execution_bookkeeping.clear_session_execution_leases(session)


__all__ = [
    "add_workflow_nodes",
    "create_runtime_session",
    "transition_session",
    "update_session_metadata",
]
