"""S2b contract tests: typed ``ExecutionHarness`` episode contract.

Prove (PLAN-20260618-001 S2b / 95 Phase 2):

* ``ExecutionEpisodeRequest``/``ExecutionEpisodeOutcome`` are frozen and their
  load-bearing fields are typed (no ``Any``-typed load-bearing output);
* ``HydraMindExecutionHarness`` satisfies the ``ExecutionHarness`` Protocol and
  ``run_episode`` returns an ``ExecutionEpisodeOutcome`` wrapping the report;
* the harness does NOT mutate ``RuntimeSession`` ("harness proposes, control
  owns transitions");
* the harness is on the live orchestrator path (full session still completes).
"""

from __future__ import annotations

import pytest

import hydramind.orchestration as orchestration_api
from hydramind.control import (
    AgentReport,
    ControlPlane,
    InMemorySessionStore,
    NodeStatus,
    RuntimeDecisionKind,
    RuntimeSession,
    SessionService,
    SessionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.control.models import VerifierResult
from hydramind.harness import Message, MessageRole
from hydramind.harness.base import StopReason, ToolCall
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.orchestration import OrchestratorAgent
from hydramind.orchestration.agent import ExecutionHarnessDependencies
from hydramind.orchestration.execution_harness import (
    ContextCompactionRequest,
    ExecutionConstraints,
    ExecutionEpisodeOutcome,
    ExecutionEpisodeRequest,
    ExecutionHarness,
    ExecutionHarnessCapabilities,
    ExecutionHarnessCapabilityError,
    ExecutionHarnessFeature,
    ExecutionHarnessPolicy,
    ExecutionHarnessRuntime,
    FailureCategory,
    HydraMindExecutionHarness,
    ModelInvocationEvidence,
    ProposedStateTransition,
    ProposedTransitionKind,
    ProviderExecutionHarnessRuntime,
    RecoveryPolicy,
    RecoverySignal,
    RecoverySignalKind,
    SubagentPolicy,
    SubagentSpawnRequest,
    ToolCallEvidence,
)
from hydramind.orchestration.explicit_submit_execution_harness import ExplicitSubmitExecutionHarness
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import ToolContext, ToolExecutionResult, ToolRegistry


def _linear_blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="linear",
        nodes=(
            WorkflowNodeSpec(key="plan", role="planner"),
            WorkflowNodeSpec(key="write", role="writer", requires=("plan",)),
        ),
    )


def _wire(harness: MockProvider) -> tuple[OrchestratorAgent, SessionService]:
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=harness,
        control=plane,
        workflow=_linear_blueprint(),
    )
    return agent, service


def _submit_factory(deps: ExecutionHarnessDependencies) -> ExplicitSubmitExecutionHarness:
    return ExplicitSubmitExecutionHarness(
        execution=deps.execution,
        context_builder=deps.context_builder,
        tool_provider=deps.tool_provider,
        tool_loop=deps.tool_loop,
        collaboration=deps.collaboration,
        report_builder=deps.report_builder,
        verifier_runner=deps.verifier_runner,
        max_tool_rounds=deps.max_tool_rounds,
    )


class _PassingVerifier:
    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, object],
        report: AgentReport,
    ) -> AgentReport:
        del session, node_config
        return report.model_copy(
            update={
                "verifier_results": (
                    VerifierResult(name="react.conformance", passed=True),
                )
            }
        )


class _FailingVerifier:
    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, object],
        report: AgentReport,
    ) -> AgentReport:
        del session, node_config
        return report.model_copy(
            update={
                "verifier_results": (
                    VerifierResult(name="react.conformance", passed=False),
                )
            }
        )


class _SingleShotHarness:
    name = "single-shot"

    async def run_episode(
        self, request: ExecutionEpisodeRequest
    ) -> ExecutionEpisodeOutcome:
        return ExecutionEpisodeOutcome(
            report=AgentReport(
                node_key=request.node_key,
                agent_id=request.agent_role,
                harness_id=self.name,
                output={"text": "single shot"},
            ),
            final_result="single shot",
        )


def _react_conformance_assertions(outcome: ExecutionEpisodeOutcome) -> None:
    assert outcome.report.harness_id == ExplicitSubmitExecutionHarness.name
    assert outcome.failure is FailureCategory.NONE
    assert outcome.final_result == "final answer"
    assert [item.round_no for item in outcome.model_invocations] == [0, 1, 2]
    assert [item.round_no for item in outcome.tool_evidence] == [1, 2]
    assert [item.tool_name for item in outcome.tool_evidence] == ["test.echo", "test.echo"]
    assert outcome.verifier_evidence
    assert outcome.proposed_transitions[0].kind is ProposedTransitionKind.COMPLETE


def test_request_and_outcome_are_frozen() -> None:
    assert ExecutionEpisodeRequest.model_config.get("frozen") is True
    assert ExecutionEpisodeOutcome.model_config.get("frozen") is True


def test_outcome_load_bearing_fields_are_typed() -> None:
    """No ``Any``-typed load-bearing output field on the (broad) outcome contract."""

    fields = ExecutionEpisodeOutcome.model_fields
    assert set(fields) == {
        "report",
        "failure",
        "final_result",
        "model_invocations",
        "tool_evidence",
        "verifier_evidence",
        "trace_event_refs",
        "proposed_transitions",
        "recovery_signals",
    }
    assert fields["report"].annotation is AgentReport
    assert fields["failure"].annotation is FailureCategory
    # Broad outputs are concretely typed (no Any / untyped dict for load-bearing).
    assert fields["final_result"].annotation == (str | None)
    assert fields["model_invocations"].annotation == tuple[ModelInvocationEvidence, ...]
    assert fields["tool_evidence"].annotation == tuple[ToolCallEvidence, ...]
    assert fields["verifier_evidence"].annotation == tuple[VerifierResult, ...]
    assert fields["trace_event_refs"].annotation == tuple[str, ...]
    assert (
        fields["proposed_transitions"].annotation
        == tuple[ProposedStateTransition, ...]
    )
    assert fields["recovery_signals"].annotation == tuple[RecoverySignal, ...]


def test_request_carries_trimmed_typed_policy_surface() -> None:
    """The policy expresses harness-owned SELF-CONTAINED knobs (ADR-0010 §F).

    PLAN-20260623-001 (A) trimmed the inert ``*_ref`` carriers; the surface now is
    exactly ``multi_turn`` + ``constraints`` + ``recovery`` + ``subagents``, each a
    self-contained knob the harness owns, and carries NO unresolved ``*_ref`` field.
    """

    fields = ExecutionEpisodeRequest.model_fields
    assert fields["policy"].annotation is ExecutionHarnessPolicy
    # Default request still constructs with only the original fields (ADDITIVE).
    policy = ExecutionHarnessPolicy()
    assert policy.multi_turn is True
    assert set(ExecutionHarnessPolicy.model_fields) == {
        "multi_turn",
        "constraints",
        "recovery",
        "subagents",
    }
    # Typed sub-policies (no untyped Any) for the load-bearing self-contained knobs.
    pf = ExecutionHarnessPolicy.model_fields
    assert pf["constraints"].annotation is ExecutionConstraints
    assert pf["recovery"].annotation is RecoveryPolicy
    assert pf["subagents"].annotation is SubagentPolicy
    # The trimmed surface carries no dangling external-resolution ref (96 §9 smell).
    assert not [name for name in pf if name.endswith(("_ref", "_refs"))]
    assert not [
        name
        for name in ExecutionConstraints.model_fields
        if name.endswith(("_ref", "_refs"))
    ]


def test_harness_satisfies_protocol() -> None:
    agent, _ = _wire(MockProvider())
    assert isinstance(agent._execution_harness, ExecutionHarness)
    assert isinstance(
        HydraMindExecutionHarness(node_invoker=agent._node_invoker),
        ExecutionHarness,
    )


def test_provider_runtime_exposes_harness_owned_capabilities() -> None:
    runtime = ProviderExecutionHarnessRuntime(MockProvider())

    assert isinstance(runtime, ExecutionHarnessRuntime)
    assert isinstance(runtime.capabilities, ExecutionHarnessCapabilities)
    assert runtime.capabilities.supports(
        ExecutionHarnessFeature.MULTI_TURN,
        ExecutionHarnessFeature.SUBAGENTS,
        ExecutionHarnessFeature.TEAM_INTERACTION,
        ExecutionHarnessFeature.TOOL_LOOP_STRATEGY,
        ExecutionHarnessFeature.OBSERVABILITY_EMISSION,
        ExecutionHarnessFeature.VERIFIER_INTEGRATION,
        ExecutionHarnessFeature.RECOVERY_STRATEGY,
        ExecutionHarnessFeature.BUDGET_POLICY,
    )
    assert not runtime.capabilities.supports(ExecutionHarnessFeature.COMPACTION)
    runtime.capabilities.require(
        ExecutionHarnessFeature.SUBAGENTS,
        operation="n2-contract",
    )


@pytest.mark.asyncio
async def test_orchestration_spawner_instantiates_subagent_from_harness_capabilities() -> None:
    # ADR-0012: the harness DECLARES the capability policy; ORCHESTRATION owns the
    # spawn act. The spawner consults the harness capability declaration and
    # instantiates the sub-agent; the harness surface no longer spawns.
    runtime = ProviderExecutionHarnessRuntime(MockProvider())
    spawner = SubagentSpawner.from_runtime(runtime)
    subagent = await spawner.spawn(
        SubagentSpawnRequest(role="researcher", instructions="Find references.")
    )

    result = await subagent.send(Message(role=MessageRole.USER, content="go"))
    summary = await subagent.close()

    assert subagent.role == "researcher"
    assert result.content == "mock-echo: go"
    assert summary == "mock-echo: go"


@pytest.mark.asyncio
async def test_harness_runtime_surface_exposes_no_spawn() -> None:
    # The narrow harness data plane never spawns (ADR-0012 §3 invariant 2).
    runtime = ProviderExecutionHarnessRuntime(MockProvider())
    assert not hasattr(runtime, "spawn_subagent")
    assert not hasattr(ExecutionHarnessRuntime, "spawn_subagent")


@pytest.mark.asyncio
async def test_runtime_compaction_fails_through_harness_capability_surface() -> None:
    runtime = ProviderExecutionHarnessRuntime(MockProvider())

    with pytest.raises(ExecutionHarnessCapabilityError, match="compaction"):
        await runtime.compact_context(ContextCompactionRequest(session_id="sess-1"))


def test_execution_helpers_are_not_public_api() -> None:
    for name in (
        "ExecutionHarness",
        "HydraMindExecutionHarness",
        "ExecutionEpisodeRequest",
        "ExecutionEpisodeOutcome",
        "FailureCategory",
    ):
        assert name not in orchestration_api.__all__


@pytest.mark.asyncio
async def test_run_episode_wraps_report_and_does_not_mutate_session() -> None:
    backend = MockProvider(scripted=[ScriptedTurn(content='{"outline": "ok"}')])
    agent, service = _wire(backend)
    session = await agent.start_session(input_payload={"topic": "x"})
    session = await service.get_session(session.id)

    before = session.model_dump()
    request = ExecutionEpisodeRequest(
        session=session,
        node_key="plan",
        agent_role="planner",
        node_config={},
        execution_id="exec-1",
        trace_id="trace-1",
    )
    outcome = await agent._execution_harness.run_episode(request)

    assert isinstance(outcome, ExecutionEpisodeOutcome)
    assert isinstance(outcome.report, AgentReport)
    assert outcome.report.node_key == "plan"
    assert outcome.failure is FailureCategory.NONE
    # Harness proposes; control owns transitions -> session unchanged.
    assert session.model_dump() == before


@pytest.mark.asyncio
async def test_run_episode_populates_broad_typed_outputs_additively() -> None:
    """N1: enriched typed outputs are populated from existing report data,
    behavior-preserving, and ``run_episode`` does not mutate ``RuntimeSession``.

    Accepting a broad typed ``policy``/``resume`` must NOT change the outcome the
    default harness produces from the SAME ``AgentNodeInvoker`` path.
    """

    backend = MockProvider(scripted=[ScriptedTurn(content='{"text": "draft body"}')])
    agent, service = _wire(backend)
    session = await agent.start_session(input_payload={"topic": "x"})
    session = await service.get_session(session.id)

    before = session.model_dump()
    # Construct with the broad typed policy surface to prove it type-checks +
    # is accepted, and that passing it does not alter behavior (ADDITIVE).
    request = ExecutionEpisodeRequest(
        session=session,
        node_key="plan",
        agent_role="planner",
        node_config={},
        execution_id="exec-1",
        trace_id="trace-1",
        policy=ExecutionHarnessPolicy(
            multi_turn=True,
            constraints=ExecutionConstraints(max_turns=8),
            recovery=RecoveryPolicy(max_retries=2),
            subagents=SubagentPolicy(max_subagents=3),
        ),
    )
    outcome = await agent._execution_harness.run_episode(request)

    # Typed broad outputs derived from already-available report data.
    assert outcome.failure is FailureCategory.NONE
    assert outcome.final_result == "draft body"
    assert isinstance(outcome.verifier_evidence, tuple)
    assert all(isinstance(v, VerifierResult) for v in outcome.verifier_evidence)
    assert outcome.proposed_transitions == (
        ProposedStateTransition(
            node_key="plan", kind=ProposedTransitionKind.COMPLETE, reason=""
        ),
    )
    # Success episode emits no recovery signal.
    assert outcome.recovery_signals == ()
    # Proposed transition maps to a control ApplyIntentKind (authorization stays
    # in control; the harness only proposes).
    assert (
        outcome.proposed_transitions[0].kind.to_apply_intent_kind().value == "complete"
    )
    # Harness proposes; control owns transitions -> session unchanged.
    assert session.model_dump() == before


@pytest.mark.asyncio
async def test_react_harness_conformance_multi_round_tools_and_done() -> None:
    provider = MockProvider(
        scripted=[
            ScriptedTurn(
                content="plan: call first tool",
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="test.echo",
                        arguments={"value": "first"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(
                content="observe first; call second tool",
                tool_calls=(
                    ToolCall(
                        id="call-2",
                        name="test.echo",
                        arguments={"value": "second"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true, "submit": "final answer"}'),
        ]
    )

    async def echo(args, context):
        del context
        return ToolExecutionResult.ok({"echo": args["value"]})

    tools = ToolRegistry(default_context=ToolContext(dry_run=False))
    tools.register_function(
        name="test.echo",
        description="echo",
        input_schema={"type": "object", "additionalProperties": True},
        handler=echo,
    )
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=provider,
        control=control,
        workflow=WorkflowBlueprint(
            name="submit-flow",
            nodes=(WorkflowNodeSpec(key="work", role="worker"),),
        ),
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=_PassingVerifier(),
        execution_harness_factory=_submit_factory,
    )
    session = await agent.start_session()
    execution = await control.open_node_execution(
        session.id,
        "work",
        trace_id="trace-react",
    )
    snapshot = await service.get_session(session.id)

    outcome = await agent._execution_harness.run_episode(
        ExecutionEpisodeRequest(
            session=snapshot,
            node_key="work",
            agent_role="worker",
            node_config={},
            execution_id=execution.id,
            trace_id="trace-react",
            policy=ExecutionHarnessPolicy(
                constraints=ExecutionConstraints(max_turns=4),
            ),
        )
    )

    _react_conformance_assertions(outcome)
    stored = await service.get_session(session.id)
    ledger = stored.nodes["work"].attempts[-1].tool_executions
    assert [item.tool_call_id for item in ledger] == ["call-1", "call-2"]


@pytest.mark.asyncio
async def test_react_harness_accepts_submit_after_last_tool_round() -> None:
    provider = MockProvider(
        scripted=[
            ScriptedTurn(
                content="plan: call tool",
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="test.echo",
                        arguments={"value": "first"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true, "submit": "final answer"}'),
        ]
    )

    async def echo(args, context):
        del context
        return ToolExecutionResult.ok({"echo": args["value"]})

    tools = ToolRegistry(default_context=ToolContext(dry_run=False))
    tools.register_function(
        name="test.echo",
        description="echo",
        input_schema={"type": "object", "additionalProperties": True},
        handler=echo,
    )
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=provider,
        control=control,
        workflow=WorkflowBlueprint(
            name="submit-boundary-flow",
            nodes=(WorkflowNodeSpec(key="work", role="worker"),),
        ),
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=_PassingVerifier(),
        execution_harness_factory=_submit_factory,
    )
    session = await agent.start_session()
    execution = await control.open_node_execution(
        session.id,
        "work",
        trace_id="trace-submit-boundary",
    )
    snapshot = await service.get_session(session.id)

    outcome = await agent._execution_harness.run_episode(
        ExecutionEpisodeRequest(
            session=snapshot,
            node_key="work",
            agent_role="worker",
            node_config={},
            execution_id=execution.id,
            trace_id="trace-submit-boundary",
            policy=ExecutionHarnessPolicy(
                constraints=ExecutionConstraints(max_turns=1),
            ),
        )
    )

    assert outcome.failure is FailureCategory.NONE
    assert outcome.final_result == "final answer"
    assert [item.round_no for item in outcome.model_invocations] == [0, 1]
    assert [item.round_no for item in outcome.tool_evidence] == [1]


@pytest.mark.asyncio
async def test_single_shot_fake_fails_harness_conformance() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(
        WorkflowBlueprint(
            name="fake-flow",
            nodes=(WorkflowNodeSpec(key="work", role="worker"),),
        )
    )

    outcome = await _SingleShotHarness().run_episode(
        ExecutionEpisodeRequest(
            session=session,
            node_key="work",
            agent_role="worker",
            node_config={},
            execution_id="exec-single",
            trace_id="trace-single",
        )
    )

    with pytest.raises(AssertionError):
        _react_conformance_assertions(outcome)


@pytest.mark.asyncio
async def test_react_harness_threads_verifier_failure_and_recovery_signal() -> None:
    provider = MockProvider(
        scripted=[ScriptedTurn(content='{"done": true, "submit": "bad"}')]
    )
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=provider,
        control=control,
        workflow=WorkflowBlueprint(
            name="submit-fail-flow",
            nodes=(WorkflowNodeSpec(key="work", role="worker"),),
        ),
        verifier_runner=_FailingVerifier(),
        execution_harness_factory=_submit_factory,
    )
    session = await agent.start_session()
    execution = await control.open_node_execution(
        session.id,
        "work",
        trace_id="trace-submit-fail",
    )
    snapshot = await service.get_session(session.id)

    outcome = await agent._execution_harness.run_episode(
        ExecutionEpisodeRequest(
            session=snapshot,
            node_key="work",
            agent_role="worker",
            node_config={},
            execution_id=execution.id,
            trace_id="trace-submit-fail",
        )
    )

    assert outcome.report.harness_id == ExplicitSubmitExecutionHarness.name
    assert outcome.failure is FailureCategory.VERIFICATION_FAILED
    assert outcome.verifier_evidence[0].passed is False
    assert outcome.proposed_transitions[0].kind is ProposedTransitionKind.FAIL
    assert outcome.recovery_signals == (
        RecoverySignal(
            kind=RecoverySignalKind.NON_RETRYABLE,
            failure=FailureCategory.VERIFICATION_FAILED,
            detail=FailureCategory.VERIFICATION_FAILED.value,
        ),
    )


@pytest.mark.asyncio
async def test_react_team_mode_threads_verifier_failure_and_recovery_signal() -> None:
    team = TeamSpec(
        id="submit-team",
        members=(
            AgentSpec(id="researcher", role="researcher"),
            AgentSpec(id="writer", role="writer"),
        ),
        protocol={"topology": "pipeline"},
    )
    node_config = {"mas_team": team.model_dump(mode="json")}
    provider = MockProvider(
        scripted=[
            ScriptedTurn(content="research done"),
            ScriptedTurn(content="writer done"),
        ]
    )
    service = SessionService(InMemorySessionStore())
    control = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=provider,
        control=control,
        workflow=WorkflowBlueprint(
            name="submit-team-flow",
            nodes=(
                WorkflowNodeSpec(
                    key="collaborate",
                    role="coordinator",
                    config=node_config,
                ),
            ),
        ),
        verifier_runner=_FailingVerifier(),
        execution_harness_factory=_submit_factory,
    )
    session = await agent.start_session()
    execution = await control.open_node_execution(
        session.id,
        "collaborate",
        trace_id="trace-submit-team-fail",
    )
    snapshot = await service.get_session(session.id)

    outcome = await agent._execution_harness.run_episode(
        ExecutionEpisodeRequest(
            session=snapshot,
            node_key="collaborate",
            agent_role="coordinator",
            node_config=node_config,
            execution_id=execution.id,
            trace_id="trace-submit-team-fail",
        )
    )

    assert outcome.report.harness_id == ExplicitSubmitExecutionHarness.name
    assert outcome.failure is FailureCategory.VERIFICATION_FAILED
    assert outcome.verifier_evidence[0].passed is False
    assert outcome.proposed_transitions[0].kind is ProposedTransitionKind.FAIL
    assert outcome.recovery_signals == (
        RecoverySignal(
            kind=RecoverySignalKind.NON_RETRYABLE,
            failure=FailureCategory.VERIFICATION_FAILED,
            detail=FailureCategory.VERIFICATION_FAILED.value,
        ),
    )


@pytest.mark.asyncio
async def test_orchestrator_runs_full_session_through_harness_path() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "intro,body"}'),
            ScriptedTurn(content='{"text": "full draft"}'),
        ]
    )
    agent, service = _wire(backend)
    session = await agent.start_session(input_payload={"topic": "x"})
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED
    assert s.nodes["plan"].status is NodeStatus.COMPLETED
    assert s.nodes["write"].status is NodeStatus.COMPLETED
