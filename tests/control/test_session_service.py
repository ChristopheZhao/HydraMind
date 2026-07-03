"""SessionService unit tests — single-writer state machine over InMemoryStore."""

from __future__ import annotations

import pytest

import hydramind.control as control_api
import hydramind.control.session_execution as session_execution_module
import hydramind.control.session_gates as session_gates_module
import hydramind.control.session_lifecycle as session_lifecycle_module
import hydramind.control.session_node_lifecycle as session_node_lifecycle_module
import hydramind.control.session_observability as session_observability_module
import hydramind.control.session_persistence as session_persistence_module
import hydramind.control.session_service as session_service_module
import hydramind.control.session_workflow as session_workflow_module
from hydramind.control import (
    AttemptStatus,
    ControlPlane,
    DecisionAction,
    ExecutionLeaseError,
    GateDecisionInput,
    GateOutcome,
    InMemorySessionStore,
    InteractionLogEventKind,
    InteractionLogRecord,
    NodeStatus,
    SessionService,
    SessionStatus,
    SessionStoreConflictError,
    SqliteSessionStore,
    ToolExecutionStatus,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    WorkflowRevision,
)
from hydramind.control.session_service import (
    InvalidTransitionError,
    NodeNotFoundError,
    SessionNotFoundError,
)


def test_session_execution_bookkeeping_boundary_is_internal() -> None:
    assert ExecutionLeaseError is session_execution_module.ExecutionLeaseError
    assert not hasattr(SessionService, "_find_attempt_by_id")
    assert not hasattr(SessionService, "_find_tool_execution")
    assert not hasattr(SessionService, "_assert_execution_lease")
    assert not hasattr(SessionService, "_clear_execution_lease")
    assert not hasattr(SessionService, "_clear_session_execution_leases")

    assert hasattr(session_execution_module, "record_tool_execution_started")
    assert hasattr(session_execution_module, "grant_execution_lease")
    assert "session_execution" not in control_api.__all__


def test_session_workflow_and_gate_boundaries_are_internal() -> None:
    assert session_service_module.GateNotFoundError is session_gates_module.GateNotFoundError
    assert not hasattr(SessionService, "_find_gate")
    assert not hasattr(SessionService, "_mark_node_stale_for_revision")
    assert not hasattr(SessionService, "_requeue_node_for_revision")
    assert not hasattr(session_service_module, "_validate_blueprint")
    assert not hasattr(session_service_module, "_descendant_keys")

    assert hasattr(session_workflow_module, "apply_workflow_revision")
    assert hasattr(session_workflow_module, "validate_blueprint")
    assert hasattr(session_gates_module, "record_gate")
    assert hasattr(session_gates_module, "apply_gate_decision")
    assert "session_workflow" not in control_api.__all__
    assert "session_gates" not in control_api.__all__


def test_target_status_for_decision_records_external_decision_deterministically() -> None:
    """``target_status_for_decision`` records the EXTERNAL decision authorization.

    It deterministically maps the human/system ``DecisionAction`` to a node
    status; control does not synthesize the decision itself (ADR-0008).
    """
    target_status = session_gates_module.target_status_for_decision
    assert target_status(DecisionAction.APPROVE) is NodeStatus.APPROVED
    assert target_status(DecisionAction.REVISE) is NodeStatus.NEEDS_REVISION
    assert target_status(DecisionAction.REPLAN) is NodeStatus.NEEDS_REVISION
    assert target_status(DecisionAction.REJECT) is NodeStatus.FAILED
    # Total over the external action surface: every DecisionAction is recorded.
    for action in DecisionAction:
        assert isinstance(target_status(action), NodeStatus)


def test_session_node_lifecycle_boundary_is_internal() -> None:
    assert hasattr(session_node_lifecycle_module, "complete_running_attempt")
    assert hasattr(session_node_lifecycle_module, "fail_running_attempt")
    assert hasattr(session_node_lifecycle_module, "abort_and_requeue_node")
    assert hasattr(
        session_node_lifecycle_module,
        "recover_expired_node_execution_leases",
    )
    assert "session_node_lifecycle" not in control_api.__all__


def test_session_lifecycle_boundary_is_internal() -> None:
    assert hasattr(session_lifecycle_module, "create_runtime_session")
    assert hasattr(session_lifecycle_module, "transition_session")
    assert hasattr(session_lifecycle_module, "update_session_metadata")
    assert hasattr(session_lifecycle_module, "add_workflow_nodes")
    assert not hasattr(session_lifecycle_module, "session_correlation")
    assert not hasattr(session_service_module, "_session_correlation")
    assert "session_lifecycle" not in control_api.__all__


def test_session_observability_boundary_is_internal() -> None:
    assert hasattr(session_observability_module, "SessionEventEmitter")
    assert hasattr(session_observability_module, "session_correlation")
    assert hasattr(session_observability_module, "node_correlation")
    assert hasattr(session_observability_module, "node_execution_completed_detail")
    assert hasattr(session_observability_module, "execution_lease_detail")
    assert hasattr(session_observability_module, "gate_decision_detail")
    assert not hasattr(SessionService, "_emit")
    assert not hasattr(SessionService, "_node_correlation")
    assert "session_observability" not in control_api.__all__


def test_session_interaction_log_boundary_is_internal() -> None:
    import hydramind.control.session_interactions as session_interactions_module

    assert hasattr(session_interactions_module, "append_interaction_log")
    assert "session_interactions" not in control_api.__all__


def test_session_persistence_boundary_is_internal() -> None:
    assert hasattr(session_persistence_module, "SessionRepository")
    assert not hasattr(SessionService, "_store")
    assert "session_persistence" not in control_api.__all__


def _blueprint() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="demo",
        nodes=(
            WorkflowNodeSpec(key="plan", role="planner"),
            WorkflowNodeSpec(key="write", role="writer", requires=("plan",)),
        ),
    )


@pytest.fixture
async def service() -> SessionService:
    return SessionService(InMemorySessionStore())


@pytest.mark.asyncio
async def test_create_session_seeds_all_nodes(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    assert session.status is SessionStatus.QUEUED
    assert set(session.nodes) == {"plan", "write"}
    assert all(n.status is NodeStatus.QUEUED for n in session.nodes.values())


@pytest.mark.asyncio
async def test_session_lifecycle_happy_path(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    sid = session.id
    await service.mark_session_running(sid)
    await service.start_node(sid, "plan")
    await service.start_attempt(sid, "plan")
    await service.complete_node(sid, "plan", output={"outline": "..."})
    s = await service.get_session(sid)
    assert s.nodes["plan"].status is NodeStatus.COMPLETED
    assert s.nodes["plan"].attempts[-1].status is AttemptStatus.SUCCEEDED
    assert s.nodes["plan"].attempts[-1].output == {"outline": "..."}


@pytest.mark.asyncio
async def test_invalid_session_transition_raises(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    with pytest.raises(InvalidTransitionError):
        await service.complete_session(session.id)  # not RUNNING yet


@pytest.mark.asyncio
async def test_unknown_session_raises(service: SessionService) -> None:
    with pytest.raises(SessionNotFoundError):
        await service.get_session("sess-does-not-exist")


@pytest.mark.asyncio
async def test_unknown_node_raises(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    with pytest.raises(NodeNotFoundError):
        await service.start_node(session.id, "nonexistent")


@pytest.mark.asyncio
async def test_gate_recorded_and_decision_revises(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "plan")
    await service.mark_node_pending_gate(session.id, "plan")
    gate = await service.record_gate(
        session.id, "plan", name="schema_check", outcome=GateOutcome.REQUIRES_DECISION
    )
    await service.apply_gate_decision(
        session.id,
        GateDecisionInput(gate_id=gate.id, action=DecisionAction.REVISE, actor="reviewer"),
    )
    node = await service.get_node(session.id, "plan")
    assert node.status is NodeStatus.NEEDS_REVISION
    assert node.gates[-1].decision is not None
    assert node.gates[-1].decision.action is DecisionAction.REVISE


@pytest.mark.asyncio
async def test_fail_node_marks_attempt_failed(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "plan")
    await service.start_attempt(session.id, "plan")
    await service.fail_node(session.id, "plan", error="boom")
    node = await service.get_node(session.id, "plan")
    assert node.status is NodeStatus.FAILED
    assert node.attempts[-1].status is AttemptStatus.FAILED
    assert node.attempts[-1].error == "boom"


@pytest.mark.asyncio
async def test_execution_lease_grant_heartbeat_release(
    service: SessionService,
) -> None:
    session = await service.create_session(_blueprint())
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "plan")
    execution = await service.start_node_execution(session.id, "plan", trace_id="trace-plan")

    leased = await service.grant_execution_lease(
        session.id,
        "plan",
        execution.id,
        owner="worker-1",
        ttl_seconds=1,
        lease_token="lease-1",
    )
    assert leased.lease_token == "lease-1"
    assert leased.lease_owner == "worker-1"
    assert leased.last_heartbeat_at is not None
    assert leased.lease_expires_at is not None
    assert service.is_execution_lease_live(leased)

    await service.assert_execution_lease(session.id, execution.id, "lease-1")
    heartbeat = await service.heartbeat_execution_lease(
        session.id,
        execution.id,
        lease_token="lease-1",
        ttl_seconds=60,
    )
    assert heartbeat.lease_expires_at is not None
    assert leased.lease_expires_at is not None
    assert heartbeat.lease_expires_at > leased.lease_expires_at

    released = await service.release_execution_lease(
        session.id,
        execution.id,
        lease_token="lease-1",
    )
    assert released.lease_token is None
    assert released.lease_owner is None
    assert released.last_heartbeat_at is None
    assert released.lease_expires_at is None


@pytest.mark.asyncio
async def test_execution_lease_fails_closed_and_clears_on_completion(
    service: SessionService,
) -> None:
    session = await service.create_session(_blueprint())
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "plan")
    execution = await service.start_node_execution(session.id, "plan")

    with pytest.raises(ExecutionLeaseError, match="owner"):
        await service.grant_execution_lease(
            session.id,
            "plan",
            execution.id,
            owner="",
        )

    await service.grant_execution_lease(
        session.id,
        "plan",
        execution.id,
        owner="worker-1",
        lease_token="lease-1",
    )
    with pytest.raises(ExecutionLeaseError, match="mismatch"):
        await service.assert_execution_lease(session.id, execution.id, "wrong")

    await service.complete_node(session.id, "plan", output={"ok": True})
    node = await service.get_node(session.id, "plan")
    completed = node.latest_attempt()
    assert completed is not None
    assert completed.status is AttemptStatus.SUCCEEDED
    assert completed.lease_token is None
    assert completed.lease_owner is None

    with pytest.raises(ExecutionLeaseError, match="active lease"):
        await service.assert_execution_lease(session.id, execution.id, "lease-1")


@pytest.mark.asyncio
async def test_records_tool_execution_ledger_under_node_execution(
    service: SessionService,
) -> None:
    session = await service.create_session(_blueprint())
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "plan")
    execution = await service.start_node_execution(
        session.id,
        "plan",
        trace_id="trace-tool",
    )

    started = await service.record_tool_execution_started(
        session.id,
        execution.id,
        tool_call_id="call-1",
        tool_name="search.web",
        round_no=1,
        arguments={"query": "HydraMind", "api_key": "secret"},
        trace_id="trace-tool",
        metadata={"execution_mode": "subagent", "subagent_id": "child-1"},
    )
    completed = await service.record_tool_execution_completed(
        session.id,
        execution.id,
        tool_call_id="call-1",
        is_error=False,
        result_preview={"content_preview": "ok", "content_json": True},
        content_length=128,
    )

    stored = await service.get_session(session.id)
    ledger = stored.nodes["plan"].latest_attempt().tool_executions
    assert len(ledger) == 1
    record = ledger[0]
    assert started.status is ToolExecutionStatus.STARTED
    assert completed.status is ToolExecutionStatus.SUCCEEDED
    assert record.status is ToolExecutionStatus.SUCCEEDED
    assert record.tool_call_id == "call-1"
    assert record.tool_name == "search.web"
    assert record.round_no == 1
    assert record.metadata == {
        "execution_mode": "subagent",
        "subagent_id": "child-1",
    }
    assert record.trace_id == "trace-tool"
    assert record.execution_id == execution.id
    assert record.arguments["api_key"] == "<redacted>"
    assert record.result_preview["content_preview"] == "ok"
    assert record.content_length == 128
    assert record.finished_at is not None


@pytest.mark.asyncio
async def test_control_plane_records_interaction_log_persistently(tmp_path) -> None:
    store_path = tmp_path / "sessions.sqlite"
    service = SessionService(SqliteSessionStore(store_path))
    control = ControlPlane(service)
    session = await service.create_session(_blueprint())
    record = InteractionLogRecord(
        session_id=session.id,
        node_key="plan",
        execution_id="exec-1",
        trace_id="trace-1",
        interaction_id="interaction-exec-1",
        team_id="team-1",
        workspace_id="workspace-1",
        event_kind=InteractionLogEventKind.INTERACTION_STARTED,
        detail={"member_ids": ["writer"]},
    )

    await control.record_interaction_event(record)
    await control.record_interaction_event(
        record.model_copy(
            update={
                "event_kind": InteractionLogEventKind.INTERACTION_COMPLETED,
                "id": "interaction-log-complete",
            }
        )
    )

    reloaded = await SessionService(SqliteSessionStore(store_path)).get_session(
        session.id
    )
    entries = reloaded.metadata["interaction_log"]["entries"]
    assert [entry["event_kind"] for entry in entries] == [
        "interaction_started",
        "interaction_completed",
    ]
    assert entries[0]["session_id"] == session.id
    assert entries[0]["node_key"] == "plan"
    assert entries[0]["execution_id"] == "exec-1"
    assert entries[0]["trace_id"] == "trace-1"
    assert entries[0]["interaction_id"] == "interaction-exec-1"
    assert entries[0]["team_id"] == "team-1"
    assert entries[0]["workspace_id"] == "workspace-1"
    assert entries[0]["detail"] == {"member_ids": ["writer"]}


@pytest.mark.asyncio
async def test_interaction_log_rejects_session_id_mismatch(
    service: SessionService,
) -> None:
    session = await service.create_session(_blueprint())
    record = InteractionLogRecord(
        session_id="sess-other",
        node_key="plan",
        execution_id="exec-1",
        interaction_id="interaction-exec-1",
        team_id="team-1",
        event_kind=InteractionLogEventKind.INTERACTION_STARTED,
    )

    with pytest.raises(ValueError, match="does not match"):
        await service.record_interaction_event(session.id, record)


@pytest.mark.asyncio
async def test_store_returns_deep_copies(service: SessionService) -> None:
    session = await service.create_session(_blueprint())
    assert session.version == 1
    s1 = await service.get_session(session.id)
    s2 = await service.get_session(session.id)
    assert s1 is not s2
    s1.nodes["plan"].status = NodeStatus.RUNNING
    s3 = await service.get_session(session.id)
    assert s3.nodes["plan"].status is NodeStatus.QUEUED


@pytest.mark.asyncio
async def test_in_memory_store_rejects_stale_session_versions() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    created = await service.create_session(_blueprint())
    first = await store.get(created.id)
    second = await store.get(created.id)
    assert first is not None
    assert second is not None
    assert first.version == second.version == 1

    first.metadata["writer"] = "first"
    await store.put(first)
    assert first.version == 2

    second.metadata["writer"] = "second"
    with pytest.raises(SessionStoreConflictError, match="version conflict"):
        await store.put(second)

    latest = await store.get(created.id)
    assert latest is not None
    assert latest.version == 2
    assert latest.metadata["writer"] == "first"


@pytest.mark.asyncio
async def test_add_workflow_nodes_and_update_metadata(
    service: SessionService,
) -> None:
    session = await service.create_session(
        WorkflowBlueprint(
            name="dynamic",
            nodes=(WorkflowNodeSpec(key="research", role="researcher"),),
        )
    )

    await service.add_workflow_nodes(
        session.id,
        (WorkflowNodeSpec(key="write", role="writer", requires=("research",)),),
    )
    await service.update_session_metadata(
        session.id,
        {"execution_plan": {"tasks": [{"key": "research"}, {"key": "write"}]}},
    )
    updated = await service.get_session(session.id)

    assert set(updated.nodes) == {"research", "write"}
    assert updated.nodes["write"].status is NodeStatus.QUEUED
    assert updated.metadata["execution_plan"]["tasks"][1]["key"] == "write"

    with pytest.raises(ValueError, match="already has node"):
        await service.add_workflow_nodes(
            session.id,
            (WorkflowNodeSpec(key="write", role="writer"),),
        )


@pytest.mark.asyncio
async def test_apply_workflow_revision_requeues_changed_node_and_downstream(
    service: SessionService,
) -> None:
    current = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
            WorkflowNodeSpec(key="review", role="reviewer", requires=("write",)),
        ),
    )
    session = await service.create_session(current)
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "research")
    await service.start_attempt(session.id, "research")
    await service.complete_node(session.id, "research", output={"v": 1})
    await service.start_node(session.id, "write")
    await service.start_attempt(session.id, "write")
    await service.complete_node(session.id, "write", output={"draft": 1})

    revised = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher", description="v2"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
            WorkflowNodeSpec(key="review", role="reviewer", requires=("write",)),
        ),
    )
    updated = await service.apply_workflow_revision(
        session.id,
        WorkflowRevision(
            current_blueprint=current,
            revised_blueprint=revised,
            changed_node_keys=("research",),
            reason="research contract changed",
            metadata={"execution_plan": {"version": "2"}},
        ),
    )

    assert updated.nodes["research"].status is NodeStatus.QUEUED
    assert updated.nodes["write"].status is NodeStatus.QUEUED
    assert updated.nodes["review"].status is NodeStatus.QUEUED
    assert updated.nodes["research"].attempts[-1].output == {"v": 1}
    assert updated.metadata["execution_plan"] == {"version": "2"}
    assert updated.metadata["workflow_revisions"][-1]["changed_node_keys"] == ["research"]
    assert updated.metadata["workflow_revisions"][-1]["requeued_node_keys"] == [
        "research",
        "review",
        "write",
    ]


@pytest.mark.asyncio
async def test_apply_workflow_revision_marks_removed_node_stale(
    service: SessionService,
) -> None:
    current = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
        ),
    )
    session = await service.create_session(current)
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "research")
    await service.start_attempt(session.id, "research")
    await service.complete_node(session.id, "research", output={"facts": "old"})

    revised = WorkflowBlueprint(
        name="dynamic",
        nodes=(WorkflowNodeSpec(key="write", role="writer"),),
    )
    updated = await service.apply_workflow_revision(
        session.id,
        WorkflowRevision(
            current_blueprint=current,
            revised_blueprint=revised,
            reason="research no longer needed",
        ),
    )

    assert updated.nodes["research"].status is NodeStatus.STALE
    assert updated.nodes["research"].latest_attempt().output == {"facts": "old"}
    assert updated.nodes["write"].status is NodeStatus.QUEUED
    assert updated.metadata["workflow_revisions"][-1]["removed_node_keys"] == ["research"]


@pytest.mark.asyncio
async def test_apply_workflow_revision_rejects_active_changed_node(
    service: SessionService,
) -> None:
    current = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
        ),
    )
    session = await service.create_session(current)
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "research")

    revised = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher", description="v2"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
        ),
    )
    with pytest.raises(InvalidTransitionError, match="active node cannot be requeued"):
        await service.apply_workflow_revision(
            session.id,
            WorkflowRevision(
                current_blueprint=current,
                revised_blueprint=revised,
                changed_node_keys=("research",),
                reason="research contract changed",
            ),
        )


@pytest.mark.asyncio
async def test_apply_workflow_revision_rejects_failed_removed_node(
    service: SessionService,
) -> None:
    current = WorkflowBlueprint(
        name="dynamic",
        nodes=(
            WorkflowNodeSpec(key="research", role="researcher"),
            WorkflowNodeSpec(key="write", role="writer", requires=("research",)),
        ),
    )
    session = await service.create_session(current)
    await service.mark_session_running(session.id)
    await service.start_node(session.id, "research")
    await service.start_attempt(session.id, "research")
    await service.fail_node(session.id, "research", error="provider failed")

    revised = WorkflowBlueprint(
        name="dynamic",
        nodes=(WorkflowNodeSpec(key="write", role="writer"),),
    )
    with pytest.raises(InvalidTransitionError, match="failed node cannot be removed"):
        await service.apply_workflow_revision(
            session.id,
            WorkflowRevision(
                current_blueprint=current,
                revised_blueprint=revised,
                reason="research no longer needed",
            ),
        )
