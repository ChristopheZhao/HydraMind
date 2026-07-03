"""Kernel scheduler tests (S91): broadcast ordering, completion, loud-fail."""

from __future__ import annotations

import pytest

from hydramind.kernel import Interaction, Message, Turn, TurnStatus, select_next_turn
from hydramind.kernel.scheduler import (
    _STRATEGIES,
    BroadcastStrategy,
    CoordinatorStrategy,
    DebateStrategy,
    DelegationStrategy,
    PipelineStrategy,
    VoteStrategy,
    select_strategy,
)
from hydramind.mas import (
    AggregationStrategy,
    ArbitrationStrategy,
    CollaborationMode,
    CollaborationProtocol,
    CollaborationTopology,
    UnexecutedProtocolError,
    executed_protocol_violations,
)
from hydramind.mas.capability import (
    EXECUTED_MODE_TOPOLOGY_PAIRS,
    EXECUTED_MODES,
    EXECUTED_TOPOLOGIES,
)


def _interaction(turns: tuple[Turn, ...] = ()) -> Interaction:
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("writer", "reviewer"),
        protocol=CollaborationProtocol(),
        turns=turns,
    )


def _pipeline_interaction(
    turns: tuple[Turn, ...] = (), messages: tuple[Message, ...] = ()
) -> Interaction:
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("writer", "reviewer"),
        protocol=CollaborationProtocol(topology=CollaborationTopology.PIPELINE),
        turns=turns,
        messages=messages,
    )


def test_broadcast_schedules_members_in_declared_order() -> None:
    interaction = _interaction()
    first = select_next_turn(interaction)
    assert first is not None
    assert first.agent_id == "writer"
    assert first.index == 0


def test_broadcast_advances_to_next_unacted_member() -> None:
    interaction = _interaction(
        turns=(Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),)
    )
    nxt = select_next_turn(interaction)
    assert nxt is not None
    assert nxt.agent_id == "reviewer"
    assert nxt.index == 1


def test_broadcast_completes_when_all_members_acted() -> None:
    interaction = _interaction(
        turns=(
            Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="reviewer", status=TurnStatus.COMPLETED),
        )
    )
    assert select_next_turn(interaction) is None


def test_single_member_broadcast_is_degenerate() -> None:
    interaction = Interaction(id="i1", team_id="team", member_ids=("solo",))
    turn = select_next_turn(interaction)
    assert turn is not None and turn.agent_id == "solo"
    done = interaction.model_copy(
        update={"turns": (Turn(index=0, agent_id="solo"),)}
    )
    assert select_next_turn(done) is None


def test_select_strategy_returns_broadcast_for_executed_protocol() -> None:
    assert isinstance(select_strategy(CollaborationProtocol()), BroadcastStrategy)


def test_select_strategy_returns_pipeline_for_pipeline_protocol() -> None:
    strategy = select_strategy(
        CollaborationProtocol(topology=CollaborationTopology.PIPELINE)
    )
    assert isinstance(strategy, PipelineStrategy)


def test_pipeline_schedules_members_in_declared_order_and_completes() -> None:
    interaction = _pipeline_interaction()
    first = select_next_turn(interaction)
    assert first is not None and first.agent_id == "writer"
    advanced = _pipeline_interaction(
        turns=(Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),)
    )
    nxt = select_next_turn(advanced)
    assert nxt is not None and nxt.agent_id == "reviewer"
    done = _pipeline_interaction(
        turns=(
            Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="reviewer", status=TurnStatus.COMPLETED),
        )
    )
    assert select_next_turn(done) is None


def test_pipeline_visible_messages_are_accumulated_but_broadcast_are_empty() -> None:
    prior = Message(id="m0", interaction_id="i1", sender="writer", content="draft")
    pipeline = _pipeline_interaction(messages=(prior,))
    pipeline_turn = select_next_turn(pipeline)
    assert pipeline_turn is not None
    pipeline_strategy = select_strategy(pipeline.protocol)
    # PIPELINE: member N reads accumulated prior-turn messages.
    assert pipeline_strategy.visible_messages(pipeline, pipeline_turn) == (prior,)
    # BROADCAST: members stay independent — no peer context.
    broadcast = _interaction()
    broadcast_turn = select_next_turn(broadcast)
    assert broadcast_turn is not None
    broadcast_strategy = select_strategy(broadcast.protocol)
    assert broadcast_strategy.visible_messages(broadcast, broadcast_turn) == ()


def _coordinator_interaction(
    turns: tuple[Turn, ...] = (), messages: tuple[Message, ...] = ()
) -> Interaction:
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("writer", "reviewer", "lead"),
        protocol=CollaborationProtocol(
            topology=CollaborationTopology.COORDINATOR, coordinator_id="lead"
        ),
        turns=turns,
        messages=messages,
    )


def test_select_strategy_returns_coordinator_for_coordinator_protocol() -> None:
    strategy = select_strategy(
        CollaborationProtocol(
            topology=CollaborationTopology.COORDINATOR, coordinator_id="lead"
        )
    )
    assert isinstance(strategy, CoordinatorStrategy)


def test_coordinator_schedules_workers_first_then_coordinator_last() -> None:
    interaction = _coordinator_interaction()
    first = select_next_turn(interaction)
    assert first is not None and first.agent_id == "writer"  # non-coordinator worker
    after_workers = _coordinator_interaction(
        turns=(
            Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="reviewer", status=TurnStatus.COMPLETED),
        )
    )
    last = select_next_turn(after_workers)
    assert last is not None and last.agent_id == "lead"  # coordinator acts last
    done = _coordinator_interaction(
        turns=(
            Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="reviewer", status=TurnStatus.COMPLETED),
            Turn(index=2, agent_id="lead", status=TurnStatus.COMPLETED),
        )
    )
    assert select_next_turn(done) is None


def test_coordinator_sees_all_peers_but_workers_are_independent() -> None:
    w = Message(id="m0", interaction_id="i1", sender="writer", content="draft")
    r = Message(id="m1", interaction_id="i1", sender="reviewer", content="review")
    interaction = _coordinator_interaction(
        turns=(
            Turn(index=0, agent_id="writer", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="reviewer", status=TurnStatus.COMPLETED),
        ),
        messages=(w, r),
    )
    strategy = select_strategy(interaction.protocol)
    coordinator_turn = select_next_turn(interaction)
    assert coordinator_turn is not None and coordinator_turn.agent_id == "lead"
    # Coordinator reads all worker outputs.
    assert strategy.visible_messages(interaction, coordinator_turn) == (w, r)
    # A worker turn sees no peer context.
    worker_turn = Turn(index=0, agent_id="writer", status=TurnStatus.PENDING)
    assert strategy.visible_messages(interaction, worker_turn) == ()


def _debate_interaction(
    turns: tuple[Turn, ...] = (),
    messages: tuple[Message, ...] = (),
    rounds: int | None = None,
) -> Interaction:
    metadata: dict[str, object] = {} if rounds is None else {"rounds": rounds}
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("a", "b"),
        protocol=CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.BROADCAST,
            metadata=metadata,
        ),
        turns=turns,
        messages=messages,
    )


def test_select_strategy_returns_debate_for_debate_protocol() -> None:
    strategy = select_strategy(
        CollaborationProtocol(mode=CollaborationMode.DEBATE)
    )
    assert isinstance(strategy, DebateStrategy)


def test_debate_round_robins_members_for_default_two_rounds() -> None:
    # 2 members x 2 default rounds = 4 turns: a, b, a, b, then complete.
    acted: list[str] = []
    interaction = _debate_interaction()
    while True:
        turn = select_next_turn(interaction)
        if turn is None:
            break
        acted.append(turn.agent_id)
        interaction = interaction.model_copy(
            update={
                "turns": (
                    *interaction.turns,
                    Turn(
                        index=turn.index,
                        agent_id=turn.agent_id,
                        status=TurnStatus.COMPLETED,
                    ),
                )
            }
        )
    assert acted == ["a", "b", "a", "b"]


def test_debate_respects_rounds_metadata() -> None:
    interaction = _debate_interaction(rounds=3)
    # 2 members x 3 rounds = 6 turns; after 6 it completes.
    turns = tuple(
        Turn(index=i, agent_id=("a", "b")[i % 2], status=TurnStatus.COMPLETED)
        for i in range(6)
    )
    assert select_next_turn(_debate_interaction(turns=turns, rounds=3)) is None
    # Mid-debate the next acting member round-robins.
    nxt = select_next_turn(interaction)
    assert nxt is not None and nxt.agent_id == "a" and nxt.index == 0


@pytest.mark.parametrize("rounds", ["bad", True, 0, -1])
def test_debate_rejects_invalid_rounds_before_scheduler(rounds: object) -> None:
    with pytest.raises(ValueError, match=r"debate metadata\.rounds"):
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.BROADCAST,
            metadata={"rounds": rounds},
        )


def test_debate_visible_messages_include_full_transcript() -> None:
    prior = (
        Message(id="m0", interaction_id="i1", sender="a", content="claim"),
        Message(id="m1", interaction_id="i1", sender="b", content="rebut"),
    )
    interaction = _debate_interaction(messages=prior)
    strategy = select_strategy(interaction.protocol)
    turn = select_next_turn(interaction)
    assert turn is not None
    assert strategy.visible_messages(interaction, turn) == prior


def _vote_interaction(turns: tuple[Turn, ...] = ()) -> Interaction:
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("a", "b", "c"),
        protocol=CollaborationProtocol(
            mode=CollaborationMode.VOTE,
            topology=CollaborationTopology.BROADCAST,
            aggregation=AggregationStrategy.VOTE,
            arbitration=ArbitrationStrategy.MAJORITY,
        ),
        turns=turns,
    )


def test_select_strategy_returns_vote_for_vote_protocol() -> None:
    assert isinstance(
        select_strategy(
            CollaborationProtocol(
                mode=CollaborationMode.VOTE,
                aggregation=AggregationStrategy.VOTE,
                arbitration=ArbitrationStrategy.MAJORITY,
            )
        ),
        VoteStrategy,
    )


def test_vote_each_member_acts_once_independently() -> None:
    interaction = _vote_interaction()
    first = select_next_turn(interaction)
    assert first is not None and first.agent_id == "a"
    strategy = select_strategy(interaction.protocol)
    # Voters are independent: no peer context.
    assert strategy.visible_messages(interaction, first) == ()
    done = _vote_interaction(
        turns=tuple(
            Turn(index=i, agent_id=m, status=TurnStatus.COMPLETED)
            for i, m in enumerate(("a", "b", "c"))
        )
    )
    assert select_next_turn(done) is None


def _delegation_interaction(turns: tuple[Turn, ...] = ()) -> Interaction:
    return Interaction(
        id="i1",
        team_id="team",
        member_ids=("worker", "boss"),
        protocol=CollaborationProtocol(
            mode=CollaborationMode.DELEGATION,
            topology=CollaborationTopology.COORDINATOR,
            coordinator_id="boss",
        ),
        turns=turns,
    )


def test_select_strategy_returns_delegation_for_delegation_protocol() -> None:
    strategy = select_strategy(
        CollaborationProtocol(
            mode=CollaborationMode.DELEGATION,
            topology=CollaborationTopology.COORDINATOR,
            coordinator_id="boss",
        )
    )
    assert isinstance(strategy, DelegationStrategy)


def test_delegation_schedules_delegator_first_then_delegates() -> None:
    interaction = _delegation_interaction()
    first = select_next_turn(interaction)
    assert first is not None and first.agent_id == "boss"  # delegator acts first
    strategy = select_strategy(interaction.protocol)
    assert strategy.visible_messages(interaction, first) == ()  # acts on the task
    after_delegator = _delegation_interaction(
        turns=(Turn(index=0, agent_id="boss", status=TurnStatus.COMPLETED),)
    )
    nxt = select_next_turn(after_delegator)
    assert nxt is not None and nxt.agent_id == "worker"  # delegate next
    instruction = Message(
        id="m0", interaction_id="i1", sender="boss", content="do X"
    )
    with_msg = after_delegator.model_copy(update={"messages": (instruction,)})
    assert strategy.visible_messages(with_msg, nxt) == (instruction,)
    done = _delegation_interaction(
        turns=(
            Turn(index=0, agent_id="boss", status=TurnStatus.COMPLETED),
            Turn(index=1, agent_id="worker", status=TurnStatus.COMPLETED),
        )
    )
    assert select_next_turn(done) is None


def test_dispatch_table_is_lock_step_with_executed_envelope() -> None:
    # (a) every dispatched (mode, topology) is within the executed envelope.
    for mode, topology in _STRATEGIES:
        assert mode in EXECUTED_MODES
        assert topology in EXECUTED_TOPOLOGIES
    # (b) every executed mode and topology is dispatched by >=1 strategy
    # (no advertised-but-undispatched value).
    dispatched_modes = {mode for mode, _ in _STRATEGIES}
    dispatched_topologies = {topology for _, topology in _STRATEGIES}
    assert EXECUTED_MODES <= dispatched_modes
    assert EXECUTED_TOPOLOGIES <= dispatched_topologies
    assert frozenset(_STRATEGIES) == EXECUTED_MODE_TOPOLOGY_PAIRS


@pytest.mark.parametrize(
    "protocol",
    [
        # Unsupported (mode, topology) COMBOS — every per-axis value is executed
        # after S100, so only unsupported pairings fail closed now.
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.PIPELINE,
        ),
        CollaborationProtocol(
            mode=CollaborationMode.DEBATE,
            topology=CollaborationTopology.COORDINATOR,
            coordinator_id="a",
        ),
    ],
)
def test_select_strategy_fails_closed_for_unsupported_combo(
    protocol: CollaborationProtocol,
) -> None:
    assert executed_protocol_violations(protocol)
    with pytest.raises(UnexecutedProtocolError, match="mode/topology pair"):
        select_strategy(protocol)
