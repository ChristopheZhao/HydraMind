"""OrchestratorAgent — drives a workflow to terminal state.

See ``docs/architecture/40-orchestration.md``. Algorithm is intentionally thin:
schedule the next ready node, run the execution harness, build a report, hand to
the control plane, and act on the resulting RuntimeDecision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from hydramind.control.control_plane import ControlPlane, RuntimeDecision, RuntimeDecisionKind
from hydramind.control.models import (
    AgentReport,
    GateDecisionInput,
    RuntimeSession,
    WorkflowBlueprint,
)
from hydramind.control.states import NodeStatus, SessionStatus
from hydramind.harness.base import ModelHint
from hydramind.harness.provider import ModelProvider
from hydramind.observability import (
    Emitter,
    ObservationEventKind,
)
from hydramind.orchestration.agent_context import AgentPromptContextBuilder
from hydramind.orchestration.agent_execution import (
    AgentExecutionRuntime,
    new_trace_id,
)
from hydramind.orchestration.agent_invocation import (
    AgentNodeInvoker,
    NodeInvocationHandler,
    ReportBuilder,
    ToolProvider,
    default_report_builder,
)
from hydramind.orchestration.agent_tools import (
    AgentToolLoop,
    ToolRunner,
)
from hydramind.orchestration.collaboration import (
    CollaborationExecutor,
)
from hydramind.orchestration.execution_harness import (
    ExecutionEpisodeRequest,
    ExecutionHarness,
    HydraMindExecutionHarness,
    ProviderExecutionHarnessRuntime,
)
from hydramind.orchestration.graph import WorkflowGraph
from hydramind.orchestration.memory_context import MemoryContextRetriever
from hydramind.orchestration.planning_contracts import (
    NodeExecutionMode,
)
from hydramind.orchestration.prompts import PromptLibrary
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.orchestration.verification import VerifierRunner

__all__ = [
    "ExecutionHarnessDependencies",
    "ExecutionHarnessFactory",
    "OrchestratorAgent",
    "ReportBuilder",
    "ToolProvider",
    "ToolRunner",
    "default_report_builder",
]


@dataclass(frozen=True)
class ExecutionHarnessDependencies:
    """Already-built collaborators available to an injected harness."""

    execution: AgentExecutionRuntime
    context_builder: AgentPromptContextBuilder
    tool_provider: ToolProvider | None
    tool_loop: AgentToolLoop
    collaboration: CollaborationExecutor
    report_builder: ReportBuilder | None
    verifier_runner: VerifierRunner | None
    node_invoker: AgentNodeInvoker
    max_tool_rounds: int


ExecutionHarnessFactory = Callable[[ExecutionHarnessDependencies], ExecutionHarness]


def _default_execution_harness_factory(
    deps: ExecutionHarnessDependencies,
) -> ExecutionHarness:
    return HydraMindExecutionHarness(node_invoker=deps.node_invoker)


class OrchestratorAgent:
    """User-facing entry point. Sequences execution harness → control."""

    def __init__(
        self,
        provider: ModelProvider,
        control: ControlPlane,
        workflow: WorkflowBlueprint,
        *,
        prompts: PromptLibrary | None = None,
        tool_provider: ToolProvider | None = None,
        tool_runner: ToolRunner | None = None,
        report_builder: ReportBuilder | None = None,
        verifier_runner: VerifierRunner | None = None,
        emitter: Emitter | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tool_rounds: int = 4,
        max_steps: int = 100,
        memory_retriever: MemoryContextRetriever | None = None,
        execution_harness_factory: ExecutionHarnessFactory | None = None,
    ) -> None:
        self._provider = provider
        self._control = control
        self._workflow = workflow
        self._graph = WorkflowGraph(workflow)
        self._model_hint = model_hint
        self._max_tool_rounds = max_tool_rounds
        self._max_steps = max_steps
        self._execution_harness_runtime = ProviderExecutionHarnessRuntime(self._provider)
        # ADR-0012: the harness DECLARES the sub-agent capability policy
        # (``execution_harness_runtime.capabilities``); ORCHESTRATION owns the
        # spawn ACT via this spawner. Durable interaction/turn state stays
        # Control-owned; the spawner performs no durable writes.
        self._subagent_spawner = SubagentSpawner.from_runtime(
            self._execution_harness_runtime
        )
        self._execution = AgentExecutionRuntime(
            provider=self._provider,
            subagent_spawner=self._subagent_spawner,
            control=self._control,
            emitter=emitter,
            model_hint=self._model_hint,
        )
        self._context_builder = AgentPromptContextBuilder(
            workflow=self._workflow,
            prompts=prompts or PromptLibrary(),
            memory_retriever=memory_retriever,
        )
        self._tool_loop = AgentToolLoop(
            control=self._control,
            tool_runner=tool_runner,
            emit_trace=self._execution.emit_trace,
            invoke_model=self._execution.invoke_model,
            max_tool_rounds=self._max_tool_rounds,
        )
        self._collaboration = CollaborationExecutor(
            subagent_spawner=self._subagent_spawner,
            model_hint=self._model_hint,
            max_tool_rounds=self._max_tool_rounds,
            emit_trace=self._execution.emit_trace,
            execute_tool_round=self._tool_loop.execute_tool_round,
            tool_context_for=self._tool_loop.tool_context_for,
            record_interaction_event=self._control.record_interaction_event,
            durable_recorder=self._control,
        )
        self._node_invoker = AgentNodeInvoker(
            execution=self._execution,
            context_builder=self._context_builder,
            tool_provider=tool_provider,
            tool_loop=self._tool_loop,
            collaboration=self._collaboration,
            report_builder=report_builder,
            verifier_runner=verifier_runner,
        )
        # Route node execution through the typed episode-level ExecutionHarness
        # (95 Phase 2 / S2b). The harness proposes outcomes + evidence; Control
        # still owns durable RuntimeSession transitions.
        self._execution_harness = (
            execution_harness_factory or _default_execution_harness_factory
        )(
            ExecutionHarnessDependencies(
                execution=self._execution,
                context_builder=self._context_builder,
                tool_provider=tool_provider,
                tool_loop=self._tool_loop,
                collaboration=self._collaboration,
                report_builder=report_builder,
                verifier_runner=verifier_runner,
                node_invoker=self._node_invoker,
                max_tool_rounds=self._max_tool_rounds,
            )
        )

    @property
    def workflow(self) -> WorkflowBlueprint:
        return self._workflow

    @property
    def graph(self) -> WorkflowGraph:
        return self._graph

    async def start_session(
        self,
        *,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeSession:
        return await self._control.service.create_session(
            self._workflow,
            input_payload=input_payload,
            metadata=metadata,
        )

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision:
        """Drive the session forward until terminal or gate halt."""
        last_decision: RuntimeDecision | None = None
        for _ in range(self._max_steps):
            session = await self._control.service.get_session(session_id)
            if session.status in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.CANCELLED,
            }:
                return last_decision or RuntimeDecision(
                    kind=(
                        RuntimeDecisionKind.COMPLETE
                        if session.status is SessionStatus.COMPLETED
                        else RuntimeDecisionKind.FAIL
                    ),
                    session_id=session_id,
                    node_key="",
                    # Mirror ControlPlane.record_session_complete: a terminal
                    # session with no nodes gets a status-derived fallback
                    # instead of a fabricated node lookup.
                    node_status=(
                        next(iter(session.nodes.values())).status
                        if session.nodes
                        else (
                            NodeStatus.COMPLETED
                            if session.status is SessionStatus.COMPLETED
                            else NodeStatus.FAILED
                        )
                    ),
                    error=session.error_message,
                )
            if session.status is SessionStatus.WAITING_GATE:
                if last_decision is None:
                    raise RuntimeError(
                        f"session {session_id} is WAITING_GATE but no gate decision "
                        f"is recorded in this run — call resume_session() with a decision"
                    )
                return last_decision

            recovered = await self._control.recover_expired_node_executions(
                session_id,
                actor=execution_owner,
            )
            if recovered:
                session = await self._control.service.get_session(session_id)

            # S5b: recover expired DURABLE TURN leases on the same scheduling
            # pass, so a crashed mid-interaction turn (a member turn whose lease
            # outlived its worker) becomes resumable in the live loop — not only
            # via a direct API/test call. recover_expired_turn_leases takes only
            # the session_id (it derives the as_of cutoff itself); it has no
            # actor/owner arg, unlike the node-execution recovery above.
            recovered_turns = await self._control.recover_expired_turn_leases(session_id)
            if recovered_turns:
                session = await self._control.service.get_session(session_id)

            ready = self._graph.ready_nodes(session)
            if not ready:
                # DAG exhausted while RUNNING (no pending gate, no failure): the
                # orchestrator DECIDES no work remains and RECORDS session
                # completion via control (ADR-0008). DAG readiness is a declared
                # structural recipe (config), not a control-made decision.
                summary = self._completion_summary(session)
                return await self._control.record_session_complete(
                    session_id,
                    summary=summary,
                )

            node_key = ready[0]
            spec = self._workflow.node_spec(node_key)
            trace_id = new_trace_id(session_id, node_key)
            execution = await self._control.open_node_execution(
                session_id,
                node_key,
                trace_id=trace_id,
            )
            lease_token: str | None = None
            if execution_owner is not None:
                leased = await self._control.grant_node_execution_lease(
                    session_id,
                    node_key,
                    execution.id,
                    owner=execution_owner,
                    ttl_seconds=lease_ttl_seconds,
                )
                lease_token = leased.lease_token
            try:
                report = await self._execution.invoke_with_lease_heartbeat(
                    partial(
                        self._invoke_for_node,
                        session=session,
                        node_key=node_key,
                        agent_role=spec.role,
                        node_config=spec.config,
                        execution_id=execution.id,
                        trace_id=trace_id,
                        lease_token=lease_token,
                    ),
                    session_id=session.id,
                    execution_id=execution.id,
                    lease_token=lease_token,
                    lease_ttl_seconds=lease_ttl_seconds,
                    lease_heartbeat_interval_seconds=lease_heartbeat_interval_seconds,
                )
            except Exception as exc:
                await self._execution.emit_trace(
                    ObservationEventKind.NODE_EXECUTION_FAILED,
                    session_id=session_id,
                    node_key=node_key,
                    execution_id=execution.id,
                    trace_id=trace_id,
                    level="error",
                    detail={"error": str(exc), "error_type": type(exc).__name__},
                )
                report = AgentReport(
                    node_key=node_key,
                    agent_id=spec.role,
                    execution_id=execution.id,
                    trace_id=trace_id,
                    lease_token=lease_token,
                    error=str(exc),
                )
            last_decision = await self._control.open_runtime_decision(session_id, report)
            if last_decision.kind is not RuntimeDecisionKind.CONTINUE:
                return last_decision

        raise RuntimeError(
            f"orchestrator hit max_steps={self._max_steps} without completing session {session_id}"
        )

    async def resume_session(
        self,
        session_id: str,
        decision: GateDecisionInput | None = None,
    ) -> RuntimeDecision:
        if decision is not None:
            applied = await self._control.apply_decision(session_id, decision)
            if applied.kind is not RuntimeDecisionKind.CONTINUE:
                return applied
        return await self.run_session(session_id)

    # ---- internal ----------------------------------------------------------

    def _dispatch_table(
        self,
    ) -> dict[NodeExecutionMode, NodeInvocationHandler]:
        """Type-directed node dispatch (ADR-0007): enum member -> async handler.

        Keys are EXACTLY the :class:`NodeExecutionMode` members; a lock-step
        contract test asserts table-keys == enum so the advertised surface, the
        enum, and the dispatch can never silently diverge. No raw
        ``execution_mode`` string comparison routes a node.
        """

        return {
            **self._node_invoker.dispatch_table(),
        }

    @staticmethod
    def _completion_summary(session: RuntimeSession) -> dict[str, Any] | None:
        """Summary for the recorded session completion: last completed node output.

        Preserves prior behavior where ``complete_session`` received the final
        node's output. The summary is the persisted output of the most recently
        updated COMPLETED node.
        """
        completed = [node for node in session.nodes.values() if node.status is NodeStatus.COMPLETED]
        if not completed:
            return None
        last = max(completed, key=lambda node: node.updated_at)
        attempt = last.latest_attempt()
        if attempt is None or not attempt.output:
            return None
        return dict(attempt.output)

    async def _invoke_for_node(
        self,
        *,
        session: RuntimeSession,
        node_key: str,
        agent_role: str,
        node_config: dict[str, Any],
        execution_id: str,
        trace_id: str,
        lease_token: str | None = None,
    ) -> AgentReport:
        request = ExecutionEpisodeRequest(
            session=session,
            node_key=node_key,
            agent_role=agent_role,
            node_config=node_config,
            execution_id=execution_id,
            trace_id=trace_id,
            lease_token=lease_token,
        )
        await self._execution.emit_trace(
            ObservationEventKind.EXECUTION_HARNESS_SELECTED,
            session_id=session.id,
            node_key=node_key,
            execution_id=execution_id,
            trace_id=trace_id,
            detail={"harness_id": self._execution_harness.name},
        )
        outcome = await self._execution_harness.run_episode(request)
        return outcome.report
