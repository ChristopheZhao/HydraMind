"""Pure message-driven scheduler seam for native-agent interactions (ADR-0007).

``select_next_turn`` decides which agent acts next from the interaction's
``CollaborationProtocol`` and the turns taken so far. This is the seam every later
topology (PIPELINE/COORDINATOR/DEBATE) extends; S91 implements only the executed
envelope (TEAM/BROADCAST) and fails loudly for anything else, reusing
``hydramind.mas.capability`` so the scheduler and the dead-surface freeze share a
single executed-capability source of truth. Pure: no harness, control, or runtime.
"""

from __future__ import annotations

from typing import Protocol

from hydramind.kernel.contracts import Interaction, Message, Turn, TurnStatus
from hydramind.mas import (
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    UnexecutedProtocolError,
    executed_protocol_violations,
)
from hydramind.mas.contracts import debate_rounds


class SchedulingStrategy(Protocol):
    """Decides the next acting turn AND what that turn may read.

    ``next_turn`` selects who acts next; ``visible_messages`` declares the prior
    interaction messages the acting turn may read. Topology behaviour is
    type-directed by the strategy object (ADR-0007) — callers never sniff a
    topology string to decide either question.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None: ...

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]: ...


def _next_unacted_turn(interaction: Interaction) -> Turn | None:
    """Return a PENDING turn for the first member that has not yet acted, in order."""

    acted = set(interaction.acted_agent_ids())
    for member_id in interaction.member_ids:
        if member_id not in acted:
            return Turn(
                index=len(interaction.turns),
                agent_id=member_id,
                status=TurnStatus.PENDING,
            )
    return None


class BroadcastStrategy:
    """TEAM/BROADCAST: every member acts once, in declared order, independently.

    Members are isolated: the acting turn reads no peer messages.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        return _next_unacted_turn(interaction)

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        return ()


class PipelineStrategy:
    """TEAM/PIPELINE: members act once in declared order; member N reads members 1..N-1.

    The acting turn reads the accumulated prior-turn messages, so each member
    builds on its predecessor's output — the defining behavioural difference from
    BROADCAST.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        return _next_unacted_turn(interaction)

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        return interaction.messages


class CoordinatorStrategy:
    """TEAM/COORDINATOR: workers act first (independent), the coordinator acts last.

    Non-coordinator members act once, in declared order, with no peer context — they
    are independent workers. The coordinator (``protocol.coordinator_id``) acts last
    and reads ALL prior member outputs, so it routes/summarizes/arbitrates over the
    team's work.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        coordinator = interaction.protocol.coordinator_id
        acted = set(interaction.acted_agent_ids())
        for member_id in interaction.member_ids:
            if member_id != coordinator and member_id not in acted:
                return Turn(
                    index=len(interaction.turns),
                    agent_id=member_id,
                    status=TurnStatus.PENDING,
                )
        if coordinator is not None and coordinator not in acted:
            return Turn(
                index=len(interaction.turns),
                agent_id=coordinator,
                status=TurnStatus.PENDING,
            )
        return None

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        if turn.agent_id == interaction.protocol.coordinator_id:
            return interaction.messages
        return ()


class DebateStrategy:
    """DEBATE/BROADCAST: every member acts each round, reading the full transcript.

    Debate is multi-round: the same members repeat across rounds (so this does NOT
    use ``_next_unacted_turn``). The number of rounds is read from
    the validated MAS protocol metadata (default 2). For ``rounds * n`` total
    turns the next member is ``member_ids[total % n]`` (round-robin); after that
    the debate is complete. Every acting turn reads ALL prior messages — members
    rebut each other.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        members = interaction.member_ids
        n = len(members)
        if n == 0:
            return None
        rounds = debate_rounds(interaction.protocol)
        total = len(interaction.turns)
        if total >= rounds * n:
            return None
        return Turn(
            index=total,
            agent_id=members[total % n],
            status=TurnStatus.PENDING,
        )

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        return interaction.messages


class VoteStrategy:
    """VOTE/BROADCAST: each member votes once, independently.

    Every member acts exactly once (``_next_unacted_turn``) and reads NO peer
    messages — voters are independent so they do not see each other's votes before
    voting. The deterministic tally over the votes happens at aggregation.
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        return _next_unacted_turn(interaction)

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        return ()


class DelegationStrategy:
    """DELEGATION/COORDINATOR: the delegator acts first, then each delegate.

    The delegator (``protocol.coordinator_id``) acts FIRST on the task and emits an
    instruction; then each remaining member (a delegate) acts in declared order,
    reading the delegator's message. The delegator sees no peer context (it acts on
    the task); each delegate reads the accumulated messages (the delegator's
    instruction plus any prior delegate output).
    """

    def next_turn(self, interaction: Interaction) -> Turn | None:
        delegator = interaction.protocol.coordinator_id
        acted = set(interaction.acted_agent_ids())
        if delegator is not None and delegator not in acted:
            return Turn(
                index=len(interaction.turns),
                agent_id=delegator,
                status=TurnStatus.PENDING,
            )
        for member_id in interaction.member_ids:
            if member_id != delegator and member_id not in acted:
                return Turn(
                    index=len(interaction.turns),
                    agent_id=member_id,
                    status=TurnStatus.PENDING,
                )
        return None

    def visible_messages(
        self, interaction: Interaction, turn: Turn
    ) -> tuple[Message, ...]:
        if turn.agent_id == interaction.protocol.coordinator_id:
            return ()
        return interaction.messages


_STRATEGIES: dict[
    tuple[CollaborationMode, CollaborationTopology], SchedulingStrategy
] = {
    (CollaborationMode.TEAM, CollaborationTopology.BROADCAST): BroadcastStrategy(),
    (CollaborationMode.TEAM, CollaborationTopology.PIPELINE): PipelineStrategy(),
    (CollaborationMode.TEAM, CollaborationTopology.COORDINATOR): CoordinatorStrategy(),
    (CollaborationMode.DEBATE, CollaborationTopology.BROADCAST): DebateStrategy(),
    (CollaborationMode.VOTE, CollaborationTopology.BROADCAST): VoteStrategy(),
    (
        CollaborationMode.DELEGATION,
        CollaborationTopology.COORDINATOR,
    ): DelegationStrategy(),
}


def select_strategy(protocol: CollaborationProtocol) -> SchedulingStrategy:
    """Return the scheduling strategy for a protocol, failing closed if unexecuted.

    The freeze predicate is the single source of truth: any value outside the
    executed envelope raises ``UnexecutedProtocolError`` rather than silently
    scheduling a flat fan-out.
    """

    violations = executed_protocol_violations(protocol)
    if violations:
        raise UnexecutedProtocolError(violations)
    key = (protocol.mode, protocol.topology)
    strategy = _STRATEGIES.get(key)
    if strategy is None:
        raise UnexecutedProtocolError(
            (f"no scheduling strategy for mode/topology {key}",)
        )
    return strategy


def select_next_turn(interaction: Interaction) -> Turn | None:
    """Return the next turn to execute, or None when the interaction is complete."""

    return select_strategy(interaction.protocol).next_turn(interaction)
