"""WorkflowGraph — topological scheduling over a WorkflowBlueprint."""

from __future__ import annotations

from hydramind.control.models import RuntimeSession, WorkflowBlueprint
from hydramind.control.states import NodeStatus


class GraphCycleError(ValueError):
    """Raised when a WorkflowBlueprint contains a dependency cycle."""


class WorkflowGraph:
    """Derives execution order and ready-node lookup from a blueprint."""

    def __init__(self, blueprint: WorkflowBlueprint) -> None:
        self._blueprint = blueprint
        self._order = self._topological(blueprint)

    @property
    def blueprint(self) -> WorkflowBlueprint:
        return self._blueprint

    def topological_order(self) -> tuple[str, ...]:
        return self._order

    def ready_nodes(self, session: RuntimeSession) -> list[str]:
        """Return node keys whose `requires` are all COMPLETED and which are
        themselves still QUEUED. Returned in topological order."""
        ready: list[str] = []
        for key in self._order:
            node = session.nodes.get(key)
            if node is None or node.status is not NodeStatus.QUEUED:
                continue
            spec = self._blueprint.node_spec(key)
            satisfied = all(
                (req_node := session.nodes.get(r)) is not None
                and req_node.status is NodeStatus.COMPLETED
                for r in spec.requires
            )
            if satisfied:
                ready.append(key)
        return ready

    @staticmethod
    def _topological(blueprint: WorkflowBlueprint) -> tuple[str, ...]:
        in_degree: dict[str, int] = {n.key: 0 for n in blueprint.nodes}
        downstream: dict[str, list[str]] = {n.key: [] for n in blueprint.nodes}
        for n in blueprint.nodes:
            for req in n.requires:
                if req not in in_degree:
                    raise ValueError(
                        f"node {n.key!r} requires unknown node {req!r}"
                    )
                in_degree[n.key] += 1
                downstream[req].append(n.key)

        order: list[str] = []
        # Stable order: iterate nodes in their declared order for ties.
        declared_index = {n.key: i for i, n in enumerate(blueprint.nodes)}
        ready = sorted(
            (k for k, d in in_degree.items() if d == 0),
            key=lambda k: declared_index[k],
        )
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in downstream[current]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    # Maintain stable ordering on insertion.
                    idx = next(
                        (
                            i
                            for i, k in enumerate(ready)
                            if declared_index[k] > declared_index[child]
                        ),
                        len(ready),
                    )
                    ready.insert(idx, child)
        if len(order) != len(blueprint.nodes):
            unresolved = [k for k, d in in_degree.items() if d > 0]
            raise GraphCycleError(
                f"workflow {blueprint.name!r} has dependency cycle among: {unresolved}"
            )
        return tuple(order)
