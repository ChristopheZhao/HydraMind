"""Tests for the state-machine transition matrices."""

from __future__ import annotations

import pytest

from hydramind.control.states import (
    AttemptStatus,
    NodeStatus,
    SessionStatus,
    is_valid_attempt_transition,
    is_valid_node_transition,
    is_valid_session_transition,
)


@pytest.mark.parametrize(
    ("from_", "to_", "ok"),
    [
        (SessionStatus.QUEUED, SessionStatus.RUNNING, True),
        (SessionStatus.QUEUED, SessionStatus.COMPLETED, False),
        (SessionStatus.RUNNING, SessionStatus.WAITING_GATE, True),
        (SessionStatus.WAITING_GATE, SessionStatus.RESUMING, True),
        (SessionStatus.RESUMING, SessionStatus.RUNNING, True),
        (SessionStatus.COMPLETED, SessionStatus.RUNNING, False),
        (SessionStatus.FAILED, SessionStatus.RUNNING, False),
        (SessionStatus.CANCELLED, SessionStatus.RUNNING, False),
    ],
)
def test_session_transitions(
    from_: SessionStatus, to_: SessionStatus, ok: bool
) -> None:
    assert is_valid_session_transition(from_, to_) is ok


@pytest.mark.parametrize(
    ("from_", "to_", "ok"),
    [
        (NodeStatus.QUEUED, NodeStatus.RUNNING, True),
        (NodeStatus.RUNNING, NodeStatus.PENDING_GATE, True),
        (NodeStatus.PENDING_GATE, NodeStatus.APPROVED, True),
        (NodeStatus.PENDING_GATE, NodeStatus.NEEDS_REVISION, True),
        (NodeStatus.NEEDS_REVISION, NodeStatus.QUEUED, True),
        (NodeStatus.APPROVED, NodeStatus.COMPLETED, True),
        (NodeStatus.COMPLETED, NodeStatus.STALE, True),
        (NodeStatus.STALE, NodeStatus.QUEUED, True),
        (NodeStatus.QUEUED, NodeStatus.COMPLETED, False),
        (NodeStatus.FAILED, NodeStatus.RUNNING, False),
    ],
)
def test_node_transitions(from_: NodeStatus, to_: NodeStatus, ok: bool) -> None:
    assert is_valid_node_transition(from_, to_) is ok


@pytest.mark.parametrize(
    ("from_", "to_", "ok"),
    [
        (AttemptStatus.RUNNING, AttemptStatus.SUCCEEDED, True),
        (AttemptStatus.RUNNING, AttemptStatus.FAILED, True),
        (AttemptStatus.RUNNING, AttemptStatus.ABORTED, True),
        (AttemptStatus.SUCCEEDED, AttemptStatus.RUNNING, False),
        (AttemptStatus.FAILED, AttemptStatus.RUNNING, False),
    ],
)
def test_attempt_transitions(
    from_: AttemptStatus, to_: AttemptStatus, ok: bool
) -> None:
    assert is_valid_attempt_transition(from_, to_) is ok
