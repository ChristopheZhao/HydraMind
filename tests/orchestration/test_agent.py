"""OrchestratorAgent end-to-end with MockProvider (no live LLM)."""

from __future__ import annotations

import json

import pytest

import hydramind.orchestration as orchestration_api
import hydramind.orchestration.agent as agent_module
import hydramind.orchestration.agent_invocation as agent_invocation_module
from hydramind.control import (
    AgentReport,
    ControlPlane,
    DecisionAction,
    GateDecisionInput,
    InMemorySessionStore,
    InteractionLogEventKind,
    NodeStatus,
    RuntimeDecisionKind,
    SessionService,
    SessionStatus,
    ToolExecutionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.gating import GateContract, GateRegistry, HumanInLoopEvaluator
from hydramind.harness.base import StopReason, ToolCall
from hydramind.mas import AgentSpec, CollaborationProtocol, SharedWorkspace, TeamSpec
from hydramind.memory import InMemoryMemoryStore, MemoryScope
from hydramind.observability import Emitter, ListObserver, ObservationEventKind
from hydramind.orchestration import (
    MemoryContextPolicy,
    MemoryContextQuery,
    OrchestratorAgent,
    PlanTaskSpec,
    PromptLibrary,
    PromptTemplate,
    StoreMemoryContextRetriever,
    WorkflowGraph,
)
from hydramind.orchestration.agent_context import AgentPromptContextBuilder
from hydramind.orchestration.agent_execution import AgentExecutionRuntime
from hydramind.orchestration.agent_tools import AgentToolLoop
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import (
    ToolContext,
    ToolExecutionResult,
    ToolPolicy,
    ToolRegistry,
    ToolRiskClass,
    build_default_tool_registry,
)


def _linear_blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="linear",
        nodes=(
            WorkflowNodeSpec(key="plan", role="planner"),
            WorkflowNodeSpec(key="write", role="writer", requires=("plan",)),
        ),
    )


def _wire(
    *,
    provider: MockProvider | None = None,
    gate_fn=None,
    prompts: PromptLibrary | None = None,
    memory_retriever=None,
) -> tuple[OrchestratorAgent, SessionService]:
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service, gate_fn=gate_fn)
    agent = OrchestratorAgent(
        provider=provider or MockProvider(),
        control=plane,
        workflow=_linear_blueprint(),
        prompts=prompts,
        memory_retriever=memory_retriever,
    )
    return agent, service


def test_agent_tool_loop_boundary_is_internal() -> None:
    assert not hasattr(OrchestratorAgent, "_drain_tool_calls")
    assert not hasattr(OrchestratorAgent, "_execute_tool_round")
    assert not hasattr(OrchestratorAgent, "_run_tool_calls")
    assert not hasattr(OrchestratorAgent, "_tool_context_for")
    assert not hasattr(OrchestratorAgent, "_tool_execution_metadata")
    assert not hasattr(OrchestratorAgent, "_tool_result_metadata")

    assert hasattr(AgentToolLoop, "drain_tool_calls")
    assert hasattr(AgentToolLoop, "execute_tool_round")
    assert "AgentToolLoop" not in orchestration_api.__all__


def test_agent_prompt_context_boundary_is_internal() -> None:
    assert not hasattr(OrchestratorAgent, "_compose_messages")
    assert not hasattr(OrchestratorAgent, "_memory_context_for_node")
    assert not hasattr(agent_module, "_session_memory_context_policy")
    assert not hasattr(agent_module, "_upstream_context")
    assert not hasattr(agent_module, "_execution_context")

    assert hasattr(AgentPromptContextBuilder, "compose_messages")
    assert "AgentPromptContextBuilder" not in orchestration_api.__all__


def test_agent_execution_support_boundary_is_internal() -> None:
    assert not hasattr(OrchestratorAgent, "_invoke_for_node_with_lease_heartbeat")
    assert not hasattr(OrchestratorAgent, "_heartbeat_execution_lease_until_stopped")
    assert not hasattr(OrchestratorAgent, "_invoke_subagent")
    assert not hasattr(OrchestratorAgent, "_invoke_model")
    assert not hasattr(OrchestratorAgent, "_emit_trace")
    assert not hasattr(agent_module, "_new_trace_id")
    assert not hasattr(agent_module, "_lease_heartbeat_interval_seconds")

    assert hasattr(AgentExecutionRuntime, "invoke_with_lease_heartbeat")
    assert hasattr(AgentExecutionRuntime, "invoke_model")
    assert hasattr(AgentExecutionRuntime, "invoke_subagent")
    assert "AgentExecutionRuntime" not in orchestration_api.__all__


def test_agent_node_invocation_boundary_is_internal() -> None:
    assert not hasattr(OrchestratorAgent, "_dispatch_direct")
    assert not hasattr(OrchestratorAgent, "_dispatch_subagent")
    assert not hasattr(OrchestratorAgent, "_dispatch_team")
    assert not hasattr(agent_module, "_NoToolProvider")
    assert not hasattr(agent_module, "_subagent_tool_origin")
    assert not hasattr(agent_module, "_tool_call_names")

    assert hasattr(agent_invocation_module, "AgentNodeInvoker")
    assert hasattr(agent_invocation_module, "default_report_builder")
    assert "AgentNodeInvoker" not in orchestration_api.__all__


@pytest.mark.asyncio
async def test_run_session_drives_linear_workflow_to_completion() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "intro,body,conclusion"}'),
            ScriptedTurn(content='{"text": "full draft"}'),
        ]
    )
    agent, service = _wire(provider=backend)

    session = await agent.start_session(input_payload={"topic": "abstractions"})
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED
    assert s.nodes["plan"].status is NodeStatus.COMPLETED
    assert s.nodes["write"].status is NodeStatus.COMPLETED
    # Completion summary is the last completed node's output, threaded through
    # the orchestrator-driven record (ADR-0008): control did not synthesize it.
    assert s.summary_output == {"text": "full draft"}


@pytest.mark.asyncio
async def test_orchestrator_records_completion_when_dag_exhausted() -> None:
    """The orchestrator (not control) decides completion when no node is ready.

    Drive every node to COMPLETED via control (which never auto-completes the
    session), then prove ``run_session`` detects empty-ready-while-RUNNING and
    records session completion through ``ControlPlane.record_session_complete``.
    This pins the equivalence: ready_nodes empty while RUNNING ⟺ all remaining
    nodes terminal → orchestrator records completion.
    """
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "ok"}'),
            ScriptedTurn(content='{"text": "draft"}'),
        ]
    )
    agent, service = _wire(provider=backend)
    session = await agent.start_session()

    # Manually drive both nodes to COMPLETED through the control plane only.
    plane = agent._control
    await plane.open_runtime_decision(
        session.id, AgentReport(node_key="plan", agent_id="planner", output={"a": 1})
    )
    await plane.open_runtime_decision(
        session.id, AgentReport(node_key="write", agent_id="writer", output={"b": 2})
    )
    # Control recorded both node completions but left the session RUNNING.
    mid = await service.get_session(session.id)
    assert mid.status is SessionStatus.RUNNING
    assert all(node.status is NodeStatus.COMPLETED for node in mid.nodes.values())

    graph = WorkflowGraph(_linear_blueprint())
    assert graph.ready_nodes(mid) == []  # ready-empty ⟺ all-terminal equivalence

    # run_session now finds nothing ready and records completion (one node-free
    # loop iteration, no harness call).
    decision = await agent.run_session(session.id)
    assert decision.kind is RuntimeDecisionKind.COMPLETE
    final = await service.get_session(session.id)
    assert final.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_session_returns_clean_fail_for_terminal_session_without_nodes() -> None:
    """A FAILED session with no nodes returns a FAIL decision, not a crash.

    Regression for PLAN-20260612-001 D1: the terminal-status branch in
    ``run_session`` dereferenced ``session.nodes.get("", None).status`` when
    ``session.nodes`` was empty, raising ``AttributeError`` instead of
    recording the terminal outcome.
    """
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service)
    blueprint = WorkflowBlueprint(name="empty", nodes=())
    agent = OrchestratorAgent(provider=MockProvider(), control=plane, workflow=blueprint)
    session = await service.create_session(blueprint)
    await service.mark_session_running(session.id)
    await service.fail_session(session.id, error="failed before any node existed")

    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.FAIL
    assert decision.node_status is NodeStatus.FAILED
    assert decision.error == "failed before any node existed"


@pytest.mark.asyncio
async def test_run_session_returns_clean_complete_for_terminal_session_without_nodes() -> None:
    """A COMPLETED session with no nodes returns a COMPLETE decision (D1)."""
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service)
    blueprint = WorkflowBlueprint(name="empty", nodes=())
    agent = OrchestratorAgent(provider=MockProvider(), control=plane, workflow=blueprint)
    session = await service.create_session(blueprint)
    await service.mark_session_running(session.id)
    await service.complete_session(session.id)

    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert decision.node_status is NodeStatus.COMPLETED
    assert decision.error is None


@pytest.mark.asyncio
async def test_gate_halts_run_and_resume_continues() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "ok"}'),
            ScriptedTurn(content='{"draft": "..."}'),
        ]
    )
    registry = GateRegistry()
    registry.register(
        HumanInLoopEvaluator(GateContract(name="review", applies_to_nodes=("write",)))
    )
    agent, service = _wire(provider=backend, gate_fn=registry.to_gate_fn())

    session = await agent.start_session()
    halted = await agent.run_session(session.id)
    assert halted.kind is RuntimeDecisionKind.AWAIT_GATE
    assert halted.gate is not None

    s = await service.get_session(session.id)
    assert s.status is SessionStatus.WAITING_GATE

    resumed = await agent.resume_session(
        session.id,
        decision=GateDecisionInput(gate_id=halted.gate.id, action=DecisionAction.APPROVE),
    )
    assert resumed.kind is RuntimeDecisionKind.COMPLETE
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_default_report_builder_parses_json_content() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"k": "v", "n": 1}'),
            ScriptedTurn(content="plain text response"),
        ]
    )
    agent, service = _wire(provider=backend)

    session = await agent.start_session()
    await agent.run_session(session.id)
    s = await service.get_session(session.id)
    plan_output = s.nodes["plan"].attempts[-1].output
    write_output = s.nodes["write"].attempts[-1].output
    assert plan_output == {"k": "v", "n": 1}
    assert write_output == {"text": "plain text response"}


@pytest.mark.asyncio
async def test_prompts_are_rendered_and_passed_to_harness() -> None:
    backend = MockProvider()
    prompts = PromptLibrary(
        {
            "planner": PromptTemplate(
                role="planner",
                system="You are a planner for {workflow_name}.",
                user_template="Plan node {node_key} given input {input}.",
            ),
            "writer": PromptTemplate(
                role="writer",
                system="You write.",
                user_template="{input}",
            ),
        }
    )
    agent, _service = _wire(provider=backend, prompts=prompts)
    session = await agent.start_session(input_payload={"topic": "X"})
    await agent.run_session(session.id)

    first_call = backend.invocations[0]
    assert first_call["system"] == "You are a planner for linear."
    assert first_call["role"] == "planner"
    assert (
        "Plan node 'plan'" in first_call["messages"][0].content
        or "plan" in first_call["messages"][0].content
    )


@pytest.mark.asyncio
async def test_executor_prompt_receives_opted_in_memory_context() -> None:
    store = InMemoryMemoryStore()
    await store.append(
        MemoryScope.WORKFLOW,
        "linear",
        "episode.previous",
        {"lesson": "keep drafts short"},
    )
    prompts = PromptLibrary(
        {
            "planner": PromptTemplate(
                role="planner",
                system="planner",
                user_template="{memory_context}",
            ),
            "writer": PromptTemplate(
                role="writer",
                system="writer",
                user_template="{input}",
            ),
        }
    )
    policy = MemoryContextPolicy(
        enabled=True,
        queries=(
            MemoryContextQuery(
                scope=MemoryScope.WORKFLOW,
                scope_id="linear",
                key_prefix="episode.",
                limit=1,
            ),
        ),
    )
    backend = MockProvider()
    agent, service = _wire(
        provider=backend,
        prompts=prompts,
        memory_retriever=StoreMemoryContextRetriever(store),
    )

    session = await agent.start_session(
        input_payload={"topic": "X"},
        metadata={"memory_context": policy.model_dump(mode="json")},
    )
    await agent.run_session(session.id)

    payload = json.loads(backend.invocations[0]["messages"][0].content)
    assert payload["entries"][0]["value"] == {"lesson": "keep drafts short"}
    assert payload["entries"][0]["evidence_ref"].startswith("memory://workflow/linear/")
    stored = await service.get_session(session.id)
    assert "entries" not in stored.metadata["memory_context"]


@pytest.mark.asyncio
async def test_executor_prompt_requires_memory_policy_opt_in() -> None:
    store = InMemoryMemoryStore()
    await store.append(MemoryScope.GLOBAL, "global", "episode.any", {"lesson": "x"})
    prompts = PromptLibrary(
        {
            "planner": PromptTemplate(
                role="planner",
                system="planner",
                user_template="{memory_context}",
            ),
            "writer": PromptTemplate(role="writer", system="writer", user_template="{input}"),
        }
    )
    backend = MockProvider()
    agent, _service = _wire(
        provider=backend,
        prompts=prompts,
        memory_retriever=StoreMemoryContextRetriever(store),
    )

    session = await agent.start_session(input_payload={"topic": "X"})
    await agent.run_session(session.id)

    assert json.loads(backend.invocations[0]["messages"][0].content) == {}


@pytest.mark.asyncio
async def test_missing_prompt_uses_builtin_prompt_fallback() -> None:
    backend = MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')])
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=backend,
        control=plane,
        workflow=WorkflowBlueprint(
            name="fallback",
            nodes=(WorkflowNodeSpec(key="inspect", role="reviewer"),),
        ),
    )

    session = await agent.start_session(input_payload={"topic": "X"})
    await agent.run_session(session.id)

    first_call = backend.invocations[0]
    assert first_call["system"] == "You are the reviewer for node 'inspect' in workflow 'fallback'."
    assert first_call["messages"][0].content == '{"topic": "X"}'


@pytest.mark.asyncio
async def test_unresolved_tool_calls_fail_session() -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(ToolCall(id="t1", name="search", arguments={"q": "x"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"final": true}'),
        ]
    )
    agent, service = _wire(provider=backend)
    session = await agent.start_session()
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.FAIL
    assert "unresolved tool calls: search" in (decision.error or "")
    s = await service.get_session(session.id)
    assert s.status is SessionStatus.FAILED
    assert "unresolved tool calls: search" in (s.error_message or "")


@pytest.mark.asyncio
async def test_tool_runner_executes_calls_and_feeds_results_back(tmp_path) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_json",
                        arguments={"path": "node/output.json", "data": {"ok": True}},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
            ScriptedTurn(content='{"text": "writer done"}'),
        ]
    )
    tools = build_default_tool_registry(context=ToolContext(artifact_root=tmp_path, dry_run=True))
    service = SessionService(InMemorySessionStore())
    plane = ControlPlane(service)
    agent = OrchestratorAgent(
        provider=backend,
        control=plane,
        workflow=_linear_blueprint(),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.start_session()
    await agent.run_session(session.id)

    assert (tmp_path / "node" / "output.json").exists()
    assert len(backend.invocations) == 3
    tool_message = backend.invocations[1]["messages"][-1]
    assert tool_message.tool_results
    s = await service.get_session(session.id)
    assert s.nodes["plan"].attempts[-1].output == {"used_tool": True}
    ledger = s.nodes["plan"].attempts[-1].tool_executions
    assert len(ledger) == 1
    assert ledger[0].tool_call_id == "tool-1"
    assert ledger[0].tool_name == "artifact.write_json"
    assert ledger[0].status is ToolExecutionStatus.SUCCEEDED
    assert ledger[0].result_preview["content_json"] is True
    assert ledger[0].content_length is not None
    assert ledger[0].metadata["risk_class"] == "write_artifact"
    assert ledger[0].metadata["side_effect_class"] == "artifact_write"
    assert ledger[0].metadata["effect_fingerprint"].startswith("sha256:")


@pytest.mark.asyncio
async def test_tool_drain_reuses_duplicate_successful_result_in_same_execution(
    tmp_path,
) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "node/out.txt", "content": "ok"},
                    ),
                    ToolCall(
                        id="tool-2",
                        name="artifact.write_text",
                        arguments={"path": "node/out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
        ]
    )
    call_count = 0

    async def write_text(args, context):
        nonlocal call_count
        del context
        call_count += 1
        return ToolExecutionResult.ok({"path": args["path"], "value": call_count})

    tools = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=False))
    tools.register_function(
        name="artifact.write_text",
        description="write text",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=write_text,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    observer = ListObserver()
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="reuse-flow",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        ),
        tool_provider=tools,
        tool_runner=tools,
        emitter=Emitter([observer]),
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert call_count == 1
    tool_message = backend.invocations[1]["messages"][-1]
    assert [result.tool_call_id for result in tool_message.tool_results] == [
        "tool-1",
        "tool-2",
    ]
    assert tool_message.tool_results[0].content == tool_message.tool_results[1].content

    stored = await service.get_session(session.id)
    ledger = stored.nodes["work"].latest_attempt().tool_executions
    assert len(ledger) == 2
    first, second = ledger
    assert first.status is ToolExecutionStatus.SUCCEEDED
    assert second.status is ToolExecutionStatus.SUCCEEDED
    assert "reused_result" not in first.metadata
    assert second.metadata["reused_result"] is True
    assert second.metadata["source_tool_call_id"] == "tool-1"
    assert second.metadata["source_effect_fingerprint"] == first.metadata["effect_fingerprint"]
    assert second.metadata["effect_fingerprint"] == first.metadata["effect_fingerprint"]

    completed = [
        event for event in observer.events if event.kind is ObservationEventKind.TOOL_CALL_COMPLETED
    ]
    reused_event = next(event for event in completed if event.detail["tool_call_id"] == "tool-2")
    assert reused_event.detail["execution_metadata"]["reused_result"] is True
    assert reused_event.detail["execution_metadata"]["source_tool_call_id"] == "tool-1"


@pytest.mark.asyncio
async def test_tool_drain_does_not_reuse_distinct_fingerprints(tmp_path) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "node/a.txt", "content": "one"},
                    ),
                    ToolCall(
                        id="tool-2",
                        name="artifact.write_text",
                        arguments={"path": "node/b.txt", "content": "two"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
        ]
    )
    seen_paths: list[str] = []

    async def write_text(args, context):
        del context
        seen_paths.append(str(args["path"]))
        return ToolExecutionResult.ok({"path": args["path"], "count": len(seen_paths)})

    tools = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=False))
    tools.register_function(
        name="artifact.write_text",
        description="write text",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=write_text,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="distinct-flow",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        ),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert seen_paths == ["node/a.txt", "node/b.txt"]
    stored = await service.get_session(session.id)
    ledger = stored.nodes["work"].latest_attempt().tool_executions
    assert len(ledger) == 2
    assert ledger[0].metadata["effect_fingerprint"] != ledger[1].metadata["effect_fingerprint"]
    assert all("reused_result" not in item.metadata for item in ledger)


@pytest.mark.asyncio
async def test_tool_drain_does_not_reuse_failed_result(tmp_path) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "node/out.txt", "content": "ok"},
                    ),
                    ToolCall(
                        id="tool-2",
                        name="artifact.write_text",
                        arguments={"path": "node/out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
        ]
    )
    call_count = 0

    async def flaky_write(args, context):
        nonlocal call_count
        del args, context
        call_count += 1
        if call_count == 1:
            return ToolExecutionResult.fail("temporary failure")
        return ToolExecutionResult.ok({"value": call_count})

    tools = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=False))
    tools.register_function(
        name="artifact.write_text",
        description="write text",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=flaky_write,
        policy=ToolPolicy(risk_class=ToolRiskClass.WRITE_ARTIFACT),
    )
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="failed-reuse-flow",
            nodes=(WorkflowNodeSpec(key="work", role="writer"),),
        ),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert call_count == 2
    stored = await service.get_session(session.id)
    ledger = stored.nodes["work"].latest_attempt().tool_executions
    assert [item.status for item in ledger] == [
        ToolExecutionStatus.FAILED,
        ToolExecutionStatus.SUCCEEDED,
    ]
    assert all("reused_result" not in item.metadata for item in ledger)


@pytest.mark.asyncio
async def test_tool_result_trace_is_redacted_without_changing_model_input(tmp_path) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-secret",
                        name="secret.tool",
                        arguments={"api_key": "input-secret", "topic": "x"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
            ScriptedTurn(content='{"text": "writer done"}'),
        ]
    )

    async def sensitive_tool(args, context):
        del args, context
        return ToolExecutionResult.ok(
            {
                "url": "https://example.com/artifact.png?signature=live-secret",
                "blob": "A" * 120,
            },
            metadata={"access_token": "live-token", "provider": "test"},
        )

    tools = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=True))
    tools.register_function(
        name="secret.tool",
        description="returns sensitive payload",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=sensitive_tool,
    )
    observer = ListObserver()
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=_linear_blueprint(),
        tool_provider=tools,
        tool_runner=tools,
        emitter=Emitter([observer]),
    )

    session = await agent.start_session()
    await agent.run_session(session.id)

    tool_message = backend.invocations[1]["messages"][-1]
    raw_content = tool_message.tool_results[0].content
    assert "live-secret" in raw_content
    assert "live-token" in raw_content
    assert "A" * 120 in raw_content

    started = next(
        event for event in observer.events if event.kind is ObservationEventKind.TOOL_CALL_STARTED
    )
    assert started.detail["arguments"]["api_key"] == "<redacted>"

    completed = next(
        event for event in observer.events if event.kind is ObservationEventKind.TOOL_CALL_COMPLETED
    )
    assert completed.detail["content_json"] is True
    assert completed.detail["content_redacted"] is True
    assert "live-secret" not in completed.detail["content_preview"]
    assert "live-token" not in completed.detail["content_preview"]
    assert "A" * 96 not in completed.detail["content_preview"]
    assert "<redacted-url>" in completed.detail["content_preview"]
    assert "<redacted-payload>" in completed.detail["content_preview"]
    assert completed.detail["content_keys"] == [
        "error",
        "metadata",
        "result",
        "success",
        "tool",
    ]
    stored = await service.get_session(session.id)
    ledger = stored.nodes["plan"].latest_attempt().tool_executions
    assert ledger[0].arguments["api_key"] == "<redacted>"
    assert "live-secret" not in ledger[0].result_preview["content_preview"]
    assert "live-token" not in ledger[0].result_preview["content_preview"]


@pytest.mark.asyncio
async def test_tool_runner_records_failed_tool_execution_in_ledger(tmp_path) -> None:
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-fail",
                        name="failing.tool",
                        arguments={"api_key": "input-secret"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"handled": true}'),
            ScriptedTurn(content='{"text": "writer done"}'),
        ]
    )

    async def failing_tool(args, context):
        del args, context
        return ToolExecutionResult.fail(
            "provider failed",
            metadata={"access_token": "live-token"},
        )

    tools = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=True))
    tools.register_function(
        name="failing.tool",
        description="returns an error",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        handler=failing_tool,
    )
    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=_linear_blueprint(),
        tool_provider=tools,
        tool_runner=tools,
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)
    stored = await service.get_session(session.id)
    ledger = stored.nodes["plan"].latest_attempt().tool_executions

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert ledger[0].status is ToolExecutionStatus.FAILED
    assert ledger[0].is_error is True
    assert ledger[0].arguments["api_key"] == "<redacted>"
    assert "live-token" not in ledger[0].result_preview["content_preview"]


@pytest.mark.asyncio
async def test_max_steps_guard_raises() -> None:
    backend = MockProvider(scripted=[ScriptedTurn(content="{}")])
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(SessionService(InMemorySessionStore())),
        workflow=_linear_blueprint(),
        max_steps=0,
    )
    session = await agent.start_session()
    with pytest.raises(RuntimeError, match="max_steps"):
        await agent.run_session(session.id)


def test_workflow_graph_accessible() -> None:
    agent, _ = _wire()
    assert isinstance(agent.graph, WorkflowGraph)
    assert agent.workflow.name == "linear"


@pytest.mark.asyncio
async def test_unknown_execution_mode_fails_closed() -> None:
    """A removed/unknown execution_mode (e.g. the retired subagent_group) fails closed.

    Type-directed dispatch parses node config into NodeExecutionMode at the
    boundary; an unknown value raises ValueError rather than silently degrading
    or routing to a deleted path (DEV-09/10).
    """

    service = SessionService(InMemorySessionStore())
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="bogus-mode-flow",
            nodes=(
                WorkflowNodeSpec(
                    key="bogus",
                    role="executor",
                    config={"execution_mode": "subagent_group"},
                ),
            ),
        ),
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)
    stored = await service.get_session(session.id)

    assert decision.kind is RuntimeDecisionKind.FAIL
    assert "unknown node execution_mode 'subagent_group'" in (decision.error or "")
    assert stored.status is SessionStatus.FAILED


def test_resolve_node_execution_mode_routes_typed() -> None:
    """Typed dispatch parses config to the right NodeExecutionMode (DEV-09)."""
    from hydramind.orchestration import NodeExecutionMode
    from hydramind.orchestration.planning_contracts import resolve_node_execution_mode

    assert resolve_node_execution_mode({}) is NodeExecutionMode.DIRECT
    assert (
        resolve_node_execution_mode({"execution_mode": "direct"}) is NodeExecutionMode.DIRECT
    )
    assert (
        resolve_node_execution_mode({"execution_mode": "subagent"})
        is NodeExecutionMode.SUBAGENT
    )
    assert resolve_node_execution_mode({"execution_mode": "team"}) is NodeExecutionMode.TEAM
    # mas_team presence wins regardless of execution_mode string.
    assert (
        resolve_node_execution_mode({"mas_team": {}, "execution_mode": "direct"})
        is NodeExecutionMode.TEAM
    )
    for bad in ("subagent_group", "bogus", "DIRECT"):
        with pytest.raises(ValueError, match="unknown node execution_mode"):
            resolve_node_execution_mode({"execution_mode": bad})


@pytest.mark.asyncio
async def test_subagent_tool_call_records_child_origin(tmp_path) -> None:
    service = SessionService(InMemorySessionStore())
    observer = ListObserver()
    tools = build_default_tool_registry(context=ToolContext(artifact_root=tmp_path, dry_run=True))
    provider = MockProvider(
        scripted=[
            ScriptedTurn(
                tool_calls=(
                    ToolCall(
                        id="child-tool-1",
                        name="artifact.write_text",
                        arguments={"path": "child/out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"subagent_tool_done": true}'),
        ]
    )
    agent = OrchestratorAgent(
        provider=provider,
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(
            name="subagent-tool-flow",
            nodes=(
                WorkflowNodeSpec(
                    key="delegate",
                    role="tool_child",
                    config={"execution_mode": "subagent"},
                ),
            ),
        ),
        tool_provider=tools,
        tool_runner=tools,
        emitter=Emitter([observer]),
    )

    session = await agent.start_session(input_payload={"task": "write child output"})
    decision = await agent.run_session(session.id)
    stored = await service.get_session(session.id)
    ledger = stored.nodes["delegate"].latest_attempt().tool_executions
    started = next(
        event for event in observer.events if event.kind is ObservationEventKind.TOOL_CALL_STARTED
    )
    completed = next(
        event for event in observer.events if event.kind is ObservationEventKind.TOOL_CALL_COMPLETED
    )
    model_started = next(
        event
        for event in observer.events
        if event.kind is ObservationEventKind.MODEL_INVOKE_STARTED
        and event.detail.get("execution_mode") == "subagent"
    )
    model_completed = next(
        event
        for event in observer.events
        if event.kind is ObservationEventKind.MODEL_INVOKE_COMPLETED
        and event.detail.get("execution_mode") == "subagent"
    )

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert (tmp_path / "child" / "out.txt").read_text(encoding="utf-8") == "ok"
    assert ledger[0].tool_name == "artifact.write_text"
    assert ledger[0].status is ToolExecutionStatus.SUCCEEDED
    assert {
        key: ledger[0].metadata[key] for key in ("execution_mode", "subagent_id", "subagent_role")
    } == {
        "execution_mode": "subagent",
            "subagent_id": "provider-sub-tool_child",
        "subagent_role": "tool_child",
    }
    assert ledger[0].metadata["risk_class"] == "write_artifact"
    assert ledger[0].metadata["effect_fingerprint"].startswith("sha256:")
    assert started.detail["origin"]["subagent_id"] == "provider-sub-tool_child"
    assert completed.detail["origin"]["subagent_id"] == "provider-sub-tool_child"
    assert started.detail["execution_metadata"] == ledger[0].metadata
    assert completed.detail["execution_metadata"] == ledger[0].metadata
    assert model_started.detail["subagent_id"] == "provider-sub-tool_child"
    assert model_completed.detail["subagent_id"] == "provider-sub-tool_child"


@pytest.mark.asyncio
async def test_native_team_spec_runs_through_team_execution_bridge() -> None:
    service = SessionService(InMemorySessionStore())
    observer = ListObserver()
    team = TeamSpec(
        id="review-team",
        members=(
            AgentSpec(id="writer", role="writer"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol=CollaborationProtocol(coordinator_id="reviewer"),
        workspace=SharedWorkspace(id="shared-draft"),
    )
    node = PlanTaskSpec(
        key="collaborate",
        role="coordinator",
        team=team,
    ).to_workflow_node()
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(service),
        workflow=WorkflowBlueprint(name="native-team-flow", nodes=(node,)),
        emitter=Emitter([observer]),
    )

    session = await agent.start_session(input_payload={"topic": "native MAS"})
    decision = await agent.run_session(session.id)
    stored = await service.get_session(session.id)
    output = stored.nodes["collaborate"].latest_attempt().output
    collaboration = output["collaboration"]
    started = next(
        event
        for event in observer.events
        if event.kind is ObservationEventKind.MODEL_INVOKE_STARTED
        and event.detail.get("execution_mode") == "team"
    )
    completed = next(
        event
        for event in observer.events
        if event.kind is ObservationEventKind.MODEL_INVOKE_COMPLETED
        and event.detail.get("execution_mode") == "team"
    )

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert collaboration["mode"] == "team"
    assert collaboration["team_id"] == "review-team"
    assert collaboration["workspace"]["id"] == "shared-draft"
    assert [item["agent_id"] for item in collaboration["results"]] == [
        "writer",
        "reviewer",
    ]
    assert all(
        item["subagent_id"].startswith("provider-sub-")
        for item in collaboration["results"]
    )
    assert started.detail["team_id"] == "review-team"
    assert started.detail["protocol"]["coordinator_id"] == "reviewer"
    assert completed.detail["workspace_id"] == "shared-draft"

    interaction_log = stored.metadata["interaction_log"]["entries"]
    assert [entry["event_kind"] for entry in interaction_log] == [
        InteractionLogEventKind.INTERACTION_STARTED.value,
        InteractionLogEventKind.TURN_STARTED.value,
        InteractionLogEventKind.TURN_COMPLETED.value,
        InteractionLogEventKind.MESSAGE_SENT.value,
        InteractionLogEventKind.TURN_STARTED.value,
        InteractionLogEventKind.TURN_COMPLETED.value,
        InteractionLogEventKind.MESSAGE_SENT.value,
        InteractionLogEventKind.INTERACTION_COMPLETED.value,
    ]
    assert {entry["interaction_id"] for entry in interaction_log} == {
        f"interaction-{stored.id}-collaborate"
    }
    assert {entry["team_id"] for entry in interaction_log} == {"review-team"}
    assert {entry["workspace_id"] for entry in interaction_log} == {"shared-draft"}
    assert [entry["actor"] for entry in interaction_log if entry["actor"]] == [
        "writer",
        "writer",
        "writer",
        "reviewer",
        "reviewer",
        "reviewer",
    ]


@pytest.mark.asyncio
async def test_run_returns_complete_when_already_done() -> None:
    backend = MockProvider(scripted=[ScriptedTurn(content="{}"), ScriptedTurn(content="{}")])
    agent, _service = _wire(provider=backend)
    session = await agent.start_session()
    first = await agent.run_session(session.id)
    assert first.kind is RuntimeDecisionKind.COMPLETE
    again = await agent.run_session(session.id)
    assert again.kind is RuntimeDecisionKind.COMPLETE


@pytest.mark.asyncio
async def test_subagent_path_stamps_speaker_name_and_threads_context() -> None:
    provider = MockProvider(scripted=[ScriptedTurn(content='{"captured": true}')])
    agent = OrchestratorAgent(
        provider=provider,
        control=ControlPlane(SessionService(InMemorySessionStore())),
        workflow=WorkflowBlueprint(
            name="speaker-name-flow",
            nodes=(
                WorkflowNodeSpec(
                    key="delegate",
                    role="reviewer",
                    config={"execution_mode": "subagent"},
                ),
            ),
        ),
    )
    session = await agent.start_session(input_payload={"task": "review"})
    await agent.run_session(session.id)

    invocation = provider.invocations[-1]
    messages = invocation["messages"]
    # DEV-33: the producer stamps the acting agent's role as Message.name on the
    # turn it sends, so multi-speaker transcripts carry agent identity.
    assert messages[-1].name == "reviewer"


class _TurnLeaseRecoverySpyControl(ControlPlane):
    """ControlPlane that counts how often the loop recovers expired turn leases.

    S5b/S7b: proves the live scheduler loop invokes ``recover_expired_turn_leases``
    on each scheduling pass alongside node-execution recovery, without any live
    LLM or real lease expiry.
    """

    def __init__(self, service: SessionService) -> None:
        super().__init__(service)
        self.turn_recovery_calls = 0

    async def recover_expired_turn_leases(self, session_id: str):  # type: ignore[override]
        self.turn_recovery_calls += 1
        return await super().recover_expired_turn_leases(session_id)


@pytest.mark.asyncio
async def test_run_session_loop_invokes_expired_turn_lease_recovery() -> None:
    # S7b GOAL 1: every scheduling pass calls turn-lease recovery so a crashed
    # mid-interaction turn becomes resumable in the LIVE loop (not only via a
    # direct API/test call). Deterministic + offline (MockProvider, spy control).
    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "intro,body,conclusion"}'),
            ScriptedTurn(content='{"text": "full draft"}'),
        ]
    )
    service = SessionService(InMemorySessionStore())
    plane = _TurnLeaseRecoverySpyControl(service)
    agent = OrchestratorAgent(
        provider=backend,
        control=plane,
        workflow=_linear_blueprint(),
    )

    session = await agent.start_session(input_payload={"topic": "abstractions"})
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    # Two ready-node passes drive the two-node DAG; turn-lease recovery runs on
    # each pass (at least once per node executed).
    assert plane.turn_recovery_calls >= 2


@pytest.mark.asyncio
async def test_expired_turn_lease_becomes_resumable_through_run_session() -> None:
    # S7b GOAL 1 (end-to-end): an interaction with an EXPIRED durable turn lease
    # is recovered (turn reverted to PENDING + interaction marked resumable) by a
    # normal ``run_session`` pass — proving the loop, not just the API, recovers.
    from datetime import UTC, datetime, timedelta

    from hydramind.control import durable_interaction_id
    from hydramind.kernel.contracts import TurnStatus

    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"outline": "ok"}'),
            ScriptedTurn(content='{"text": "draft"}'),
        ]
    )
    store = InMemorySessionStore()
    service = SessionService(store)
    plane = ControlPlane(service)
    agent = OrchestratorAgent(provider=backend, control=plane, workflow=_linear_blueprint())

    session = await agent.start_session(input_payload={"topic": "X"})
    interaction_id = durable_interaction_id(session.id, "plan")
    await plane.start_interaction(
        session.id,
        interaction_id=interaction_id,
        node_key="plan",
        execution_id="exec-pre",
        team_id="team",
        protocol_mode="team",
        topology="pipeline",
        member_ids=("writer", "reviewer"),
    )
    # A turn leased in a prior (crashed) attempt.
    granted = await plane.grant_turn_lease(
        session.id,
        interaction_id=interaction_id,
        turn_index=0,
        agent_id="writer",
        owner="dead-worker",
        ttl_seconds=300,
    )
    leased = granted.turn_by_index(0)
    assert leased is not None and leased.turn_lease_expires_at is not None

    # Rewind the stored lease expiry into the past (simulating a crashed worker
    # whose lease has since expired) via the store, so the loop's as_of=now()
    # recovery on the next scheduling pass reclaims it. A get/put round-trip
    # preserves the optimistic-version contract.
    stored = await store.get(session.id)
    assert stored is not None
    interaction_pre = stored.durable_interactions[interaction_id]
    past = datetime.now(UTC) - timedelta(seconds=60)
    rewound_turns = tuple(
        t.model_copy(update={"turn_lease_expires_at": past}) if t.turn_index == 0 else t
        for t in interaction_pre.turns
    )
    stored.durable_interactions[interaction_id] = interaction_pre.model_copy(
        update={"turns": rewound_turns}
    )
    await store.put(stored)

    await agent.run_session(session.id)

    interaction = await plane.get_durable_interaction(session.id, interaction_id)
    assert interaction is not None
    reverted = interaction.turn_by_index(0)
    assert reverted is not None
    assert reverted.status is TurnStatus.PENDING
    assert reverted.turn_lease_token is None
    assert interaction.recovered_from_turn_index == 0
