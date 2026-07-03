# 20 — Control Plane

> The single owner of runtime state mutations. Read this with `00-overview.md` §2.

## 1. Position in the Stack

```
Orchestration ─── decides what to do next
       │
       ▼
Control ──────── single writer of RuntimeSession (SoT)
       │
       ▼
Gating ────────── authorizes transitions via GateResult
       │
       ▼
Harness ──────── executes LLM/tool calls
```

The control plane is the **single writer** of runtime state. Orchestrator agents decide and report; the control plane validates and applies. No other layer mutates `RuntimeSession`.

## 2. Data Model

Three nested aggregates carry runtime state:

```
RuntimeSession            (session-level SoT)
├── NodeState[*]          (per workflow node)
│   ├── NodeExecution[*]  (control-owned execution records; `attempt_no` is retry metadata)
│   │   └── ToolExecution[*] (durable redacted tool-call ledger for this execution)
│   └── Gate[*]           (latest gate result(s) for the node)
└── GateDecision[*]       (decisions applied to gates)
```

This mirrors the reference project's proven schema, with the video-specific
parts removed:

| Reference project | HydraMind P0 | Why |
|---|---|---|
| SQLAlchemy ORM, multi-table joins | Pydantic models + pluggable `SessionStore` | Avoid framework lock-in; SQL persistence is P1 |
| Lease tokens on node execution records | Control-owned execution lease primitives | Lease facts live with `NodeExecution`; distributed CAS/worker enforcement remains later |
| Hard-coded `DEFAULT_NODE_BLUEPRINT` | External `WorkflowBlueprint` config | Removes the largest video-specific debt |

### Status Enums

```python
class SessionStatus(StrEnum):
    QUEUED        # accepted, not yet running
    RUNNING       # at least one node active
    WAITING_GATE  # paused on gate decision
    RESUMING      # gate cleared, restarting
    COMPLETED
    FAILED
    CANCELLED


class NodeStatus(StrEnum):
    QUEUED
    RUNNING
    PENDING_GATE   # awaiting GateResult
    APPROVED       # gate decision = approve
    NEEDS_REVISION # gate decision = revise → back to QUEUED on retry
    COMPLETED
    FAILED
    STALE          # upstream revised, this node's output is invalid


class AttemptStatus(StrEnum):
    RUNNING
    SUCCEEDED
    FAILED
    ABORTED


class ToolExecutionStatus(StrEnum):
    STARTED
    SUCCEEDED
    FAILED
```

Valid transitions are enforced by `states.is_valid_transition(from_, to_)`. The transition matrix is the single source of truth; service methods consult it before any write.

## 3. SessionService

`SessionService` is the only public API for mutating `RuntimeSession`. Categories:

| Category | Methods | Notes |
|---|---|---|
| Lifecycle | `create_session`, `mark_session_running`, `mark_session_waiting_gate`, `mark_session_resuming`, `complete_session`, `fail_session`, `cancel_session` | Each validates state-machine legality |
| Node | `start_node`, `mark_node_pending_gate`, `approve_node`, `mark_node_needs_revision`, `complete_node`, `fail_node`, `revise_back_to_queued` | Direct state transitions only |
| Workflow revision | `apply_workflow_revision` | Applies dynamic graph changes, preserves history, marks removed nodes `STALE`, and requeues changed/downstream nodes |
| Execution | `start_node_execution` (`start_attempt` remains a compatibility alias), close via `complete_node` / `fail_node` | Opened before harness/tool runtime work; `attempt_no` is retry metadata |
| Tool execution ledger | `record_tool_execution_started`, `record_tool_execution_completed` | Durable redacted record of tool calls under the active `NodeExecution`; observability remains evidence, not SoT |
| Execution recovery | `recover_expired_node_executions` | Aborts expired leased `RUNNING` executions and requeues their nodes before worker redelivery scheduling |
| Execution lease | `grant_execution_lease`, `assert_execution_lease`, `heartbeat_execution_lease`, `release_execution_lease` | Durable control-owned lease metadata for worker/runtime hosts; queue transports do not own lease authority |
| Gate | `record_gate`, `apply_gate_decision` | Validated against `GateContract` (S3) |
| Query | `get_session`, `get_node`, `get_latest_gate` | Read-only, returns deep-copied models |

**Concurrency model (P0)**: single-writer per session. Node executions can now
carry lease owner/token/heartbeat/expiry metadata, and the queue worker path
validates agent reports against the active execution lease. `SessionStore`
writes are versioned and reject stale `RuntimeSession` payloads with
`SessionStoreConflictError`; distributed worker safety still needs worker-process
liveness plus production transport visibility/DLQ policy.
Expired leased reports fail closed instead of being accepted as unleased
reports. On redelivery, the orchestrator asks control to recover expired
`RUNNING` node executions before scheduling, so the old execution is retained as
`ABORTED` history and the node becomes `QUEUED` for a fresh execution.

**Persistence**: `SessionStore` is an abstract Protocol with both `InMemorySessionStore` and `SqliteSessionStore` in P0. SQLite persists the full `RuntimeSession` JSON envelope while keeping `SessionService` as the only mutation owner. Successful writes increment `RuntimeSession.version`; stale writes fail closed instead of overwriting newer state.

## 4. ControlPlane

`ControlPlane` owns the execution envelope and the gate/apply loop. The orchestrator asks control to open a node execution before harness/tool work, then calls it once per agent report; the control plane:

1. Opens `NodeExecution` and returns `execution_id` / `trace_id` correlation.
2. Optionally grants a worker execution lease for runtime-hosted work.
3. Recovers expired leased executions before worker redelivery scheduling.
4. Records tool-call started/completed facts under the active `NodeExecution`.
5. Rejects reports for leased executions unless they carry the matching live lease token.
6. Calls injected `GateFn`(s) (S3 will formalize as `GateEvaluator`).
7. Converts gate outcomes, gate decisions, report errors, deriver output, or
   explicit workflow-revision calls into typed `ApplyIntent` requests.
8. Applies the intent through `SessionService`: complete, pause for gate, fail,
   requeue/retry, or graph update. Unsupported transitions fail closed through
   `ApplyIntent` validation or the state-transition matrix.
9. Emits control transition events; detailed model/tool trajectory is emitted by orchestration/runtime observers under the same `execution_id`.

```python
class ControlPlane:
    def __init__(
        self,
        service: SessionService,
        gate_fn: GateFn | None = None,
    ): ...

    async def open_node_execution(
        self, session_id: str, node_key: str, *, trace_id: str | None = None
    ) -> NodeExecution: ...

    async def open_runtime_decision(
        self, session_id: str, report: AgentReport
    ) -> RuntimeDecision: ...

    async def apply_decision(
        self, session_id: str, decision: GateDecisionInput
    ) -> NodeState: ...

    async def apply_workflow_revision(
        self, session_id: str, revision: WorkflowRevision
    ) -> RuntimeSession: ...

    async def recover_expired_node_executions(
        self, session_id: str, *, actor: str | None = None
    ) -> tuple[NodeExecution, ...]: ...

    async def record_tool_execution_started(...) -> ToolExecution: ...

    async def record_tool_execution_completed(...) -> ToolExecution: ...
```

`ApplyIntent` is the control facade's auditable transition request. It is a
closed enum over `complete`, `pause`, `fail`, `requeue`, and
`workflow_revision`. Report-derived transitions carry authorization metadata
from the gate result or explicit control deriver; gate-resume transitions carry
the applied `GateDecision`; graph updates must enter through
`ControlPlane.apply_workflow_revision()`. Legacy `ApplyPayload` remains a
deriver input shape for completed-node output and is normalized into
`ApplyIntent` before mutation.

`ToolExecution` stores the durable execution facts needed for post-crash
diagnosis: `tool_call_id`, `tool_name`, round, redacted arguments, status,
redacted result preview, content length, timestamps, `trace_id`, and
`execution_id`. Optional `metadata` may identify evidence-only origin such as a
subagent id and role, and may include a tool side-effect evidence envelope such
as risk class, side-effect class, dry/live scope, and a deterministic
redaction-safe effect fingerprint. It is not a second control state machine.
The ledger deliberately does not store raw tool payloads or implement
idempotent replay; those require a future artifact/audit store and side-effect
policy.
When orchestration suppresses a duplicate successful tool call inside the same
`NodeExecution`, the duplicate still gets its own `ToolExecution` row and is
marked with `metadata.reused_result=true` plus the source tool-call id. That is
runtime evidence for this execution only, not cross-node/session replay state.

`GateFn` is a callable typed `(session: RuntimeSession, node: NodeState, report: AgentReport) -> GateResult | None`. S3 will introduce `GateEvaluator` as a richer Protocol over this.

`WorkflowRevision` is the control-layer contract for dynamic planning. It takes
old and revised `WorkflowBlueprint` values plus changed node keys, computes
added/removed/downstream affected nodes, and applies the session mutation through
`SessionService`. Removed nodes are retained as `STALE` so node execution history
remains auditable; changed nodes and their old-graph descendants are moved back
to `QUEUED` when their current status can be safely rerun.

## 5. Ownership Invariants

These are checked by tests and enforced by code review:

1. Only `SessionService` mutates `RuntimeSession` (or its sub-objects).
2. `ControlPlane` calls `SessionService`; it never reaches into the store directly.
3. `OrchestratorAgent` (S4) calls `ControlPlane.open_node_execution()` and `ControlPlane.open_runtime_decision()`; it never mutates `RuntimeSession` directly.
4. `Harness` knows nothing about any of these types.
5. Workflow-specific node blueprints live in `WorkflowBlueprint` config, never hardcoded in this module.

## 6. What's Explicitly Out of P0

- **Worker-process liveness / production visibility timeout / production DLQ policy** → P1
- **Distributed lock beyond optimistic session CAS** → P1
- **Distributed stale propagation with lease/CAS guarantees** → P1
- **Gate authority registry** (which agent can trigger which gate) → S3 via `GateEvaluator.applicable_to()`
- **Distributed worker support** → P3

## 7. Heritage Diff vs Reference Project

| Reference (`short-video-maker`) | HydraMind P0 | Action |
|---|---|---|
| `runtime_session_service.py` (~2000 LOC) | `session_service.py` (~400 LOC target) | Drop ORM + lease, keep state machine |
| `orchestration_control_plane.py` (~380 LOC) | `control_plane.py` (~200 LOC target) | Drop `audio_contract`, `_GATE_AUTHORITY` → config |
| `WorkflowSession` ORM model | `RuntimeSession` Pydantic model | Faithful field translation |
| `DEFAULT_NODE_BLUEPRINT` constant | `WorkflowBlueprint` external config | Required parameter |
| `AudioDeliveryGateEvaluator` | (lives in `examples/short_video/` after S6) | Out of framework |
