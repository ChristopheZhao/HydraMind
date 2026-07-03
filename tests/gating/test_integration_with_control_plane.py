"""End-to-end: GateRegistry plugged into ControlPlane drives real transitions."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from hydramind.control import (
    AgentReport,
    ControlPlane,
    DecisionAction,
    GateDecisionInput,
    InMemorySessionStore,
    NodeStatus,
    RuntimeDecisionKind,
    SessionService,
    SessionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.control.models import Gate
from hydramind.control.states import GateOutcome
from hydramind.gating import (
    GateContract,
    GateRegistry,
    HumanInLoopEvaluator,
    SchemaCheckEvaluator,
)


class _Required(BaseModel):
    title: str


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="duo",
        nodes=(
            WorkflowNodeSpec(key="draft", role="writer"),
            WorkflowNodeSpec(key="publish", role="publisher", requires=("draft",)),
        ),
    )


@pytest.mark.asyncio
async def test_schema_check_passes_then_human_review_on_publish() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())

    registry = GateRegistry()
    registry.register(
        SchemaCheckEvaluator(GateContract(name="schema"), _Required),
    )
    registry.register(
        HumanInLoopEvaluator(
            GateContract(
                name="final_review",
                description="needs human ok",
                applies_to_nodes=("publish",),
            )
        ),
    )

    plane = ControlPlane(service, gate_fn=registry.to_gate_fn())

    draft = await plane.open_runtime_decision(
        session.id,
        AgentReport(node_key="draft", agent_id="writer", output={"title": "valid draft"}),
    )
    assert draft.kind is RuntimeDecisionKind.CONTINUE

    publish = await plane.open_runtime_decision(
        session.id,
        AgentReport(node_key="publish", agent_id="publisher", output={"title": "to publish"}),
    )
    assert publish.kind is RuntimeDecisionKind.AWAIT_GATE
    assert publish.gate is not None
    assert publish.gate.name == "final_review"

    s = await service.get_session(session.id)
    assert s.status is SessionStatus.WAITING_GATE

    approval = await plane.apply_decision(
        session.id,
        GateDecisionInput(
            gate_id=publish.gate.id,
            action=DecisionAction.APPROVE,
            actor="reviewer-1",
        ),
    )
    # Approving the final gate via apply_decision RECORDS node completion only;
    # the session stays RUNNING (decision authority for completion is the
    # orchestrator, ADR-0008). The orchestrator records completion explicitly.
    assert approval.kind is RuntimeDecisionKind.CONTINUE
    final = await service.get_session(session.id)
    assert final.status is SessionStatus.RUNNING
    assert final.nodes["publish"].status is NodeStatus.COMPLETED

    recorded = await plane.record_session_complete(session.id)
    assert recorded.kind is RuntimeDecisionKind.COMPLETE
    final = await service.get_session(session.id)
    assert final.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_schema_failure_requires_decision_then_revise() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_blueprint())

    registry = GateRegistry()
    registry.register(SchemaCheckEvaluator(GateContract(name="schema"), _Required))

    plane = ControlPlane(service, gate_fn=registry.to_gate_fn())

    bad = await plane.open_runtime_decision(
        session.id,
        AgentReport(node_key="draft", agent_id="writer", output={}),  # missing title
    )
    assert bad.kind is RuntimeDecisionKind.AWAIT_GATE
    assert bad.gate is not None
    assert "errors" in bad.gate.detail

    revised = await plane.apply_decision(
        session.id,
        GateDecisionInput(gate_id=bad.gate.id, action=DecisionAction.REVISE),
    )
    assert revised.kind is RuntimeDecisionKind.CONTINUE

    s = await service.get_session(session.id)
    assert s.nodes["draft"].status is NodeStatus.QUEUED  # ready for retry
    assert s.status is SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_coordinator_arbitration_is_gate_authorized() -> None:
    # ADR-0008/0004: the coordinator AGENT decides the summary; the gating layer
    # AUTHORIZES the transition. A gate inspecting the coordinator arbitration
    # result can BLOCK its acceptance at the node boundary (reusing the existing
    # GateResult seam — no bespoke arbitration engine).
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(
        WorkflowBlueprint(
            name="team",
            nodes=(WorkflowNodeSpec(key="collaborate", role="coordinator"),),
        )
    )

    async def gate_fn(s: object, node: object, report: AgentReport) -> Gate | None:
        aggregation = (report.output or {}).get("mas_team", {}).get("aggregation", {})
        if aggregation.get("strategy") == "coordinator_summary" and not aggregation.get(
            "summary"
        ):
            return Gate(
                name="empty_coordinator_summary",
                node_key=node.key,  # type: ignore[attr-defined]
                outcome=GateOutcome.BLOCK,
            )
        return None

    plane = ControlPlane(service, gate_fn=gate_fn)

    # An empty coordinator summary is BLOCKed (arbitration not authorized).
    blocked = await plane.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="collaborate",
            agent_id="coordinator",
            output={
                "mas_team": {
                    "aggregation": {"strategy": "coordinator_summary", "summary": ""}
                }
            },
        ),
    )
    assert blocked.kind is RuntimeDecisionKind.FAIL


@pytest.mark.asyncio
async def test_coordinator_arbitration_passes_gate_with_summary() -> None:
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(
        WorkflowBlueprint(
            name="team",
            nodes=(WorkflowNodeSpec(key="collaborate", role="coordinator"),),
        )
    )

    async def gate_fn(s: object, node: object, report: AgentReport) -> Gate | None:
        aggregation = (report.output or {}).get("mas_team", {}).get("aggregation", {})
        if aggregation.get("strategy") == "coordinator_summary" and not aggregation.get(
            "summary"
        ):
            return Gate(
                name="empty_coordinator_summary",
                node_key=node.key,  # type: ignore[attr-defined]
                outcome=GateOutcome.BLOCK,
            )
        return None

    plane = ControlPlane(service, gate_fn=gate_fn)
    ok = await plane.open_runtime_decision(
        session.id,
        AgentReport(
            node_key="collaborate",
            agent_id="coordinator",
            output={
                "mas_team": {
                    "aggregation": {
                        "strategy": "coordinator_summary",
                        "summary": "final",
                    }
                }
            },
        ),
    )
    assert ok.kind is RuntimeDecisionKind.CONTINUE
