"""Collaboration execution helpers for native MAS team interactions."""

from __future__ import annotations

from hydramind.harness.base import (
    InvocationResult,
    ModelHint,
)
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
    DurableInteractionRecorder,
    InteractionLogRecorderFn,
    ToolContextFactoryFn,
    ToolRoundExecutorFn,
    TraceEmitterFn,
)
from hydramind.orchestration.collaboration_member_strategy import (
    DefaultMemberTurnStrategy,
    ExplicitSubmitMemberTurnStrategy,
    MemberTurnStrategy,
    MemberTurnTooling,
)
from hydramind.orchestration.collaboration_team import NativeTeamExecutor
from hydramind.orchestration.subagent_spawn import SubagentSpawner

__all__ = [
    "CollaborationExecutionRequest",
    "CollaborationExecutor",
    "ToolContextFactoryFn",
    "ToolRoundExecutorFn",
    "TraceEmitterFn",
]


class CollaborationExecutor:
    """Executes native team collaboration.

    The executor delegates all runtime state writes back through callbacks owned
    by the orchestrator/control path. It never mutates ``RuntimeSession`` and
    never evaluates gates.

    The per-member turn loop is a swappable ``MemberTurnStrategy`` (PLAN-20260621
    M1): the default-harness path uses the batch-drain strategy, and a swapped
    explicit-submit harness supplies ``explicit_submit_member_strategy`` so each MAS
    member is driven by the swapped strategy — the orchestration layer coordinates MULTIPLE
    per-agent harness strategies rather than wrapping only the outer team node.
    """

    def __init__(
        self,
        *,
        subagent_spawner: SubagentSpawner,
        model_hint: ModelHint,
        max_tool_rounds: int,
        emit_trace: TraceEmitterFn,
        execute_tool_round: ToolRoundExecutorFn,
        tool_context_for: ToolContextFactoryFn,
        record_interaction_event: InteractionLogRecorderFn | None = None,
        durable_recorder: DurableInteractionRecorder | None = None,
    ) -> None:
        self._subagent_spawner = subagent_spawner
        self._tooling = MemberTurnTooling(
            execute_tool_round=execute_tool_round,
            tool_context_for=tool_context_for,
            emit_trace=emit_trace,
            model_hint=model_hint,
            max_tool_rounds=max_tool_rounds,
        )
        self._default_member_strategy = DefaultMemberTurnStrategy(self._tooling)
        self._explicit_submit_member_strategy = ExplicitSubmitMemberTurnStrategy(
            self._tooling
        )
        self._native_team = NativeTeamExecutor(
            subagent_spawner=self._subagent_spawner,
            model_hint=model_hint,
            emit_trace=emit_trace,
            record_interaction_event=record_interaction_event,
            durable_recorder=durable_recorder,
        )

    @property
    def explicit_submit_member_strategy(self) -> MemberTurnStrategy:
        """Explicit-submit per-member strategy, supplied by the swapped harness."""

        return self._explicit_submit_member_strategy

    async def invoke_team(
        self,
        request: CollaborationExecutionRequest,
        *,
        member_strategy: MemberTurnStrategy | None = None,
    ) -> InvocationResult:
        return await self._native_team.invoke(
            request,
            member_strategy=member_strategy or self._default_member_strategy,
        )
