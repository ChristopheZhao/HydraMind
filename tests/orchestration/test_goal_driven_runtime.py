"""Goal-driven orchestration path."""

from __future__ import annotations

import json

import pytest

from hydramind.control import (
    ControlPlane,
    DecisionAction,
    Gate,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    NodeStatus,
    RuntimeDecisionKind,
    SessionService,
    SessionStatus,
    TaskContract,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.gating import GateRegistry, VerifierFeedbackEvaluator
from hydramind.harness import ToolCall
from hydramind.harness.base import StopReason
from hydramind.memory import InMemoryMemoryStore, MemoryScope
from hydramind.orchestration import (
    ExecutionPlan,
    GoalDrivenOrchestratorAgent,
    GoalSpec,
    MemoryContextPolicy,
    MemoryContextQuery,
    OrchestratorAgent,
    PlanDelta,
    PlanTaskSpec,
    PromptLibrary,
    PromptTemplate,
    StoreMemoryContextRetriever,
    TaskContractVerifierRunner,
)
from hydramind.orchestration.planning_contracts import (
    with_goal_expected_artifacts,
    with_goal_quality_contract,
)
from hydramind.orchestration.planning_payloads import plan_name
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import ToolContext, build_default_tool_registry


class StubGoalPlanner:
    """A deterministic test-local ``PlannerProvider`` double.

    The rule-based ``StaticGoalPlanner`` was deleted in S96; these tests still
    need a deterministic planner that projects a goal to a single ``work`` task
    (carrying the goal's expected-artifacts/quality-contract) without driving the
    harness. This double implements the ``PlannerProvider`` protocol directly and
    is NOT importable from the package, so it is not a production rule engine.
    """

    async def initial_plan(self, goal: GoalSpec) -> ExecutionPlan:
        tasks = goal.suggested_tasks or (self._default_task(goal),)
        return with_goal_quality_contract(
            with_goal_expected_artifacts(
                ExecutionPlan(
                    name=plan_name(goal.objective),
                    goal=goal,
                    tasks=tuple(tasks),
                    rationale="stub planner",
                )
            )
        )

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        del goal, current_plan
        return PlanDelta(
            rationale="stub planner does not mutate the plan",
            feedback_refs=feedback,
        )

    @staticmethod
    def _default_task(goal: GoalSpec) -> PlanTaskSpec:
        team = goal.teams[0] if len(goal.teams) == 1 else None
        agent = goal.agents[0] if team is None and len(goal.agents) == 1 else None
        contract = TaskContract(
            objective=goal.objective,
            acceptance_criteria=goal.success_criteria,
            expected_artifacts=goal.expected_artifacts,
            negative_cases=goal.constraints,
            quality_contract=goal.quality_contract,
        )
        return PlanTaskSpec(
            key="work",
            role="executor",
            description=goal.objective,
            agent=agent,
            team=team,
            contract=contract,
            tools=goal.available_tools,
        )


class FeedbackReplanPlanner(StubGoalPlanner):
    def __init__(self) -> None:
        self.feedback_seen: tuple[str, ...] = ()

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        del goal, current_plan
        self.feedback_seen = feedback
        return PlanDelta(
            add_tasks=(
                PlanTaskSpec(
                    key="review",
                    role="reviewer",
                    requires=("work",),
                ),
            ),
            rationale="add review after verifier feedback",
            feedback_refs=feedback,
        )


class UpdateResearchPlanner(StubGoalPlanner):
    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        del goal, current_plan
        return PlanDelta(
            update_tasks=(
                PlanTaskSpec(
                    key="research",
                    role="researcher",
                    description="revised research scope",
                ),
            ),
            rationale="revise research task",
            feedback_refs=feedback,
        )


class ArtifactRepairPlanner(StubGoalPlanner):
    def __init__(self) -> None:
        self.feedback_seen: tuple[str, ...] = ()

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        del goal, current_plan
        self.feedback_seen = feedback
        return PlanDelta(
            update_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    tools=("artifact.write_text",),
                    contract=TaskContract(
                        expected_artifacts=("reports/done.txt",),
                    ),
                ),
            ),
            rationale="repair missing artifact by enabling artifact writer",
            feedback_refs=feedback,
        )


class RequiredToolRoutingPlanner(StubGoalPlanner):
    def __init__(self) -> None:
        self.feedback_seen: tuple[str, ...] = ()

    async def initial_plan(self, goal: GoalSpec) -> ExecutionPlan:
        return ExecutionPlan(
            name="required-tool-routing",
            goal=goal,
            tasks=(
                PlanTaskSpec(
                    key="search",
                    role="researcher",
                    description="Call the search tool first.",
                    tools=("search.web",),
                ),
            ),
            rationale="start with search only",
        )

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta:
        self.feedback_seen = feedback
        # Deterministic routing: when feedback names a missing required tool that
        # is declared available, add a dependent task that calls exactly that tool.
        for item in feedback:
            if "image.generate" in item and "image.generate" in goal.available_tools:
                last_key = (
                    current_plan.tasks[-1].key if current_plan.tasks else None
                )
                return PlanDelta(
                    add_tasks=(
                        PlanTaskSpec(
                            key="use_image_generate",
                            role="executor",
                            description=(
                                "Call required tool image.generate and report "
                                "its result."
                            ),
                            requires=(last_key,) if last_key else (),
                            tools=("image.generate",),
                            contract=TaskContract(
                                objective="Call required tool image.generate.",
                                acceptance_criteria=(
                                    "Tool image.generate has a successful "
                                    "ToolExecution record.",
                                ),
                            ),
                        ),
                    ),
                    rationale=(
                        "stub planner added missing required tool task(s): "
                        "image.generate"
                    ),
                    feedback_refs=feedback,
                )
        return PlanDelta(
            rationale="stub planner does not mutate the plan",
            feedback_refs=feedback,
        )


@pytest.mark.asyncio
async def test_goal_driven_run_completes_without_workflow_yaml() -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(scripted=[ScriptedTurn(content='{"done": true}')])
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
    )

    session = await agent.run_goal(
        GoalSpec(objective="Produce a concise status report")
    )

    assert session.status is SessionStatus.COMPLETED
    assert session.workflow_name.startswith("goal-produce-a-concise-status-report")
    assert session.metadata["goal"]["objective"] == "Produce a concise status report"
    assert "execution_plan" in session.metadata
    assert session.nodes["work"].latest_attempt().output == {"done": True}


@pytest.mark.asyncio
async def test_goal_driven_executor_receives_opted_in_memory_context() -> None:
    store = InMemoryMemoryStore()
    await store.append(
        MemoryScope.WORKFLOW,
        "goal-memory-workflow",
        "episode.previous",
        {"lesson": "prefer exact dates"},
    )
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(scripted=[ScriptedTurn(content='{"done": true}')])
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
        memory_retriever=StoreMemoryContextRetriever(store),
        prompts=PromptLibrary(
            {
                "writer": PromptTemplate(
                    role="writer",
                    system="writer",
                    user_template="{memory_context}",
                ),
            }
        ),
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Use prior memory",
            suggested_tasks=(PlanTaskSpec(key="work", role="writer"),),
            memory_context=MemoryContextPolicy(
                enabled=True,
                queries=(
                    MemoryContextQuery(
                        scope=MemoryScope.WORKFLOW,
                        scope_id="goal-memory-workflow",
                        key_prefix="episode.",
                    ),
                ),
            ),
        )
    )

    prompt = json.loads(backend.invocations[0]["messages"][0].content)
    assert prompt["entries"][0]["value"] == {"lesson": "prefer exact dates"}
    assert session.metadata["goal"]["memory_context"]["enabled"] is True
    assert "entries" not in session.metadata["goal"]["memory_context"]


@pytest.mark.asyncio
async def test_goal_without_available_tools_exposes_no_default_tools(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(scripted=[ScriptedTurn(content='{"done": true}')])
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.run_goal(GoalSpec(objective="Do work without tools"))

    assert session.status is SessionStatus.COMPLETED
    assert backend.invocations[0]["tools"] == []


@pytest.mark.asyncio
async def test_goal_tools_are_limited_to_declared_task_scope(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_json",
                        arguments={"path": "goal/out.json", "data": {"ok": True}},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Write a JSON artifact",
            available_tools=("artifact.write_json",),
        )
    )

    assert session.status is SessionStatus.COMPLETED
    assert [tool.name for tool in backend.invocations[0]["tools"]] == [
        "artifact.write_json"
    ]
    assert (tmp_path / "goal" / "out.json").exists()
    assert session.nodes["work"].latest_attempt().output == {"used_tool": True}


@pytest.mark.asyncio
async def test_goal_verifier_runner_passes_expected_artifact(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "reports/done.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=StubGoalPlanner(),
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=TaskContractVerifierRunner(tmp_path),
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Write done marker",
            available_tools=("artifact.write_text",),
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    tools=("artifact.write_text",),
                    contract=TaskContract(
                        expected_artifacts=("reports/done.txt",),
                    ),
                ),
            ),
        )
    )

    output = session.nodes["work"].latest_attempt().output
    assert session.status is SessionStatus.COMPLETED
    assert output["_verifier_results"][0]["passed"] is True
    assert output["_verifier_results"][0]["evidence_refs"] == ["reports/done.txt"]


@pytest.mark.asyncio
async def test_goal_verifier_feedback_gate_halts_missing_artifact(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(scripted=[ScriptedTurn(content='{"done": true}')])
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=StubGoalPlanner(),
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=TaskContractVerifierRunner(tmp_path),
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Forget to write report",
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    contract=TaskContract(
                        expected_artifacts=("reports/missing.txt",),
                    ),
                ),
            ),
        )
    )

    gate = session.nodes["work"].latest_gate()
    output = session.nodes["work"].latest_attempt().output
    assert session.status is SessionStatus.WAITING_GATE
    assert gate is not None
    assert gate.name == "verifier_feedback"
    assert gate.outcome is GateOutcome.REQUIRES_DECISION
    assert output["_verifier_results"][0]["passed"] is False
    assert output["_feedback"][0]["suggested_action"] == "revise"


@pytest.mark.asyncio
async def test_goal_replan_decision_applies_delta_and_resumes(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    planner = FeedbackReplanPlanner()
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"draft": "v1"}'),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "reports/done.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
            ScriptedTurn(content='{"reviewed": true}'),
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=planner,
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=TaskContractVerifierRunner(tmp_path),
        # Exercise the MANUAL external-decision REPLAN path: disable the
        # agent-driven auto-repair loop so the gate is surfaced to the operator.
        max_repair_attempts=0,
    )
    session = await agent.start_goal(
        GoalSpec(
            objective="Write and review report",
            available_tools=("artifact.write_text",),
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    tools=("artifact.write_text",),
                    contract=TaskContract(
                        expected_artifacts=("reports/done.txt",),
                    ),
                ),
            ),
        )
    )

    halted = await agent.run_goal_session(session.id)
    assert halted.kind is RuntimeDecisionKind.AWAIT_GATE
    assert halted.gate is not None

    decision = await agent.resume_goal_session(
        session.id,
        GateDecisionInput(
            gate_id=halted.gate.id,
            action=DecisionAction.REPLAN,
            payload={"feedback": "operator requested a review step"},
        ),
    )
    stored = await service.get_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert stored.status is SessionStatus.COMPLETED
    assert set(stored.nodes) == {"work", "review"}
    assert stored.nodes["review"].latest_attempt().output == {"reviewed": True}
    assert stored.metadata["execution_plan"]["tasks"][1]["key"] == "review"
    assert "operator requested a review step" in planner.feedback_seen
    assert any("missing expected artifact" in item for item in planner.feedback_seen)


@pytest.mark.asyncio
async def test_agent_repair_replans_verifier_gate(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    planner = ArtifactRepairPlanner()
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"draft": "missing artifact"}'),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "reports/done.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"done": true}'),
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=planner,
        tool_provider=tools,
        tool_runner=tools,
        verifier_runner=TaskContractVerifierRunner(tmp_path),
        max_repair_attempts=1,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Repair missing artifact automatically",
            available_tools=("artifact.write_text",),
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    contract=TaskContract(
                        expected_artifacts=("reports/done.txt",),
                    ),
                ),
            ),
        )
    )

    work = session.nodes["work"]
    first_gate = work.gates[0]
    assert session.status is SessionStatus.COMPLETED
    assert len(work.attempts) == 2
    assert first_gate.decision is not None
    assert first_gate.decision.action is DecisionAction.REPLAN
    assert first_gate.decision.actor == "orchestrator"
    assert "missing expected artifact" in planner.feedback_seen[0]
    assert (tmp_path / "reports" / "done.txt").exists()
    assert work.latest_attempt().output["_verifier_results"][0]["passed"] is True


@pytest.mark.asyncio
async def test_required_tool_feedback_routes_to_missing_tool_node(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    planner = RequiredToolRoutingPlanner()
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="search-1",
                        name="search.web",
                        arguments={"query": "HydraMind", "count": 1},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"search_done": true}'),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="image-1",
                        name="image.generate",
                        arguments={"prompt": "HydraMind architecture diagram"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"image_done": true}'),
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=planner,
        tool_provider=tools,
        tool_runner=tools,
        max_repair_attempts=1,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Search, then generate an image.",
            available_tools=("search.web", "image.generate"),
            required_tools=("search.web", "image.generate"),
        )
    )

    search = session.nodes["search"]
    image = session.nodes["use_image_generate"]
    search_tools = [
        tool.tool_name
        for attempt in search.attempts
        for tool in attempt.tool_executions
    ]
    image_tools = [
        tool.tool_name
        for attempt in image.attempts
        for tool in attempt.tool_executions
    ]

    assert session.status is SessionStatus.COMPLETED
    assert search_tools == ["search.web"]
    assert image_tools == ["image.generate"]
    assert any("image.generate" in item for item in planner.feedback_seen)
    assert search.gates[0].decision is not None
    assert search.gates[0].decision.action is DecisionAction.APPROVE
    assert search.gates[0].decision.payload["original_action"] == "replan"
    assert session.metadata["execution_plan"]["tasks"][1]["key"] == "use_image_generate"
    assert session.metadata["workflow_revisions"][-1]["added_node_keys"] == [
        "use_image_generate"
    ]
    assert [
        [tool.name for tool in invocation["tools"]]
        for invocation in backend.invocations
    ] == [
        ["search.web"],
        ["search.web"],
        ["image.generate"],
        ["image.generate"],
    ]


@pytest.mark.asyncio
async def test_agent_declines_repair_surfaces_gate(tmp_path) -> None:
    # When the planner AGENT returns an empty delta, it has decided NOT to
    # repair: the verifier-failure gate is surfaced for an external/human
    # decision instead of looping (ADR-0008: the decision is the agent's).
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"done": false}'),
        ]
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=StubGoalPlanner(),
        verifier_runner=TaskContractVerifierRunner(tmp_path),
        max_repair_attempts=1,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Cannot repair missing artifact",
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    contract=TaskContract(
                        expected_artifacts=("reports/missing.txt",),
                    ),
                ),
            ),
        )
    )

    work = session.nodes["work"]
    assert session.status is SessionStatus.WAITING_GATE
    # The empty-delta decision surfaces the gate without an extra attempt.
    assert len(work.attempts) == 1
    assert len(work.gates) == 1
    assert work.gates[0].decision is None


@pytest.mark.asyncio
async def test_repair_disabled_when_max_attempts_zero(tmp_path) -> None:
    # max_repair_attempts=0 disables the agent-repair safety loop entirely:
    # a verifier-failure gate is surfaced and no revise_plan is attempted.
    service = SessionService(InMemorySessionStore())
    planner = ArtifactRepairPlanner()
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"draft": "missing artifact"}'),
        ]
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(
            service,
            gate_fn=GateRegistry([VerifierFeedbackEvaluator()]).to_gate_fn(),
        ),
        planner=planner,
        verifier_runner=TaskContractVerifierRunner(tmp_path),
        max_repair_attempts=0,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="No auto repair",
            suggested_tasks=(
                PlanTaskSpec(
                    key="work",
                    role="writer",
                    contract=TaskContract(
                        expected_artifacts=("reports/missing.txt",),
                    ),
                ),
            ),
        )
    )

    work = session.nodes["work"]
    assert session.status is SessionStatus.WAITING_GATE
    assert len(work.attempts) == 1
    assert work.gates[0].decision is None
    # The planner agent was never asked to revise (no repair decision sought).
    assert planner.feedback_seen == ()


@pytest.mark.asyncio
async def test_undeclared_tool_call_fails_goal_node(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="image.generate",
                        arguments={"prompt": "blue square"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            )
        ]
    )
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.run_goal(
        GoalSpec(
            objective="Write text only",
            available_tools=("artifact.write_text",),
        )
    )

    assert session.status is SessionStatus.FAILED
    assert "unauthorized tool calls: image.generate" in (session.error_message or "")


def test_execution_plan_applies_delta_with_dependency_validation() -> None:
    goal = GoalSpec(objective="Research and write")
    base = ExecutionPlan(
        name="goal-research-and-write",
        goal=goal,
        tasks=(PlanTaskSpec(key="research", role="researcher"),),
    )

    updated = base.apply_delta(
        PlanDelta(
            add_tasks=(
                PlanTaskSpec(
                    key="write",
                    role="writer",
                    requires=("research",),
                ),
            ),
            rationale="add final writer",
        )
    )

    assert [task.key for task in updated.tasks] == ["research", "write"]
    assert updated.metadata["last_plan_delta_rationale"] == "add final writer"

    with pytest.raises(ValueError, match="requires unknown"):
        base.apply_delta(
            PlanDelta(
                add_tasks=(
                    PlanTaskSpec(
                        key="write",
                        role="writer",
                        requires=("missing",),
                    ),
                )
            )
        )


@pytest.mark.asyncio
async def test_goal_session_applies_plan_delta_and_runs_added_task() -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"research": "done"}'),
            ScriptedTurn(content='{"report": "done"}'),
        ]
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
    )

    session = await agent.start_goal(
        GoalSpec(
            objective="Research and write",
            suggested_tasks=(
                PlanTaskSpec(key="research", role="researcher"),
            ),
        )
    )
    updated = await agent.apply_plan_delta(
        session.id,
        PlanDelta(
            add_tasks=(
                PlanTaskSpec(
                    key="write",
                    role="writer",
                    requires=("research",),
                ),
            ),
            rationale="writer added after initial plan",
        ),
    )
    decision = await agent.run_goal_session(session.id)
    stored = await service.get_session(session.id)

    assert [task.key for task in updated.tasks] == ["research", "write"]
    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert set(stored.nodes) == {"research", "write"}
    assert stored.nodes["write"].latest_attempt().output == {"report": "done"}
    assert stored.metadata["execution_plan"]["tasks"][1]["key"] == "write"


@pytest.mark.asyncio
async def test_goal_plan_delta_removes_task_without_losing_node_history() -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider(scripted=[ScriptedTurn(content='{"report": "done"}')])
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        planner=StubGoalPlanner(),
    )
    session = await agent.start_goal(
        GoalSpec(
            objective="Write directly",
            suggested_tasks=(
                PlanTaskSpec(key="research", role="researcher"),
                PlanTaskSpec(key="write", role="writer", requires=("research",)),
            ),
        )
    )

    updated = await agent.apply_plan_delta(
        session.id,
        PlanDelta(
            remove_task_keys=("research",),
            update_tasks=(PlanTaskSpec(key="write", role="writer"),),
            rationale="research no longer required",
        ),
    )
    decision = await agent.run_goal_session(session.id)
    stored = await service.get_session(session.id)

    assert [task.key for task in updated.tasks] == ["write"]
    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert stored.nodes["research"].status is NodeStatus.STALE
    assert stored.nodes["write"].latest_attempt().output == {"report": "done"}
    assert stored.metadata["execution_plan"]["tasks"][0]["key"] == "write"
    assert stored.metadata["workflow_revisions"][-1]["removed_node_keys"] == [
        "research"
    ]


@pytest.mark.asyncio
async def test_goal_plan_delta_update_requeues_changed_node_after_gate() -> None:
    service = SessionService(InMemorySessionStore())
    gate_count = 0

    async def gate_once(session, node, report):
        nonlocal gate_count
        del session, report
        if node.key == "research" and gate_count == 0:
            gate_count += 1
            return GateOutcome.REQUIRES_DECISION
        return None

    async def gate_fn(session, node, report):
        outcome = await gate_once(session, node, report)
        if outcome is None:
            return None
        return Gate(name="research_review", node_key=node.key, outcome=outcome)

    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"research": "v1"}'),
            ScriptedTurn(content='{"research": "v2"}'),
            ScriptedTurn(content='{"report": "done"}'),
        ]
    )
    agent = GoalDrivenOrchestratorAgent(
        provider=backend,
        control=ControlPlane(service, gate_fn=gate_fn),
        planner=UpdateResearchPlanner(),
    )
    session = await agent.start_goal(
        GoalSpec(
            objective="Research then write",
            suggested_tasks=(
                PlanTaskSpec(key="research", role="researcher"),
                PlanTaskSpec(key="write", role="writer", requires=("research",)),
            ),
        )
    )

    halted = await agent.run_goal_session(session.id)
    assert halted.kind is RuntimeDecisionKind.AWAIT_GATE
    assert halted.gate is not None

    decision = await agent.resume_goal_session(
        session.id,
        GateDecisionInput(
            gate_id=halted.gate.id,
            action=DecisionAction.REPLAN,
            payload={"feedback": "redo research with narrower scope"},
        ),
    )
    stored = await service.get_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert [attempt.output for attempt in stored.nodes["research"].attempts] == [
        {"research": "v1"},
        {"research": "v2"},
    ]
    assert stored.nodes["write"].latest_attempt().output == {"report": "done"}


@pytest.mark.asyncio
async def test_downstream_prompt_receives_upstream_context() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "intro"}'),
            ScriptedTurn(content='{"draft": "ok"}'),
        ]
    )
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="context-flow",
            nodes=(
                WorkflowNodeSpec(key="plan", role="planner"),
                WorkflowNodeSpec(key="write", role="writer", requires=("plan",)),
            ),
        ),
        prompts=PromptLibrary(
            {
                "planner": PromptTemplate(
                    role="planner",
                    system="planner",
                    user_template="{input}",
                ),
                "writer": PromptTemplate(
                    role="writer",
                    system="writer",
                    user_template="{context}",
                ),
            }
        ),
    )

    session = await agent.start_session(input_payload={"topic": "X"})
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    writer_message = backend.invocations[1]["messages"][0].content
    context = json.loads(writer_message)
    assert context["nodes"]["plan"]["output"] == {"outline": "intro"}


@pytest.mark.asyncio
async def test_subagent_execution_mode_reports_through_control() -> None:
    service = SessionService(InMemorySessionStore())
    backend = MockProvider()
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="subagent-flow",
            nodes=(
                WorkflowNodeSpec(
                    key="research",
                    role="researcher",
                    config={"execution_mode": "subagent"},
                ),
            ),
        ),
    )

    session = await agent.start_session(input_payload={"topic": "HydraMind"})
    decision = await agent.run_session(session.id)
    stored = await service.get_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    output = stored.nodes["research"].latest_attempt().output
    assert output["text"].startswith("mock-echo:")
    assert backend.invocations
