"""Execution bookkeeping helpers for ``SessionService``."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from hydramind.control.models import (
    NodeAttempt,
    NodeState,
    RuntimeSession,
    ToolExecution,
)
from hydramind.control.states import (
    AttemptStatus,
    ToolExecutionStatus,
)
from hydramind.observability.redaction import redact_value


class ExecutionLeaseError(RuntimeError):
    """Raised when node execution lease validation fails."""


def record_tool_execution_started(
    session: RuntimeSession,
    execution_id: str,
    *,
    tool_call_id: str,
    tool_name: str,
    round_no: int,
    arguments: dict[str, Any] | None,
    trace_id: str | None,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> ToolExecution:
    """Record that a tool call started inside a node execution envelope."""

    attempt, node = find_attempt_by_id(session, execution_id)
    tool = find_tool_execution(attempt, tool_call_id)
    redacted_arguments = redact_value(arguments or {})
    if not isinstance(redacted_arguments, dict):
        redacted_arguments = {"value": redacted_arguments}
    if tool is None:
        tool = ToolExecution(
            node_key=attempt.node_key,
            execution_id=attempt.id,
            trace_id=trace_id or attempt.trace_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            round_no=round_no,
            arguments=redacted_arguments,
            started_at=now,
            metadata=dict(metadata or {}),
        )
        attempt.tool_executions.append(tool)
    else:
        tool.tool_name = tool_name
        tool.round_no = round_no
        tool.arguments = redacted_arguments
        tool.trace_id = trace_id or attempt.trace_id
        if metadata:
            tool.metadata = {**tool.metadata, **metadata}
        if tool.status is ToolExecutionStatus.STARTED:
            tool.started_at = tool.started_at or now
    node.updated_at = now
    session.updated_at = now
    return tool.model_copy(deep=True)


def record_tool_execution_completed(
    session: RuntimeSession,
    execution_id: str,
    *,
    tool_call_id: str,
    is_error: bool,
    result_preview: dict[str, Any] | None,
    content_length: int | None,
    error: str | None,
    now: datetime,
) -> ToolExecution:
    """Record a tool result under the control-owned node execution."""

    attempt, node = find_attempt_by_id(session, execution_id)
    tool = find_tool_execution(attempt, tool_call_id)
    if tool is None:
        raise ExecutionLeaseError(
            f"tool call {tool_call_id!r} not found in execution {execution_id!r}"
        )
    tool.status = (
        ToolExecutionStatus.FAILED
        if is_error
        else ToolExecutionStatus.SUCCEEDED
    )
    tool.is_error = is_error
    tool.result_preview = dict(result_preview or {})
    tool.content_length = content_length
    tool.error = error
    tool.finished_at = now
    node.updated_at = now
    session.updated_at = now
    return tool.model_copy(deep=True)


def grant_execution_lease(
    session: RuntimeSession,
    node_key: str,
    execution_id: str,
    *,
    owner: str,
    ttl_seconds: int,
    lease_token: str | None,
    now: datetime,
) -> NodeAttempt:
    attempt, node = find_attempt_by_id(session, execution_id)
    if attempt.node_key != node_key:
        raise ExecutionLeaseError(
            f"execution {execution_id!r} belongs to node {attempt.node_key!r}"
        )
    if attempt.status is not AttemptStatus.RUNNING:
        raise ExecutionLeaseError(f"execution {execution_id!r} is not running")
    owner_value = owner.strip()
    if not owner_value:
        raise ExecutionLeaseError("execution lease owner must not be empty")
    if is_execution_lease_live(attempt, as_of=now):
        raise ExecutionLeaseError(
            f"execution {execution_id!r} already has a live lease"
        )
    token = lease_token or f"lease-{uuid.uuid4().hex[:24]}"
    if not token.strip():
        raise ExecutionLeaseError("execution lease token must not be empty")
    attempt.lease_token = token
    attempt.lease_owner = owner_value
    attempt.last_heartbeat_at = now
    attempt.lease_expires_at = now + lease_ttl(ttl_seconds)
    node.updated_at = now
    session.updated_at = now
    return attempt


def assert_execution_lease(
    attempt: NodeAttempt,
    lease_token: str,
    *,
    as_of: datetime,
    allow_expired: bool = False,
) -> None:
    token = lease_token.strip()
    if not token:
        raise ExecutionLeaseError("execution lease token must not be empty")
    if not attempt.lease_token or not attempt.lease_owner:
        raise ExecutionLeaseError(
            f"execution {attempt.id!r} does not have an active lease"
        )
    if attempt.lease_token != token:
        raise ExecutionLeaseError(f"execution {attempt.id!r} lease token mismatch")
    if attempt.status is not AttemptStatus.RUNNING:
        raise ExecutionLeaseError(f"execution {attempt.id!r} is not running")
    if not allow_expired and not is_execution_lease_live(attempt, as_of=as_of):
        raise ExecutionLeaseError(f"execution {attempt.id!r} lease expired")


def heartbeat_execution_lease(
    session: RuntimeSession,
    execution_id: str,
    *,
    lease_token: str,
    ttl_seconds: int,
    now: datetime,
) -> NodeAttempt:
    attempt, node = find_attempt_by_id(session, execution_id)
    assert_execution_lease(attempt, lease_token, as_of=now)
    attempt.last_heartbeat_at = now
    attempt.lease_expires_at = now + lease_ttl(ttl_seconds)
    node.updated_at = now
    session.updated_at = now
    return attempt


def release_execution_lease(
    session: RuntimeSession,
    execution_id: str,
    *,
    lease_token: str,
    now: datetime,
) -> tuple[NodeAttempt, str | None]:
    attempt, node = find_attempt_by_id(session, execution_id)
    assert_execution_lease(
        attempt,
        lease_token,
        as_of=now,
        allow_expired=True,
    )
    owner = attempt.lease_owner
    clear_execution_lease(attempt)
    node.updated_at = now
    session.updated_at = now
    return attempt, owner


def find_attempt_by_id(
    session: RuntimeSession,
    execution_id: str,
) -> tuple[NodeAttempt, NodeState]:
    for node in session.nodes.values():
        for attempt in node.attempts:
            if attempt.id == execution_id:
                return attempt, node
    raise ExecutionLeaseError(f"execution {execution_id!r} not found")


def find_tool_execution(
    attempt: NodeAttempt,
    tool_call_id: str,
) -> ToolExecution | None:
    for tool in attempt.tool_executions:
        if tool.tool_call_id == tool_call_id:
            return tool
    return None


def is_execution_lease_live(
    attempt: NodeAttempt,
    *,
    as_of: datetime,
) -> bool:
    if not attempt.lease_token or not attempt.lease_owner:
        return False
    if attempt.lease_expires_at is None:
        return False
    return attempt.lease_expires_at > as_of


def has_execution_lease_metadata(attempt: NodeAttempt) -> bool:
    return bool(
        attempt.lease_token
        or attempt.lease_owner
        or attempt.lease_expires_at is not None
    )


def clear_execution_lease(attempt: NodeAttempt) -> None:
    attempt.lease_token = None
    attempt.lease_owner = None
    attempt.last_heartbeat_at = None
    attempt.lease_expires_at = None


def clear_session_execution_leases(session: RuntimeSession) -> None:
    for node in session.nodes.values():
        for attempt in node.attempts:
            clear_execution_lease(attempt)


def lease_ttl(ttl_seconds: int) -> timedelta:
    if ttl_seconds <= 0:
        raise ExecutionLeaseError("execution lease ttl_seconds must be positive")
    return timedelta(seconds=ttl_seconds)
