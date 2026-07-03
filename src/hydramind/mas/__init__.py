"""Native MAS primitives for team collaboration."""

from hydramind.mas.capability import (
    EXECUTED_MODE_TOPOLOGY_PAIRS,
    UnexecutedProtocolError,
    executed_protocol_violations,
    require_executed_team,
)
from hydramind.mas.contracts import (
    AgentSpec,
    AggregationStrategy,
    ArbitrationStrategy,
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    SharedWorkspace,
    TeamSpec,
    WorkspaceScope,
)
from hydramind.mas.protocol_outcomes import (
    PROTOCOL_OUTCOME_SCHEMA_VERSION,
    CoordinatorOutcome,
    MemberVoteRecord,
    VoteOutcome,
    build_vote_outcome,
    canonicalize_vote,
    member_vote_record,
)

__all__ = [
    "EXECUTED_MODE_TOPOLOGY_PAIRS",
    "PROTOCOL_OUTCOME_SCHEMA_VERSION",
    "AgentSpec",
    "AggregationStrategy",
    "ArbitrationStrategy",
    "CollaborationMode",
    "CollaborationProtocol",
    "CollaborationTopology",
    "CoordinatorOutcome",
    "MemberVoteRecord",
    "SharedWorkspace",
    "TeamSpec",
    "UnexecutedProtocolError",
    "VoteOutcome",
    "WorkspaceScope",
    "build_vote_outcome",
    "canonicalize_vote",
    "executed_protocol_violations",
    "member_vote_record",
    "require_executed_team",
]
