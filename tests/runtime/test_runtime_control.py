"""Runtime control-plane assembly tests."""

from __future__ import annotations

import pytest

from hydramind.control import (
    AgentReport,
    InMemorySessionStore,
    RuntimeDecisionKind,
    SessionStatus,
    VerifierResult,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.runtime_control import (
    build_goal_control_runtime,
    build_workflow_control_runtime,
)


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="control",
        nodes=(WorkflowNodeSpec(key="write", role="writer"),),
    )


@pytest.mark.asyncio
async def test_goal_control_runtime_uses_injected_store_and_verifier_gate() -> None:
    store = InMemorySessionStore()
    runtime = build_goal_control_runtime(
        session_store=store,
        session_store_kind="memory",
        store_path=None,
        emitter=None,
    )
    session = await runtime.service.create_session(_blueprint())

    decision = await runtime.control.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="write",
            agent_id="writer",
            output={"draft": "v1"},
            verifier_results=(VerifierResult(name="quality", passed=False, score=0.1),),
        ),
    )
    stored = await runtime.service.get_session(session.id)

    assert runtime.store is store
    assert runtime.control.service is runtime.service
    assert decision.kind is RuntimeDecisionKind.AWAIT_GATE
    assert stored.status is SessionStatus.WAITING_GATE
    assert stored.nodes["write"].latest_gate().name == "verifier_feedback"


@pytest.mark.asyncio
async def test_workflow_control_runtime_loads_workflow_gates_py(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: control
nodes:
  - key: write
    role: writer
""",
        encoding="utf-8",
    )
    (tmp_path / "gates.py").write_text(
        """
from hydramind.gating import GateContract, GateRegistry, HumanInLoopEvaluator


def build_gate_registry():
    return GateRegistry([
        HumanInLoopEvaluator(
            GateContract(
                name="workflow_review",
                description="workflow review required",
                applies_to_nodes=("write",),
            )
        )
    ])
""",
        encoding="utf-8",
    )
    runtime = build_workflow_control_runtime(
        workflow_path=workflow,
        session_store=None,
        session_store_kind="memory",
        store_path=None,
        emitter=None,
    )
    session = await runtime.service.create_session(_blueprint())

    decision = await runtime.control.open_runtime_decision(
        session.id,
        AgentReport(node_key="write", agent_id="writer", output={"draft": "v1"}),
    )

    assert decision.kind is RuntimeDecisionKind.AWAIT_GATE
    assert decision.gate is not None
    assert decision.gate.name == "workflow_review"
    assert decision.gate.detail["prompt"] == "workflow review required"


@pytest.mark.asyncio
async def test_workflow_control_runtime_without_gates_keeps_control_path_open(
    tmp_path,
) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: control
nodes:
  - key: write
    role: writer
""",
        encoding="utf-8",
    )
    runtime = build_workflow_control_runtime(
        workflow_path=workflow,
        session_store=InMemorySessionStore(),
        session_store_kind="memory",
        store_path=None,
        emitter=None,
    )
    session = await runtime.service.create_session(_blueprint())

    decision = await runtime.control.open_runtime_decision(
        session.id,
        AgentReport(node_key="write", agent_id="writer", output={"draft": "v1"}),
    )

    assert decision.kind is RuntimeDecisionKind.CONTINUE
    assert runtime.control.service is runtime.service
