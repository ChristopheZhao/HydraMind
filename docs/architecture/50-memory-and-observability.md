# 50 — Memory & Observability

Two cross-cutting subsystems delivered together because they share a design
constraint: **they observe the runtime, they never mutate it.**

## 1. Memory

The reference project's memory module distinguishes short-term **working
memory** (per-workflow scope, ephemeral) from long-term **episodic memory**
(cross-session snapshots). HydraMind keeps the same shape but with pluggable
stores and explicit scoping.

### Primitives

```python
class MemoryScope(StrEnum):
    AGENT      # visible to one agent identity within one session
    SESSION    # visible to all agents within one session
    WORKFLOW   # visible across all sessions of one workflow
    GLOBAL     # visible across all workflows (use sparingly)

class MemoryEntry(BaseModel):
    scope: MemoryScope
    scope_id: str        # e.g. agent_id, session_id, workflow_name, "global"
    key: str
    value: Any
    created_at: datetime
    metadata: dict[str, Any]
```

### Store Protocol

```python
class MemoryStore(Protocol):
    async def put(self, entry: MemoryEntry) -> None: ...
    async def get(self, scope, scope_id, key) -> MemoryEntry | None: ...
    async def append(self, scope, scope_id, key, value, metadata=None) -> MemoryEntry: ...
    async def scan(self, scope, scope_id, *, key_prefix=None) -> list[MemoryEntry]: ...
    async def delete(self, scope, scope_id, key) -> None: ...
```

`append` differs from `put` only in that it auto-generates a unique key
(`{key}.{counter}`) so the same logical name can hold an ordered list of values
(working-memory pattern).

### Built-in Stores

`InMemoryMemoryStore` is the process-local implementation for tests, examples,
and single-process runs. `SqliteMemoryStore` is the local durable implementation:
it stores one row per `(scope, scope_id, key)`, persists the full `MemoryEntry`
JSON envelope, and supports bounded `scan()` queries by explicit scope and key
prefix. Reopening the same SQLite file preserves entries and append suffixes.

The SQLite store is still only a typed memory backend. It does not introduce a
retrieval ranker, vector index, cross-process write policy, or runtime state
ownership. Runtime code must still opt into memory projection through
`MemoryContextPolicy` / `MemoryContextRetriever`, and retrieved entries remain
prompt context only.

Runtime assembly can bind a store either by passing a concrete `MemoryStore`
object or by using the configured factory exposed as
`create_memory_store(kind, path)`. That factory lives at the runtime support
edge and is backed by a process-local registry:
`register_memory_store(kind, builder)` lets extension code add a store kind
without changing the memory facades, observers, planner, executor, or
memory-context retriever. Built-in `memory` / `in_memory` and `sqlite` kinds are
registered by default.

The registry is only configuration assembly. It is not plugin discovery, memory
ownership, cross-process synchronization, or retrieval policy. Queued workers
that rely on a custom memory-store kind must register the same kind before goal
runtime assembly. Session persistence and memory persistence are separate
choices; `--session-store sqlite` never implies a SQLite memory store.
When goal runtime memory is enabled without an explicit store, the runtime edge
uses the registered `memory` store kind as the default; it does not instantiate a
concrete backend directly in the runtime entrypoint.

### Facades

`AgentMemory(store, agent_id)` and `EpisodicMemory(store, workflow_name)`
are thin facades that bind a scope/scope_id and offer ergonomic methods.
`AgentMemory` binds `MemoryScope.AGENT` to one agent identity (a team member id)
so an agent's own turn history is recoverable independently of the flat session
trajectory. Users can ignore the facades and talk to `MemoryStore` directly if
they prefer.

`EpisodeProjectorObserver` is optional. It consumes observation events and
writes compact episode summaries with `trace_id` / `execution_id` references,
including an ordered who-acted-next `agent_turns` sequence reconstructed from the
first-class agent/turn events. It does not store raw trajectory as memory and
never mutates `RuntimeSession`.

`AgentTurnMemoryObserver` is the opt-in production write path for
`MemoryScope.AGENT`: on each `AGENT_MESSAGE_SENT` event it appends the acting
agent's turn output under its agent id. Like the episode projector it is
observe-only — it reads events and writes memory, never touching
`RuntimeSession` or the control plane.

### What's NOT in memory's scope

- **No retrieval ranking / vector search** — memory is a typed key-value store;
  RAG belongs in user code via tools, not in the framework core.
- **No raw trajectory ownership** — raw process detail belongs to observability
  trace artifacts. Episodic memory stores trajectory-level summaries.
- **No automatic semantic summarization** — that's user policy or harness
  compaction. The built-in projector only builds compact structural summaries.
- **No cross-process locking** — single-writer assumption matches control plane.

## 2. Observability

Every meaningful state transition in `SessionService` and `ControlPlane` emits a
typed `ObservationEvent`. Orchestration/runtime mechanics emit detailed
trajectory events for model invokes, tool calls, tool results, and tool-drain
rounds. Subscribers (`Observer`s) consume events and write to stdout, JSONL
artifacts, a structured logger, or an OpenTelemetry exporter.

When a control event happens inside a node execution envelope, it carries the
same top-level `trace_id` and `execution_id` fields as the orchestration
model/tool events. This keeps control transitions, gates, decisions, tool
drains, and terminal session events joinable without making trace artifacts the
authoritative runtime state.

Tool trace events are evidence, not model context. `tool_call_started` stores
redacted arguments. `tool_call_completed` stores a structured, redacted preview
of `ToolResultBlock.content` plus basic shape metadata; the raw tool result is
still passed back to the harness turn unchanged. This keeps smoke/eval artifacts
useful without making observability the owner of raw tool payloads.

The control layer now also stores a compact `ToolExecution` ledger under each
`NodeExecution`. That ledger is the runtime SoT for whether a tool call started,
succeeded, or failed. Observability remains the detailed trajectory stream:
use it for operator inspection, smoke evidence, and episode projection, but do
not rely on it as the recovery authority for tool side effects.

For child-originated tool calls, observability and the ledger share the same
origin metadata shape. `TOOL_DRAIN_ROUND`, `TOOL_CALL_STARTED`, and
`TOOL_CALL_COMPLETED` include an `origin` object, while the corresponding
`ToolExecution.metadata` stores the same `execution_mode`, `subagent_id`, and
`subagent_role`. Episodic memory may summarize that relationship later, but raw
child trajectories remain observability evidence.

Tool trace events may also include `execution_metadata`, mirroring the
redaction-safe side-effect envelope persisted in `ToolExecution.metadata`.
Observers can use this to correlate risk class and effect fingerprints with the
raw trajectory. Memory projections should summarize the relationship, not store
raw tool arguments or results.
If a duplicate successful tool call is suppressed inside one `NodeExecution`,
the corresponding `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` events include
`execution_metadata.reused_result=true` and a source tool-call id. This keeps the
trajectory explainable while the control-owned `ToolExecution` ledger remains
the authoritative fact that the duplicate was not handler-executed.

Planning can happen before a `RuntimeSession` exists, so model-planner
diagnostics are stored as compact plan metadata rather than trace events:
`planner_diagnostics` records status and counts for invoke attempts, retries,
repairs, and phases, while `last_plan_delta_diagnostics` captures the latest
replan delta diagnostics after a control-owned graph revision. It deliberately
does not store raw planner prompts or model responses.

Memory can enter planner and executor prompts only through the opt-in
`MemoryContextPolicy` / `MemoryContextRetriever` surface in
`hydramind.orchestration`. The built-in store-backed retriever performs bounded
`MemoryStore.scan()` calls over explicit `(scope, scope_id, key_prefix)` queries
and returns entries with `memory://...` evidence refs. The retrieved projection
is prompt context only: orchestration does not write it into `RuntimeSession`,
`ExecutionPlan.metadata`, node output, or memory stores, and it does not add
vector search, hidden ranking, or automatic summarization.

### Event Schema

```python
class ObservationEventKind(StrEnum):
    SESSION_CREATED
    SESSION_RUNNING
    SESSION_WAITING_GATE
    SESSION_RESUMING
    SESSION_COMPLETED
    SESSION_FAILED
    SESSION_CANCELLED
    NODE_STARTED
    NODE_COMPLETED
    NODE_FAILED
    NODE_REVISED          # NEEDS_REVISION → QUEUED transition
    ATTEMPT_STARTED          # compatibility name for the internal retry record
    NODE_EXECUTION_STARTED
    NODE_EXECUTION_COMPLETED
    NODE_EXECUTION_FAILED
    NODE_EXECUTION_ABORTED   # expired leased execution was recovered/requeued
    MODEL_INVOKE_STARTED
    MODEL_INVOKE_COMPLETED
    TOOL_DRAIN_ROUND
    TOOL_CALL_STARTED
    TOOL_CALL_COMPLETED
    GATE_RECORDED
    DECISION_APPLIED
    EXECUTION_LEASE_GRANTED
    EXECUTION_LEASE_HEARTBEAT
    EXECUTION_LEASE_RELEASED

class ObservationEvent(BaseModel):
    event_id: str
    kind: ObservationEventKind
    session_id: str
    node_key: str | None = None
    trace_id: str | None = None
    execution_id: str | None = None
    parent_event_id: str | None = None
    actor: str | None = None
    source: str
    level: str
    detail: dict[str, Any] = {}
    created_at: datetime
```

### Emitter / Observer

```python
class Observer(Protocol):
    async def on_event(self, event: ObservationEvent) -> None: ...

class Emitter:
    def add(self, observer: Observer) -> None: ...
    async def emit(self, event: ObservationEvent) -> None: ...
    async def close(self) -> None: ...
```

The emitter dispatches to **all** subscribed observers concurrently and
collects errors — one failing observer never blocks the others.

### Built-in Observers (P0)

| Observer | Purpose |
|---|---|
| `LoggingObserver` | Logs to a standard `logging.Logger` (or structlog if installed) with one line per event |
| `OpenTelemetryObserver` | Lazy-imports `opentelemetry` and emits OTel GenAI semantic-convention spans |
| `ListObserver` | Collects events into a list (for tests) |
| `JsonlObserver` | Writes one event per JSON line for smoke tests and run-state console artifacts |

### Integration with SessionService

`SessionService` accepts an optional `emitter`:

```python
service = SessionService(InMemorySessionStore(), emitter=Emitter([LoggingObserver()]))
```

When present, every public mutation method emits one or more events. When
absent, the service is silent — backward-compatible with all S2 tests.

### What's NOT in observability's scope

- **Metrics** (counters, histograms) — events stream only in P0; metrics
  aggregation is P2.
- **Distributed tracing context propagation across processes** — local span
  emission only in P0; carrier headers are P3.
- **Authoritative runtime state** — trace events are evidence. `RuntimeSession`
  remains the control-owned SoT.
- **Raw tool-result storage** — observability records redacted previews and
  shape metadata. Raw payload retention belongs to a caller-supplied artifact or
  audit store, not the default trace stream.
- **Tool execution authority** — tool started/completed facts are persisted by
  the control-owned `ToolExecution` ledger. Observers may mirror them as trace
  evidence, but they do not own recovery state.
- **Framework-core dashboarding** — examples may render trace artifacts, but
  HydraMind core does not ship a live UI server.

## 3. Ownership Invariants

1. Memory and observability **observe** the runtime; they never mutate
   `RuntimeSession` or call back into the control plane.
2. `MemoryStore` and `Observer` are Protocols. Users supply their own.
3. The framework reads memory only when a caller supplies an enabled
   `MemoryContextPolicy` and retriever. The retrieved projection may inform
   planner/executor prompts, but decisions still belong to the planner,
   orchestrator, and agent.
4. Observation failures are isolated — a broken observer cannot fail a session.

## 4. Heritage Diff

| Reference (`short-video-maker`) | HydraMind P0 | Action |
|---|---|---|
| `backend/app/agents/memory/` (163 files) | `hydramind.memory` (≤6 files) | Massive simplification; pluggable store keeps room for users to add what they need |
| ad-hoc `shared_memory_id` field | `MemoryScope.SESSION` with explicit scope_id | Typed |
| `obs_builder.py` / `obs_events.py` / `obs_validator.py` | `ObservationEvent` + `Emitter` | Smaller surface; same intent |
| OTel adapter coupled inside `orchestration_observation_adapter.py` | `OpenTelemetryObserver` plugin | Inverted: framework emits, adapter subscribes |
