"""Native-agent kernel primitives (ADR-0007).

First-class, durable, scheduled collaboration entities and the pure scheduler seam.
Wired in S94: ``NativeTeamExecutor`` drives team turns through ``select_strategy`` so
a PIPELINE team executes as scheduled turns where member N reads member N-1.
"""

from hydramind.kernel.contracts import (
    Interaction,
    InteractionStatus,
    Message,
    MessageRole,
    Turn,
    TurnStatus,
)
from hydramind.kernel.scheduler import (
    BroadcastStrategy,
    CoordinatorStrategy,
    DebateStrategy,
    DelegationStrategy,
    PipelineStrategy,
    SchedulingStrategy,
    VoteStrategy,
    select_next_turn,
    select_strategy,
)

__all__ = [
    "BroadcastStrategy",
    "CoordinatorStrategy",
    "DebateStrategy",
    "DelegationStrategy",
    "Interaction",
    "InteractionStatus",
    "Message",
    "MessageRole",
    "PipelineStrategy",
    "SchedulingStrategy",
    "Turn",
    "TurnStatus",
    "VoteStrategy",
    "select_next_turn",
    "select_strategy",
]
