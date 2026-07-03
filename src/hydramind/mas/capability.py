"""Executed-capability source of truth for native MAS collaboration.

This module is the single authoritative declaration of which
``CollaborationProtocol`` values and ``SharedWorkspace`` usages the runtime
*actually executes today*. The rich ``mas.contracts`` ontology declares far more
than the runtime currently honors (see ADR-0007); until each value is genuinely
wired by the agent-native kernel rewrite (PLAN-20260604-001 S91+), declaring an
unexecuted value must fail closed rather than silently degrade to flat fan-out.

Every later capability sprint widens these frozen sets in the same commit that
wires the behavior, so declared capability and executed behavior can never
silently diverge again.
"""

from __future__ import annotations

from hydramind.mas.contracts import (
    AggregationStrategy,
    ArbitrationStrategy,
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    TeamSpec,
)

# The executed capability envelope. Keep these as the ONLY place runtime
# capability is declared; widen them only alongside the code that executes them.
EXECUTED_MODES: frozenset[CollaborationMode] = frozenset(
    {
        CollaborationMode.TEAM,
        CollaborationMode.DELEGATION,
        CollaborationMode.DEBATE,
        CollaborationMode.VOTE,
    }
)
EXECUTED_TOPOLOGIES: frozenset[CollaborationTopology] = frozenset(
    {
        CollaborationTopology.BROADCAST,
        CollaborationTopology.PIPELINE,
        CollaborationTopology.COORDINATOR,
    }
)
EXECUTED_AGGREGATIONS: frozenset[AggregationStrategy] = frozenset(
    {
        AggregationStrategy.COLLECT,
        AggregationStrategy.COORDINATOR_SUMMARY,
        AggregationStrategy.VOTE,
    }
)
EXECUTED_ARBITRATIONS: frozenset[ArbitrationStrategy] = frozenset(
    {
        ArbitrationStrategy.COORDINATOR,
        ArbitrationStrategy.MAJORITY,
        ArbitrationStrategy.NONE,
    }
)
EXECUTED_MODE_TOPOLOGY_PAIRS: frozenset[
    tuple[CollaborationMode, CollaborationTopology]
] = frozenset(
    {
        (CollaborationMode.TEAM, CollaborationTopology.BROADCAST),
        (CollaborationMode.TEAM, CollaborationTopology.PIPELINE),
        (CollaborationMode.TEAM, CollaborationTopology.COORDINATOR),
        (CollaborationMode.DEBATE, CollaborationTopology.BROADCAST),
        (CollaborationMode.VOTE, CollaborationTopology.BROADCAST),
        (CollaborationMode.DELEGATION, CollaborationTopology.COORDINATOR),
    }
)
# Workspace artifact/memory refs were removed (S102 execute-or-remove): collaboration
# data flows through the message-passing kernel seam, not workspace references, so there
# is no longer any unexecuted workspace capability to gate.


class UnexecutedProtocolError(ValueError):
    """Raised when a TeamSpec declares collaboration capability the runtime cannot honor."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        joined = "; ".join(violations)
        super().__init__(
            "team declares collaboration capability not yet executed by the runtime "
            f"(ADR-0007 dead-surface freeze): {joined}"
        )


def executed_protocol_violations(
    protocol: CollaborationProtocol,
) -> tuple[str, ...]:
    """Return human-readable descriptions of every unexecuted declaration.

    Pure predicate: an empty result means the protocol is fully within the
    executed envelope and is safe to run today. The guard remains live for any
    FUTURE enum value added without a corresponding ``EXECUTED_*`` widening.
    """

    violations: list[str] = []
    if protocol.mode not in EXECUTED_MODES:
        violations.append(f"mode={protocol.mode.value!r} is not executed")
    if protocol.topology not in EXECUTED_TOPOLOGIES:
        violations.append(f"topology={protocol.topology.value!r} is not executed")
    if (
        protocol.mode in EXECUTED_MODES
        and protocol.topology in EXECUTED_TOPOLOGIES
        and (protocol.mode, protocol.topology) not in EXECUTED_MODE_TOPOLOGY_PAIRS
    ):
        violations.append(
            "mode/topology pair "
            f"{protocol.mode.value!r}/{protocol.topology.value!r} is not executed"
        )
    if protocol.aggregation not in EXECUTED_AGGREGATIONS:
        violations.append(
            f"aggregation={protocol.aggregation.value!r} is not executed"
        )
    if protocol.arbitration not in EXECUTED_ARBITRATIONS:
        violations.append(
            f"arbitration={protocol.arbitration.value!r} is not executed"
        )
    return tuple(violations)


def require_executed_team(team: TeamSpec) -> None:
    """Fail closed if ``team`` declares any capability the runtime cannot execute.

    Used at the projection boundary (PlanTaskSpec) and as an executor backstop so a
    hand-authored team cannot silently degrade to flat fan-out.
    """

    violations = executed_protocol_violations(team.protocol)
    if violations:
        raise UnexecutedProtocolError(violations)
