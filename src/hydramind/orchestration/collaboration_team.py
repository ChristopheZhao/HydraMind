"""Native MAS team execution for orchestration collaboration."""

from __future__ import annotations

from typing import Any

from hydramind.control.interaction_state import (
    DurableInteraction,
    durable_interaction_id,
)
from hydramind.harness.base import (
    InvocationResult,
    ModelHint,
)
from hydramind.kernel.contracts import TurnStatus
from hydramind.mas import (
    CollaborationMode,
    TeamSpec,
    require_executed_team,
)
from hydramind.mas.protocol_outcomes import CoordinatorOutcome, VoteOutcome
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
    DurableInteractionRecorder,
    InteractionLogRecorderFn,
    TraceEmitterFn,
)
from hydramind.orchestration.collaboration_events import NativeTeamEventEmitter
from hydramind.orchestration.collaboration_interaction import (
    member_result_from_durable,
    team_invocation_result,
    team_member_content,
    team_typed_outcome,
)
from hydramind.orchestration.collaboration_logging import NativeTeamInteractionLogger
from hydramind.orchestration.collaboration_member import NativeTeamMemberRunner
from hydramind.orchestration.collaboration_member_strategy import MemberTurnStrategy
from hydramind.orchestration.collaboration_runtime import (
    NativeTeamInteractionRuntime,
)
from hydramind.orchestration.subagent_spawn import SubagentSpawner


class NativeTeamExecutor:
    """Executes first-class ``TeamSpec`` collaboration nodes."""

    def __init__(
        self,
        *,
        subagent_spawner: SubagentSpawner,
        model_hint: ModelHint,
        emit_trace: TraceEmitterFn,
        record_interaction_event: InteractionLogRecorderFn | None = None,
        durable_recorder: DurableInteractionRecorder | None = None,
    ) -> None:
        self._events = NativeTeamEventEmitter(
            model_hint=model_hint,
            emit_trace=emit_trace,
        )
        self._member_runner = NativeTeamMemberRunner(
            subagent_spawner=subagent_spawner,
        )
        self._interaction_log = NativeTeamInteractionLogger(record_interaction_event)
        # Authoritative durable interaction state seam (S5a). Distinct from the
        # preview projection above: records full-content turns/messages + the
        # typed protocol outcome through the control boundary as the team runs.
        # Record-only — no scheduling/turn-selection change.
        self._durable = durable_recorder

    async def invoke(
        self,
        request: CollaborationExecutionRequest,
        *,
        member_strategy: MemberTurnStrategy,
    ) -> InvocationResult:
        team = _team_spec_from_config(request.node_config)
        require_executed_team(team)
        await self._events.invocation_started(request=request, team=team)
        # Drive the team as scheduled kernel turns (ADR-0007). The strategy —
        # type-directed by ``protocol.topology`` — decides who acts next and what
        # prior messages that turn may read: BROADCAST keeps members independent,
        # PIPELINE feeds member N the accumulated output of members 1..N-1. The
        # ``Interaction`` remains pure scheduling state here; durable records are
        # submitted through a control-owned logging seam.
        #
        # Resume-from-durable-state (S5b): the durable interaction belongs to the
        # NODE (id keyed by session+node), so a fresh attempt after a crash/
        # requeue can CONTINUE the prior in-progress interaction rather than
        # replaying the whole node. We reconstruct the kernel Interaction from the
        # completed durable turns/messages and the SAME scheduler picks the next
        # not-yet-completed turn.
        interaction_id = durable_interaction_id(request.session_id, request.node_key)
        resume_from = await self._resumable_interaction(request)
        results: list[dict[str, Any]] = []
        if resume_from is not None:
            runtime = NativeTeamInteractionRuntime.seed_from_durable(
                team=team,
                interaction=resume_from,
            )
            results = self._prior_results_from_durable(team, resume_from)
        else:
            runtime = NativeTeamInteractionRuntime(
                team=team,
                interaction_id=interaction_id,
            )
        await self._interaction_log.interaction_started(
            request=request,
            team=team,
            interaction_id=runtime.interaction_id,
            workspace_id=runtime.workspace_id,
        )
        if self._durable is not None:
            # Idempotent: a replayed start returns the existing aggregate, so the
            # resume case does not reset recorded turns. A fresh case creates it.
            await self._durable.start_interaction(
                request.session_id,
                interaction_id=runtime.interaction_id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                team_id=team.id,
                protocol_mode=team.protocol.mode.value,
                topology=team.protocol.topology.value,
                member_ids=tuple(member.id for member in team.members),
                workspace_id=runtime.workspace_id,
            )
            if resume_from is not None:
                await self._durable.mark_interaction_resumed(
                    request.session_id,
                    interaction_id=runtime.interaction_id,
                    recovered_from_turn_index=runtime.completed_turn_count,
                )
        try:
            scheduled_turn = runtime.next_turn()
            while scheduled_turn is not None:
                member = scheduled_turn.member
                turn_detail = scheduled_turn.turn_detail
                turn_index = int(turn_detail["turn_index"])
                # First-class per-turn observability (ADR-0007): the team interaction is
                # visible at agent granularity instead of a single MODEL_INVOKE. These are
                # additive ``emit_trace`` calls through the existing seam — no RuntimeSession
                # mutation (observe-only).
                if scheduled_turn.is_coordinator_handoff:
                    await self._events.coordinator_handoff(
                        request=request,
                        actor=member.id,
                        turn_detail=turn_detail,
                    )
                await self._events.turn_started(
                    request=request,
                    actor=member.id,
                    turn_detail=turn_detail,
                )
                await self._interaction_log.turn_started(
                    request=request,
                    team=team,
                    interaction_id=runtime.interaction_id,
                    workspace_id=runtime.workspace_id,
                    actor=member.id,
                    turn_index=turn_index,
                    detail=turn_detail,
                )
                member_result = await self._member_runner.run(
                    member=member,
                    team=team,
                    request=request,
                    available_tools=request.tools,
                    seed_messages=scheduled_turn.seed_messages,
                    member_strategy=member_strategy,
                )
                results.append(member_result)
                member_content = team_member_content(member_result)
                await self._events.turn_completed(
                    request=request,
                    actor=member.id,
                    turn_detail=turn_detail,
                    member_result=member_result,
                )
                await self._interaction_log.turn_completed(
                    request=request,
                    team=team,
                    interaction_id=runtime.interaction_id,
                    workspace_id=runtime.workspace_id,
                    actor=member.id,
                    turn_index=turn_index,
                    member_result=member_result,
                    detail=turn_detail,
                )
                await self._events.message_sent(
                    request=request,
                    actor=member.id,
                    turn_detail=turn_detail,
                    content=member_content,
                )
                await self._interaction_log.message_sent(
                    request=request,
                    team=team,
                    interaction_id=runtime.interaction_id,
                    workspace_id=runtime.workspace_id,
                    actor=member.id,
                    turn_index=turn_index,
                    content=member_content,
                    detail=turn_detail,
                )
                if self._durable is not None:
                    # Authoritative durable state (S5a): the completed turn and
                    # the message with FULL content (not the bounded preview).
                    # record_interaction_turn defaults to TurnStatus.COMPLETED;
                    # the executor stays free of kernel scheduling status types.
                    await self._durable.record_interaction_turn(
                        request.session_id,
                        interaction_id=runtime.interaction_id,
                        turn_index=turn_index,
                        agent_id=member.id,
                    )
                    await self._durable.record_interaction_message(
                        request.session_id,
                        interaction_id=runtime.interaction_id,
                        turn_index=turn_index,
                        sender=member.id,
                        content=member_content,
                    )
                if team.protocol.mode is CollaborationMode.VOTE:
                    # First-class per-voter observability (ADR-0007): each member's turn
                    # in VOTE mode is a vote; the S99 AGENT_VOTE vocabulary is wired here.
                    await self._events.vote(
                        request=request,
                        actor=member.id,
                        turn_detail=turn_detail,
                        content=member_content,
                    )
                    await self._interaction_log.vote_cast(
                        request=request,
                        team=team,
                        interaction_id=runtime.interaction_id,
                        workspace_id=runtime.workspace_id,
                        actor=member.id,
                        turn_index=turn_index,
                        content=member_content,
                        detail=turn_detail,
                    )
                runtime.record_member_message(
                    scheduled_turn=scheduled_turn,
                    content=member_content,
                )
                scheduled_turn = runtime.next_turn()
            invocation = team_invocation_result(
                team=team,
                coordinator_role=request.agent_role,
                results=results,
            )
            if self._durable is not None:
                # Authoritative typed protocol outcome (S5a), reusing the S3b
                # VoteOutcome/CoordinatorOutcome builders.
                outcome = team_typed_outcome(team, results)
                if isinstance(outcome, VoteOutcome):
                    await self._durable.record_interaction_outcome(
                        request.session_id,
                        interaction_id=runtime.interaction_id,
                        vote=outcome,
                    )
                elif isinstance(outcome, CoordinatorOutcome):
                    await self._durable.record_interaction_outcome(
                        request.session_id,
                        interaction_id=runtime.interaction_id,
                        coordinator=outcome,
                    )
        except Exception as exc:
            await self._interaction_log.interaction_failed(
                request=request,
                team=team,
                interaction_id=runtime.interaction_id,
                workspace_id=runtime.workspace_id,
                error=exc,
            )
            if self._durable is not None:
                await self._durable.fail_interaction(
                    request.session_id,
                    interaction_id=runtime.interaction_id,
                    error=str(exc),
                )
            raise
        await self._events.invocation_completed(
            request=request,
            team=team,
            invocation=invocation,
        )
        await self._interaction_log.interaction_completed(
            request=request,
            team=team,
            interaction_id=runtime.interaction_id,
            workspace_id=runtime.workspace_id,
            invocation=invocation,
        )
        if self._durable is not None:
            # Defaults to InteractionStatus.COMPLETED (no kernel enum named here).
            await self._durable.complete_interaction(
                request.session_id,
                interaction_id=runtime.interaction_id,
            )
        return invocation

    async def _resumable_interaction(
        self,
        request: CollaborationExecutionRequest,
    ) -> DurableInteraction | None:
        """Return the prior in-progress durable interaction to resume, if any."""

        if self._durable is None:
            return None
        return await self._durable.find_resumable_interaction(
            request.session_id, request.node_key
        )

    @staticmethod
    def _prior_results_from_durable(
        team: TeamSpec,
        interaction: DurableInteraction,
    ) -> list[dict[str, Any]]:
        """Rebuild member results for COMPLETED durable turns (no re-run).

        These feed aggregation / typed-outcome so the resumed interaction still
        produces an outcome over ALL members. The completed members are NOT
        re-run — their authoritative content is read from durable state.
        """

        roles_by_id = {member.id: member.role for member in team.members}
        content_by_turn = {
            message.turn_index: message.content for message in interaction.messages
        }
        results: list[dict[str, Any]] = []
        for turn in sorted(interaction.turns, key=lambda t: t.turn_index):
            if turn.status is not TurnStatus.COMPLETED:
                continue
            results.append(
                member_result_from_durable(
                    agent_id=turn.agent_id,
                    role=roles_by_id.get(turn.agent_id, turn.agent_id),
                    content=content_by_turn.get(turn.turn_index, ""),
                )
            )
        return results


def _team_spec_from_config(node_config: dict[str, Any]) -> TeamSpec:
    raw = node_config.get("mas_team")
    if raw is None:
        raise RuntimeError("team execution requires config.mas_team")
    try:
        return TeamSpec.model_validate(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid native MAS team config: {exc}") from exc


__all__ = ["NativeTeamExecutor"]
