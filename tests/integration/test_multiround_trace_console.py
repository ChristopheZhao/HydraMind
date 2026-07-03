"""Multi-round framework runs with trajectory and console evidence."""

from __future__ import annotations

import pytest
from examples.agent_run_console.render import build_run_state, write_console

from hydramind.control import (
    AgentReport,
    ControlPlane,
    DecisionAction,
    Gate,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    NodeState,
    RuntimeDecisionKind,
    RuntimeSession,
    SessionService,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.harness.base import StopReason, ToolCall
from hydramind.observability import Emitter, ListObserver
from hydramind.orchestration import OrchestratorAgent
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import ToolContext, build_default_tool_registry


def _single_node(role: str = "executor") -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="multiround",
        nodes=(WorkflowNodeSpec(key="work", role=role),),
    )


@pytest.mark.asyncio
async def test_multiround_tool_drain_trace_and_console(tmp_path) -> None:
    observer = ListObserver()
    emitter = Emitter([observer])
    service = SessionService(InMemorySessionStore(), emitter=emitter)
    tools = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_json",
                        arguments={"path": "case/output.json", "data": {"ok": True}},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-2",
                        name="artifact.exists",
                        arguments={"path": "case/output.json"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"final": "tool loop done"}'),
        ]
    )
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service),
        workflow=_single_node(),
        tool_provider=tools,
        tool_runner=tools,
        emitter=emitter,
    )

    session = await agent.start_session()
    decision = await agent.run_session(session.id)

    assert decision.kind is RuntimeDecisionKind.COMPLETE
    assert (tmp_path / "case" / "output.json").exists()
    kinds = observer.kinds()
    assert kinds.count("tool_drain_round") == 2
    assert kinds.count("tool_call_started") == 2
    assert kinds.count("model_invoke_completed") == 3
    execution_ids = {event.execution_id for event in observer.events if event.execution_id}
    assert len(execution_ids) == 1

    html_path = write_console(observer.events, tmp_path / "console" / "tool.html")
    html = html_path.read_text(encoding="utf-8")
    assert "HydraMind Run Console" in html
    assert "tool_call_started" in html
    assert build_run_state(observer.events)["status"] == "completed"


@pytest.mark.asyncio
async def test_multiround_gate_revision_trace_and_console(tmp_path) -> None:
    observer = ListObserver()
    emitter = Emitter([observer])
    service = SessionService(InMemorySessionStore(), emitter=emitter)

    async def gate_once(
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        if not node.gates:
            return Gate(
                name="revision_review",
                node_key=node.key,
                outcome=GateOutcome.REQUIRES_DECISION,
            )
        return None

    backend = MockProvider(
        scripted=[
            ScriptedTurn(content='{"draft": "v1"}'),
            ScriptedTurn(content='{"draft": "v2"}'),
        ]
    )
    agent = OrchestratorAgent(
        provider=backend,
        control=ControlPlane(service, gate_fn=gate_once),
        workflow=_single_node(role="writer"),
        emitter=emitter,
    )

    session = await agent.start_session()
    halted = await agent.run_session(session.id)
    assert halted.kind is RuntimeDecisionKind.AWAIT_GATE
    assert halted.gate is not None

    await agent.resume_session(
        session.id,
        GateDecisionInput(
            gate_id=halted.gate.id,
            action=DecisionAction.REVISE,
            rationale="needs another pass",
        ),
    )
    final = await agent.run_session(session.id)

    assert final.kind is RuntimeDecisionKind.COMPLETE
    kinds = observer.kinds()
    assert kinds.count("node_execution_started") == 2
    assert kinds.count("model_invoke_completed") == 2
    assert "decision_applied" in kinds
    execution_ids = {event.execution_id for event in observer.events if event.execution_id}
    assert len(execution_ids) == 2

    html_path = write_console(observer.events, tmp_path / "console" / "revision.html")
    html = html_path.read_text(encoding="utf-8")
    assert "revision_review" in html
    assert "node_execution_started" in html
