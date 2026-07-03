"""WorkflowGraph: topological order + ready-node lookup + cycle detection."""

from __future__ import annotations

import pytest

from hydramind.control.models import (
    NodeState,
    RuntimeSession,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.control.states import NodeStatus, SessionStatus
from hydramind.orchestration import GraphCycleError, WorkflowGraph


def _linear() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="linear",
        nodes=(
            WorkflowNodeSpec(key="a", role="x"),
            WorkflowNodeSpec(key="b", role="x", requires=("a",)),
            WorkflowNodeSpec(key="c", role="x", requires=("b",)),
        ),
    )


def _diamond() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="diamond",
        nodes=(
            WorkflowNodeSpec(key="root", role="x"),
            WorkflowNodeSpec(key="l", role="x", requires=("root",)),
            WorkflowNodeSpec(key="r", role="x", requires=("root",)),
            WorkflowNodeSpec(key="merge", role="x", requires=("l", "r")),
        ),
    )


def test_linear_topological_order() -> None:
    g = WorkflowGraph(_linear())
    assert g.topological_order() == ("a", "b", "c")


def test_diamond_topological_order_stable() -> None:
    g = WorkflowGraph(_diamond())
    order = g.topological_order()
    assert order[0] == "root"
    assert order[-1] == "merge"
    assert set(order[1:3]) == {"l", "r"}


def test_cycle_detection() -> None:
    bp = WorkflowBlueprint(
        name="cyc",
        nodes=(
            WorkflowNodeSpec(key="a", role="x", requires=("b",)),
            WorkflowNodeSpec(key="b", role="x", requires=("a",)),
        ),
    )
    with pytest.raises(GraphCycleError):
        WorkflowGraph(bp)


def test_unknown_dependency_rejected() -> None:
    bp = WorkflowBlueprint(
        name="bad",
        nodes=(WorkflowNodeSpec(key="a", role="x", requires=("ghost",)),),
    )
    with pytest.raises(ValueError, match="ghost"):
        WorkflowGraph(bp)


def test_ready_nodes_respects_requires() -> None:
    g = WorkflowGraph(_diamond())
    session = RuntimeSession(
        workflow_name="diamond",
        status=SessionStatus.RUNNING,
        nodes={
            "root": NodeState(key="root", status=NodeStatus.COMPLETED),
            "l": NodeState(key="l", status=NodeStatus.QUEUED),
            "r": NodeState(key="r", status=NodeStatus.QUEUED),
            "merge": NodeState(key="merge", status=NodeStatus.QUEUED),
        },
    )
    assert g.ready_nodes(session) == ["l", "r"]


def test_ready_nodes_returns_merge_when_both_branches_done() -> None:
    g = WorkflowGraph(_diamond())
    session = RuntimeSession(
        workflow_name="diamond",
        status=SessionStatus.RUNNING,
        nodes={
            "root": NodeState(key="root", status=NodeStatus.COMPLETED),
            "l": NodeState(key="l", status=NodeStatus.COMPLETED),
            "r": NodeState(key="r", status=NodeStatus.COMPLETED),
            "merge": NodeState(key="merge", status=NodeStatus.QUEUED),
        },
    )
    assert g.ready_nodes(session) == ["merge"]


def test_ready_nodes_empty_when_all_done() -> None:
    g = WorkflowGraph(_linear())
    session = RuntimeSession(
        workflow_name="linear",
        status=SessionStatus.RUNNING,
        nodes={
            "a": NodeState(key="a", status=NodeStatus.COMPLETED),
            "b": NodeState(key="b", status=NodeStatus.COMPLETED),
            "c": NodeState(key="c", status=NodeStatus.COMPLETED),
        },
    )
    assert g.ready_nodes(session) == []
