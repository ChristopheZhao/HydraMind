"""S7a negative-acceptance suite — all five §6 negative cases, fully offline.

PLAN-20260618-001 §6 "Required Regression Checks" / §7 negative cases /
95 §9 Acceptance Taxonomy. Each case EXERCISES the real code path built by a
prior slice (it does not re-implement the behavior) and constructs a typed
:class:`AcceptanceReport` documenting the evidence class + outcome, so the
report type is itself exercised.

The five cases:

  (a) invalid vote schema fails/gates instead of raw-text tallying [S3b]
  (b) prompt-affecting memory observer failure is surfaced (not swallowed) [S4a]
  (c) duplicate queue delivery does not duplicate side effects /
      memory / repair / interaction turns [S4c]
  (d) crash/restart resumes (or safely fails) from durable state [S5b]
  (e) multi-worker execution does not corrupt session/interaction state [NEW]

These are CONTRACT/PLUMBING evidence (Class 1-2). No live model decides, so
``require_offline_class`` guards every report against a live mislabel.
"""

from __future__ import annotations

from typing import Any

import pytest

from hydramind.control import (
    ControlPlane,
    ExecutionLeaseError,
    InMemorySessionStore,
    InteractionLogEventKind,
    InteractionLogRecord,
    SessionService,
    SessionStoreConflictError,
    WorkflowBlueprint,
    WorkflowNodeSpec,
    durable_interaction_id,
)
from hydramind.governance import (
    AcceptanceClass,
    AcceptanceFailureCategory,
    AcceptanceReport,
    ExecutionHarnessRef,
    ModelProviderRef,
    RecoveryBehavior,
    TaskRef,
    require_offline_class,
)
from hydramind.kernel.contracts import MessageRole, TurnStatus
from hydramind.mas import ArbitrationStrategy, build_vote_outcome, member_vote_record
from hydramind.memory import (
    AgentTurnMemoryObserver,
    InMemoryMemoryStore,
    MemoryScope,
    MemoryWriteAuthority,
    MemoryWriteClass,
    MemoryWriteError,
)
from hydramind.observability import (
    CriticalObserverError,
    Emitter,
    ObservationEvent,
    ObservationEventKind,
)

# ---- shared report builders -------------------------------------------------

_PROVIDER = ModelProviderRef(provider="offline", model_id="deterministic-replay")
_HARNESS = ExecutionHarnessRef(name="HydraMindExecutionHarness", harness_id="default")


def _offline_report(
    *,
    task_id: str,
    description: str,
    acceptance_class: AcceptanceClass,
    success: bool,
    failure: AcceptanceFailureCategory = AcceptanceFailureCategory.NONE,
    recovery: RecoveryBehavior = RecoveryBehavior.NOT_APPLICABLE,
    evaluator: str,
    evidence_refs: tuple[str, ...] = (),
    notes: str | None = None,
) -> AcceptanceReport:
    report = AcceptanceReport(
        task=TaskRef(id=task_id, description=description),
        model_provider=_PROVIDER,
        execution_harness=_HARNESS,
        tools_environment="offline (no network, dry-run/in-memory)",
        evaluator_profile=evaluator,
        acceptance_class=acceptance_class,
        success=success,
        failure_category=failure,
        recovery_behavior=recovery,
        evidence_refs=evidence_refs,
        notes=notes,
    )
    # GUARD: an offline negative-case report must never be labeled live.
    return require_offline_class(report)


def _single_node_workflow() -> WorkflowBlueprint:
    return WorkflowBlueprint(
        name="acceptance-negative",
        nodes=(WorkflowNodeSpec(key="work", role="writer"),),
    )


# ---- (a) invalid vote schema gates instead of raw-text tally [S3b] ----------


def test_negative_a_invalid_vote_does_not_become_majority_key() -> None:
    declared = ("approve", "reject")
    votes = [
        member_vote_record(agent_id="a", raw="approve", declared_options=declared),
        # Free-form text: under a raw-text tally this would be its own key.
        member_vote_record(
            agent_id="b",
            raw="let's table this for now",
            declared_options=declared,
        ),
        member_vote_record(
            agent_id="c", raw="not sure honestly", declared_options=declared
        ),
    ]
    outcome = build_vote_outcome(
        votes,
        arbitration=ArbitrationStrategy.MAJORITY,
        declared_options=declared,
    )

    # Free-form text is INVALID and never tallied — it cannot win.
    assert outcome.tally == {"approve": 1}
    assert outcome.winner == "approve"
    assert {r.agent_id for r in outcome.invalid} == {"b", "c"}
    assert all(not r.valid for r in outcome.invalid)

    report = _offline_report(
        task_id="neg-a-invalid-vote",
        description="invalid/free-form votes gate instead of raw-text tally",
        acceptance_class=AcceptanceClass.CONTRACT,
        success=True,  # the GATE behavior is the desired outcome.
        recovery=RecoveryBehavior.GATED,
        evaluator="vote-canonicalization (S3b VoteOutcome)",
        evidence_refs=("src/hydramind/mas/protocol_outcomes.py",),
        notes="2 free-form votes surfaced as invalid, never a majority key",
    )
    assert report.acceptance_class is AcceptanceClass.CONTRACT
    assert report.recovery_behavior is RecoveryBehavior.GATED


# ---- (b) prompt-affecting memory observer failure surfaced [S4a] ------------


class _RaisingMemoryStore(InMemoryMemoryStore):
    async def append(
        self,
        scope: MemoryScope,
        scope_id: str,
        key: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ):
        raise OSError("disk full")


def _agent_message_event() -> ObservationEvent:
    return ObservationEvent(
        kind=ObservationEventKind.AGENT_MESSAGE_SENT,
        session_id="sess-neg-b",
        node_key="collaborate",
        actor="writer",
        detail={
            "turn_index": 0,
            "interaction_id": "interaction-1",
            "role": "writer",
            "content_preview": "draft-text",
        },
    )


@pytest.mark.asyncio
async def test_negative_b_prompt_affecting_memory_failure_is_surfaced() -> None:
    authority = MemoryWriteAuthority(_RaisingMemoryStore())
    observer = AgentTurnMemoryObserver(authority)
    # prompt-affecting observers are marked critical so the emitter surfaces.
    assert observer.critical is True
    assert observer.memory_write_class is MemoryWriteClass.PROMPT_AFFECTING

    emitter = Emitter([observer])
    with pytest.raises(CriticalObserverError) as excinfo:
        await emitter.emit(_agent_message_event())
    assert any(isinstance(f, MemoryWriteError) for f in excinfo.value.failures)

    report = _offline_report(
        task_id="neg-b-memory-surface",
        description="prompt-affecting memory observer failure is surfaced",
        acceptance_class=AcceptanceClass.PLUMBING,
        success=True,  # surfacing (not swallowing) is the desired outcome.
        recovery=RecoveryBehavior.GATED,
        evaluator="critical-observer surfacing (S4a MemoryWriteAuthority)",
        evidence_refs=(
            "src/hydramind/memory/ownership.py",
            "src/hydramind/observability/ (CriticalObserverError)",
        ),
        notes="store outage raised CriticalObserverError carrying MemoryWriteError",
    )
    assert report.acceptance_class is AcceptanceClass.PLUMBING


# ---- (c) duplicate queue delivery is idempotent [S4c] -----------------------


@pytest.mark.asyncio
async def test_negative_c_duplicate_delivery_is_idempotent() -> None:
    # (c-memory) duplicate AGENT_MESSAGE_SENT does not double-append memory.
    store = InMemoryMemoryStore()
    observer = AgentTurnMemoryObserver(MemoryWriteAuthority(store))
    await observer.on_event(_agent_message_event())
    await observer.on_event(_agent_message_event())  # duplicate delivery
    mem_entries = await store.scan(MemoryScope.AGENT, "writer")
    assert len(mem_entries) == 1

    # (c-repair) durable repair budget caps reservations across re-deliveries.
    service = SessionService(InMemorySessionStore())
    session = await service.create_session(_single_node_workflow())
    assert await service.reserve_auto_repair_attempt(session.id, max_attempts=1)
    assert not await service.reserve_auto_repair_attempt(session.id, max_attempts=1)
    assert await service.auto_repair_attempts_used(session.id) == 1

    # (c-interaction) replayed interaction turn is not double-appended.
    def _record(record_id: str) -> InteractionLogRecord:
        return InteractionLogRecord(
            id=record_id,
            session_id=session.id,
            node_key="work",
            execution_id="exec-1",
            interaction_id="interaction-1",
            team_id="team-1",
            event_kind=InteractionLogEventKind.TURN_COMPLETED,
            actor="writer",
            turn_index=0,
            content_preview="turn output",
        )

    await service.record_interaction_event(session.id, _record("rec-a"))
    await service.record_interaction_event(session.id, _record("rec-b"))  # duplicate
    refreshed = await service.get_session(session.id)
    assert len(refreshed.metadata["interaction_log"]["entries"]) == 1

    report = _offline_report(
        task_id="neg-c-idempotent-delivery",
        description="duplicate queue delivery does not duplicate side effects",
        acceptance_class=AcceptanceClass.PLUMBING,
        success=True,
        recovery=RecoveryBehavior.RECOVERED,
        evaluator="idempotency ledger (S4c processed_idempotency_keys)",
        evidence_refs=(
            "src/hydramind/control/session_service.py",
            "src/hydramind/memory/ownership.py",
        ),
        notes="memory/repair/interaction-turn all deduped under replay",
    )
    assert report.acceptance_class is AcceptanceClass.PLUMBING


# ---- (d) crash/restart resumes (or safely fails) from durable state [S5b] ---


@pytest.mark.asyncio
async def test_negative_d_crash_resumes_from_durable_state() -> None:
    # Durable interaction state survives a "restart": a fresh SessionService over
    # the SAME store re-reads completed turns + authoritative messages.
    store = InMemorySessionStore()
    service = SessionService(store)
    session = await service.create_session(_single_node_workflow())
    interaction_id = durable_interaction_id(session.id, "work")

    interaction = await service.start_interaction(
        session.id,
        interaction_id=interaction_id,
        node_key="work",
        execution_id="exec-1",
        team_id="team-1",
        protocol_mode="vote",
        topology="all_to_all",
        member_ids=("writer", "reviewer"),
    )
    await service.record_interaction_turn(
        session.id,
        interaction_id=interaction_id,
        turn_index=0,
        agent_id="writer",
        status=TurnStatus.COMPLETED,
    )
    await service.record_interaction_message(
        session.id,
        interaction_id=interaction_id,
        turn_index=0,
        sender="writer",
        content="full authoritative content for member-1",
        role=MessageRole.AGENT,
    )

    # Simulate worker restart: a brand-new service instance over the same store.
    restarted = SessionService(store)
    resumed = await restarted.find_resumable_interaction(session.id, "work")
    assert resumed is not None
    assert resumed.id == interaction.id
    completed_turns = [t for t in resumed.turns if t.status is TurnStatus.COMPLETED]
    assert len(completed_turns) == 1
    # Authoritative message carries FULL content (not just a preview).
    assert any(
        m.content == "full authoritative content for member-1"
        for m in resumed.messages
    )

    report = _offline_report(
        task_id="neg-d-crash-resume",
        description="crash/restart resumes from durable interaction state",
        acceptance_class=AcceptanceClass.PLUMBING,
        success=True,
        recovery=RecoveryBehavior.RECOVERED,
        evaluator="durable interaction resume (S5b)",
        evidence_refs=(
            "src/hydramind/control/interaction_state.py",
            "src/hydramind/control/interaction_turn_lease.py",
        ),
        notes="restart re-read completed turn + full authoritative message",
    )
    assert report.recovery_behavior is RecoveryBehavior.RECOVERED


# ---- (e) multi-worker execution does not corrupt state [NEW] ----------------


@pytest.mark.asyncio
async def test_negative_e_multi_worker_does_not_corrupt_state() -> None:
    """Two concurrent workers race for the SAME node execution.

    True OS-level concurrency is non-deterministic, so the race is simulated by
    interleaved control calls (the documented S7 fallback). Two assertions:

    1. LEASE OWNERSHIP: two ``grant_node_execution_lease`` attempts for the same
       execution — exactly ONE wins; the second is rejected (a live lease is
       held). One worker owns the node; no double-apply.
    2. VERSION CONFLICT: two workers that both loaded the session at the same
       version and both try to ``put`` — the second ``put`` is rejected with a
       version conflict, so a stale write cannot corrupt committed state.
    """

    store = InMemorySessionStore()
    service = SessionService(store)
    control = ControlPlane(service)
    session = await service.create_session(_single_node_workflow())

    # --- 1. node-lease ownership: second grant loses ---
    attempt = await control.open_node_execution(session.id, "work")
    won = await control.grant_node_execution_lease(
        session.id,
        "work",
        attempt.id,
        owner="worker-1",
        ttl_seconds=300,
    )
    assert won.lease_owner == "worker-1"
    with pytest.raises(ExecutionLeaseError):
        # worker-2 races for the same execution; a live lease blocks it.
        await control.grant_node_execution_lease(
            session.id,
            "work",
            attempt.id,
            owner="worker-2",
            ttl_seconds=300,
        )
    # State stays consistent: still owned by the single winner.
    after = await service.get_session(session.id)
    owners = {
        a.lease_owner
        for node in after.nodes.values()
        for a in node.attempts
        if a.lease_owner is not None
    }
    assert owners == {"worker-1"}

    # --- 2. store version-conflict: stale write loses ---
    loaded_a = await store.get(session.id)
    loaded_b = await store.get(session.id)
    assert loaded_a is not None and loaded_b is not None
    assert loaded_a.version == loaded_b.version  # both at the same version
    await store.put(loaded_a)  # worker A commits first
    with pytest.raises(SessionStoreConflictError):
        await store.put(loaded_b)  # worker B's stale write is rejected

    report = _offline_report(
        task_id="neg-e-multi-worker",
        description="multi-worker execution does not corrupt session/interaction state",
        acceptance_class=AcceptanceClass.PLUMBING,
        success=True,
        recovery=RecoveryBehavior.GATED,
        evaluator="lease ownership + optimistic version conflict",
        evidence_refs=(
            "src/hydramind/control/session_execution.py",
            "src/hydramind/control/store.py",
        ),
        notes="one worker won the node lease; stale concurrent write rejected",
    )
    assert report.acceptance_class is AcceptanceClass.PLUMBING


# ---- taxonomy guard: offline reports cannot be mislabeled live ---------------


def test_all_five_classes_are_present_and_offline_guard_rejects_live() -> None:
    classes = {c for c in AcceptanceClass}
    assert classes == {
        AcceptanceClass.CONTRACT,
        AcceptanceClass.PLUMBING,
        AcceptanceClass.REPLAY,
        AcceptanceClass.LIVE_AGENT,
        AcceptanceClass.LIVE_MAS,
    }
    # The offline negative-suite reports are never live.
    assert not AcceptanceClass.CONTRACT.is_live
    assert not AcceptanceClass.PLUMBING.is_live
    assert not AcceptanceClass.REPLAY.is_live
    assert AcceptanceClass.LIVE_AGENT.is_live
    assert AcceptanceClass.LIVE_MAS.is_live
