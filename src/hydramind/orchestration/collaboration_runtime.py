"""Ephemeral native team interaction runtime for orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hydramind.control.interaction_state import DurableInteraction
from hydramind.harness.base import Message
from hydramind.kernel import Interaction, InteractionStatus, select_strategy
from hydramind.kernel.contracts import Message as InteractionMessage
from hydramind.kernel.contracts import MessageRole as InteractionMessageRole
from hydramind.kernel.contracts import Turn as InteractionTurn
from hydramind.kernel.contracts import TurnStatus as InteractionTurnStatus
from hydramind.mas import AgentSpec, CollaborationTopology, TeamSpec
from hydramind.orchestration.collaboration_interaction import (
    harness_message_from_interaction,
    team_turn_detail,
)


@dataclass(frozen=True)
class NativeTeamScheduledTurn:
    """One scheduled native team member turn ready for execution."""

    member: AgentSpec
    turn_detail: dict[str, Any]
    seed_messages: tuple[Message, ...]
    is_coordinator_handoff: bool
    workspace_id: str | None
    _turn: InteractionTurn


class NativeTeamInteractionRuntime:
    """Advance one team interaction through the pure kernel scheduler."""

    def __init__(
        self,
        *,
        team: TeamSpec,
        interaction_id: str,
        seed_turns: tuple[InteractionTurn, ...] = (),
        seed_messages: tuple[InteractionMessage, ...] = (),
    ) -> None:
        self._team = team
        self._members_by_id = {member.id: member for member in team.members}
        self._strategy = select_strategy(team.protocol)
        # Reconstructing from durable state PRE-SEEDS completed turns/messages so
        # the SAME deterministic scheduler advances to the next not-yet-completed
        # turn (S5b resume). The frozen kernel Interaction is built once, fully,
        # so its _validate_consistency invariant runs over the seeded state and
        # fails loudly if a durable record can't satisfy it.
        self._interaction = Interaction(
            id=interaction_id,
            team_id=team.id,
            member_ids=tuple(self._members_by_id),
            protocol=team.protocol,
            messages=seed_messages,
            turns=seed_turns,
            status=InteractionStatus.RUNNING,
            workspace_id=team.workspace.id if team.workspace is not None else None,
        )
        self._is_coordinator_topology = (
            team.protocol.topology is CollaborationTopology.COORDINATOR
        )

    @classmethod
    def seed_from_durable(
        cls,
        *,
        team: TeamSpec,
        interaction: DurableInteraction,
    ) -> NativeTeamInteractionRuntime:
        """Reconstruct an in-memory runtime from a durable interaction (S5b).

        Only COMPLETED durable turns (and their authoritative messages) are
        reloaded, so the kernel scheduler treats them as already-acted and
        ``next_turn`` returns the NEXT not-yet-completed turn. Already-completed
        turns are NOT re-run (no repeated tool side effects / authoritative
        messages); their messages are reloaded as visible context for the
        remaining members. Kernel scheduling semantics are unchanged — resume
        just pre-seeds state into the SAME scheduler.
        """

        members_by_id = {member.id: member for member in team.members}
        completed = sorted(
            (
                turn
                for turn in interaction.turns
                if turn.status is InteractionTurnStatus.COMPLETED
            ),
            key=lambda turn: turn.turn_index,
        )
        seed_turns: list[InteractionTurn] = []
        seed_messages: list[InteractionMessage] = []
        for turn in completed:
            if turn.agent_id not in members_by_id:
                raise ValueError(
                    f"durable turn agent_id {turn.agent_id!r} is not a current "
                    f"team member; cannot reconstruct interaction {interaction.id!r}"
                )
            turn_messages = tuple(
                InteractionMessage(
                    id=message.id,
                    interaction_id=interaction.id,
                    turn_index=message.turn_index,
                    sender=message.sender,
                    role=InteractionMessageRole.AGENT,
                    content=message.content,
                )
                for message in interaction.messages
                if message.turn_index == turn.turn_index
            )
            seed_messages.extend(turn_messages)
            seed_turns.append(
                InteractionTurn(
                    index=turn.turn_index,
                    agent_id=turn.agent_id,
                    status=InteractionTurnStatus.COMPLETED,
                    message_ids=tuple(m.id for m in turn_messages),
                )
            )
        return cls(
            team=team,
            interaction_id=interaction.id,
            seed_turns=tuple(seed_turns),
            seed_messages=tuple(seed_messages),
        )

    @property
    def completed_turn_count(self) -> int:
        return len(self._interaction.turns)

    @property
    def interaction_id(self) -> str:
        return self._interaction.id

    @property
    def workspace_id(self) -> str | None:
        return self._interaction.workspace_id

    def next_turn(self) -> NativeTeamScheduledTurn | None:
        turn = self._strategy.next_turn(self._interaction)
        if turn is None:
            return None
        member = self._members_by_id[turn.agent_id]
        return NativeTeamScheduledTurn(
            member=member,
            turn_detail=team_turn_detail(
                team=self._team,
                member=member,
                turn=turn,
                interaction_id=self._interaction.id,
                workspace_id=self._interaction.workspace_id,
            ),
            seed_messages=tuple(
                harness_message_from_interaction(message)
                for message in self._strategy.visible_messages(
                    self._interaction,
                    turn,
                )
            ),
            is_coordinator_handoff=(
                self._is_coordinator_topology
                and member.id == self._team.protocol.coordinator_id
            ),
            workspace_id=self._interaction.workspace_id,
            _turn=turn,
        )

    def record_member_message(
        self,
        *,
        scheduled_turn: NativeTeamScheduledTurn,
        content: str,
    ) -> None:
        turn = scheduled_turn._turn
        member = scheduled_turn.member
        produced = InteractionMessage(
            id=f"msg-{turn.index}-{member.id}",
            interaction_id=self._interaction.id,
            turn_index=turn.index,
            sender=member.id,
            role=InteractionMessageRole.AGENT,
            content=content,
        )
        completed = InteractionTurn(
            index=turn.index,
            agent_id=member.id,
            status=InteractionTurnStatus.COMPLETED,
            message_ids=(produced.id,),
        )
        self._interaction = self._interaction.model_copy(
            update={
                "messages": (*self._interaction.messages, produced),
                "turns": (*self._interaction.turns, completed),
            }
        )


__all__ = ["NativeTeamInteractionRuntime", "NativeTeamScheduledTurn"]
