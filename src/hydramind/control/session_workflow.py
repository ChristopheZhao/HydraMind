"""Workflow revision helpers for ``SessionService``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from hydramind.control.models import (
    NodeState,
    RuntimeSession,
    WorkflowBlueprint,
    WorkflowRevision,
)
from hydramind.control.states import NodeStatus

NodeTransition = Callable[[NodeState, NodeStatus], None]
InvalidTransition = Callable[[str], None]


@dataclass(frozen=True)
class WorkflowRevisionSummary:
    added_node_keys: tuple[str, ...]
    removed_node_keys: tuple[str, ...]
    changed_node_keys: tuple[str, ...]
    requeued_node_keys: tuple[str, ...]


def apply_workflow_revision(
    session: RuntimeSession,
    revision: WorkflowRevision,
    *,
    now: datetime,
    transition_node: NodeTransition,
    invalid_transition: InvalidTransition,
) -> WorkflowRevisionSummary:
    """Apply a workflow graph revision while preserving node history."""

    validate_blueprint(revision.current_blueprint)
    validate_blueprint(revision.revised_blueprint)
    current_keys = blueprint_keys(revision.current_blueprint)
    revised_keys = blueprint_keys(revision.revised_blueprint)
    changed_keys = set(revision.changed_node_keys)
    unknown_changed = changed_keys - (current_keys & revised_keys)
    if unknown_changed:
        raise ValueError(
            "changed_node_keys must exist in both blueprints: "
            f"{sorted(unknown_changed)}"
        )
    removed_keys = current_keys - revised_keys
    added_keys = revised_keys - current_keys
    requeue_keys = (
        changed_keys
        | descendant_keys(revision.current_blueprint, changed_keys | removed_keys)
    ) - removed_keys

    for key in sorted(added_keys):
        if key in session.nodes:
            requeue_node_for_revision(
                session.nodes[key],
                transition_node=transition_node,
                invalid_transition=invalid_transition,
            )
        else:
            session.nodes[key] = NodeState(key=key)

    for key in sorted(removed_keys):
        node = session.nodes.get(key)
        if node is not None:
            mark_node_stale_for_revision(
                node,
                transition_node=transition_node,
                invalid_transition=invalid_transition,
            )

    for key in sorted(requeue_keys):
        node = session.nodes.get(key)
        if node is not None:
            requeue_node_for_revision(
                node,
                transition_node=transition_node,
                invalid_transition=invalid_transition,
            )

    revision_record = {
        "reason": revision.reason,
        "feedback_refs": list(revision.feedback_refs),
        "added_node_keys": sorted(added_keys),
        "removed_node_keys": sorted(removed_keys),
        "changed_node_keys": sorted(changed_keys),
        "requeued_node_keys": sorted(requeue_keys),
        "applied_at": now.isoformat(),
    }
    history = session.metadata.get("workflow_revisions")
    if not isinstance(history, list):
        history = []
    session.metadata = {
        **session.metadata,
        **revision.metadata,
        "workflow_revisions": [*history, revision_record],
    }
    session.updated_at = now
    return WorkflowRevisionSummary(
        added_node_keys=tuple(sorted(added_keys)),
        removed_node_keys=tuple(sorted(removed_keys)),
        changed_node_keys=tuple(sorted(changed_keys)),
        requeued_node_keys=tuple(sorted(requeue_keys)),
    )


def validate_blueprint(blueprint: WorkflowBlueprint) -> None:
    keys: set[str] = set()
    for node in blueprint.nodes:
        if node.key in keys:
            raise ValueError(f"duplicate workflow node key {node.key!r}")
        keys.add(node.key)
    for node in blueprint.nodes:
        missing = [key for key in node.requires if key not in keys]
        if missing:
            raise ValueError(
                f"node {node.key!r} requires unknown node(s): {missing}"
            )


def blueprint_keys(blueprint: WorkflowBlueprint) -> set[str]:
    return {node.key for node in blueprint.nodes}


def descendant_keys(
    blueprint: WorkflowBlueprint,
    roots: set[str],
) -> set[str]:
    if not roots:
        return set()
    children: dict[str, list[str]] = {node.key: [] for node in blueprint.nodes}
    for node in blueprint.nodes:
        for required in node.requires:
            children.setdefault(required, []).append(node.key)
    descendants: set[str] = set()
    frontier = list(roots)
    while frontier:
        current = frontier.pop(0)
        for child in children.get(current, []):
            if child in descendants:
                continue
            descendants.add(child)
            frontier.append(child)
    return descendants


def mark_node_stale_for_revision(
    node: NodeState,
    *,
    transition_node: NodeTransition,
    invalid_transition: InvalidTransition,
) -> None:
    if node.status is NodeStatus.STALE:
        return
    if node.status in {NodeStatus.RUNNING, NodeStatus.PENDING_GATE}:
        invalid_transition(
            f"node {node.key!r}: active node cannot be removed by workflow revision"
        )
    if node.status is NodeStatus.NEEDS_REVISION:
        transition_node(node, NodeStatus.QUEUED)
    if node.status is NodeStatus.FAILED:
        invalid_transition(
            f"node {node.key!r}: failed node cannot be removed by workflow revision"
        )
    transition_node(node, NodeStatus.STALE)


def requeue_node_for_revision(
    node: NodeState,
    *,
    transition_node: NodeTransition,
    invalid_transition: InvalidTransition,
) -> None:
    if node.status is NodeStatus.QUEUED:
        return
    if node.status is NodeStatus.RUNNING:
        invalid_transition(
            f"node {node.key!r}: active node cannot be requeued by workflow revision"
        )
    if node.status is NodeStatus.PENDING_GATE:
        return
    if node.status is NodeStatus.FAILED:
        invalid_transition(
            f"node {node.key!r}: failed node cannot be requeued by workflow revision"
        )
    if node.status is NodeStatus.NEEDS_REVISION:
        transition_node(node, NodeStatus.QUEUED)
        return
    if node.status is not NodeStatus.STALE:
        transition_node(node, NodeStatus.STALE)
    transition_node(node, NodeStatus.QUEUED)
