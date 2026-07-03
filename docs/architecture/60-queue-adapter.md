# 60 — Queue Adapter (Agent–Queue Decoupling)

> Differentiation Anchor #3. In most MAS frameworks, the workflow engine and the worker pool are the same process. HydraMind's queue layer is an adapter behind a Protocol — the framework caller decides whether to run in-process, on Celery, or on something else, and the choice never reaches into the orchestrator or control plane.

## 1. Why a Queue Layer

The reference project (short-video-maker) proved the importance of separation: `task_queue.py` only handles eligibility scheduling; the orchestrator mainline owns runtime state. The bug-prone alternative — orchestrator state living inside Celery tasks — was rejected during their architecture review and we keep that lesson.

HydraMind makes this discipline structural via a `QueueAdapter` Protocol.

## 2. What the Queue Layer Owns

| Responsibility | Owner |
|---|---|
| Enqueue a session for execution | `QueueAdapter` |
| Pull next ready session id | Pollable `QueueAdapter` capability |
| Acknowledge / retry on failure | Pollable `QueueAdapter` capability |
| Heartbeat / visibility timeout | Pollable `QueueAdapter` capability (impl-specific) |
| **Workflow state (SoT)** | `SessionService` — never the queue |
| **Gate/apply decisions** | `ControlPlane` — never the queue |
| **Model/provider access** | `ModelProvider` via runtime/orchestration — never the queue |
| **Execution episode/tool loop** | `ExecutionHarness` via `OrchestratorAgent` — never the queue |

The queue is a transport for `session_id`s. It does not store payloads, does not encode workflows, does not parse outputs. If your queue dies, the SoT in `SessionStore` is untouched.

## 3. Protocol

```python
class QueueMessage(BaseModel):
    session_id: str
    enqueued_at: datetime
    attempt: int = 0
    metadata: dict[str, Any] = {}

class QueueCapability(StrEnum):
    ENQUEUE = "enqueue"
    DEQUEUE = "dequeue"
    ACK = "ack"
    NACK = "nack"
    PENDING = "pending"
    CLOSE = "close"

@dataclass(frozen=True)
class QueueCapabilities:
    features: frozenset[QueueCapability]

    @property
    def supports_pollable_delivery(self) -> bool: ...

class QueueAdapter(Protocol):
    name: str
    capabilities: QueueCapabilities

    async def enqueue(self, session_id: str, *, metadata: ... = None) -> QueueMessage: ...

    async def dequeue(self, *, timeout: float | None = None) -> QueueMessage | None:
        """Block up to ``timeout`` seconds; return None on timeout."""

    async def ack(self, message: QueueMessage) -> None: ...
    async def nack(self, message: QueueMessage, *, retry: bool = True) -> None: ...

    async def pending(self) -> int: ...
    async def close(self) -> None: ...
```

The protocol is intentionally minimal. Visibility-timeout, dead-letter queues, priority lanes — all implementation concerns of specific adapters.
The capability declaration separates enqueue-only dispatch adapters from
pollable delivery transports. `QueueExecutionHost` requires the pollable
delivery capability (`dequeue`, `ack`, and `nack`) before it starts a worker
cycle.

## 4. Built-in Adapters (P0)

| Adapter | Module | Capabilities | Purpose |
|---|---|---|---|
| `InMemoryQueueAdapter` | `hydramind.queue.in_memory` | enqueue + pollable delivery | Single-process FIFO with async lock; tests + small demos |
| `CeleryQueueAdapter` | `hydramind.queue.celery_adapter` | enqueue-only | Wraps a Celery app dispatcher; lazy import via `hydramind[celery]` extras |
| `RedisStreamQueueAdapter` | `hydramind.queue.redis_stream` | enqueue + pollable delivery | Redis Streams consumer-group adapter with receive/ack/nack, visibility reclaim, stale-handle protection, DLQ evidence, and lazy `hydramind[redis]` dependency |

The Celery adapter is the **only** place in the framework that knows about Celery primitives — same isolation discipline as harness/LLM SDKs.
It publishes `session_id` work into a Celery app, but it is not a
`QueueExecutionHost` polling transport.
The Redis Streams adapter is the first broker-backed pollable transport. It
uses consumer groups for delivery, `XAUTOCLAIM`-style visibility reclaim for
stalled in-flight messages, per-delivery tokens so stale handles become no-ops
after redelivery, and a separate dead-letter stream for max-attempt overflow.
It also exposes explicit dead-letter replay methods for requeueing selected
dead-lettered session ids with replay evidence. It still carries only
`session_id` plus delivery metadata and it keeps the Redis dependency inside
`hydramind.queue`.

## 5. Worker Wiring (typical pattern)

The framework keeps process supervision outside the core. Library users can wire
a polling host in a few lines:

```python
queue = InMemoryQueueAdapter()
orchestrator = OrchestratorAgent(harness=..., control=..., workflow=...)
host = QueueExecutionHost(
    queue=queue,
    orchestrator=orchestrator,
    worker_id="worker-1",
)

async def worker_loop() -> None:
    while True:
        await host.run_once(timeout=5.0)
```

For runtime code that wants a bounded loop primitive instead of hand-rolling the
`while True`, `QueueExecutionHost.run_loop()` repeatedly calls the same
`run_once()` path and returns aggregate evidence (`WorkerLoopResult`):
iterations, deliveries, acked messages, delivery errors, idle cycles, stop
reason, timestamps, and the last `WorkerRunResult`. Operators can bound the
loop with `max_iterations` or `max_idle_cycles`; supervisors can also pass a
`stop_requested` callable so signal or service-manager shutdown is observed
between polling cycles without interrupting in-flight ack/nack handling.

The CLI exposes the same bounded process layer for explicit session ids and for
pollable broker queues. Explicit-session mode remains local/transient:
`hydramind worker loop <workflow.yaml> --session-id <id> ...
--max-idle-cycles 1` and `hydramind worker goal-loop --session-id <id> ...
--max-idle-cycles 1` enqueue the provided ids into a transient local queue.
Broker mode selects a pollable queue adapter:
`hydramind worker loop <workflow.yaml> --queue redis --queue-redis-url
redis://... --max-idle-cycles 1` and `hydramind worker goal-loop --queue
redis --queue-redis-url redis://... --max-idle-cycles 1` consume session ids
from Redis Streams. Both modes invoke `QueueExecutionHost.run_loop()` and print
`WorkerLoopResult` JSON.

For foreground daemon-adjacent operation, the CLI also exposes Redis-backed
signal-aware commands:

```bash
hydramind worker daemon <workflow.yaml> \
  --queue redis --queue-redis-url redis://localhost:6379/0 \
  --session-store sqlite --store-path var/hydramind.sqlite

hydramind worker goal-daemon \
  --queue redis --queue-redis-url redis://localhost:6379/0 \
  --session-store sqlite --store-path var/hydramind.sqlite
```

`worker daemon` and `worker goal-daemon` require a pollable Redis queue, install
temporary SIGINT/SIGTERM handlers, poll with a finite timeout so shutdown can be
observed, and print final `WorkerLoopResult` JSON. They may accept
`--max-iterations` for controlled runs and tests, but do not require a stop
bound. They remain foreground worker processes, not a background fork, process
supervisor, restart policy, distributed lock manager, or worker pool.

For read-only operator liveness checks, the CLI exposes the same host health
snapshot without requiring a workflow YAML, session id, or orchestrator:

```bash
hydramind worker health \
  --queue redis --queue-redis-url redis://localhost:6379/0
```

The command constructs the Redis queue adapter, calls `QueueExecutionHost.health()`,
prints `WorkerHealthSnapshot` JSON, and closes the adapter. The snapshot is
transport evidence only (`pending`, `in_flight`, and `dead_letters` when the
adapter exposes them). It does not prove workflow correctness, process
supervisor state, distributed worker membership, or a production SLA.

The same host also accepts a goal-driven orchestrator, because the worker
contract is the narrow `run_session(session_id, ...)` surface rather than a
workflow-specific class. A queued goal session is reconstructed from the stored
`ExecutionPlan`; a queued workflow session is reconstructed from its recipe
blueprint.

The pattern is **pollable-transport agnostic**: swap `InMemoryQueueAdapter` for
`RedisStreamQueueAdapter` or another adapter that declares pollable delivery
and the host/orchestrator code does not change. `CeleryQueueAdapter` is
intentionally enqueue-only; using it with `QueueExecutionHost` fails closed
before polling begins.
`QueueExecutionHost` supplies worker identity to the orchestrator, which opens a
control-owned execution lease before model/tool work and heartbeats that lease
while a node invocation is running. The queue message still contains only
`session_id`; lease authority stays on `NodeExecution`.
Each `run_once()` returns delivery evidence: queue name, message handle, queue
attempt, worker id, delivery action (`ack`, `nack_retry`, `nack_drop`, or
`idle`), timestamps, duration, retry flag, and error type when a delivery
exception occurs. The host also exposes a read-only `health()` snapshot for
queue liveness (`pending`, and adapter-provided `in_flight` / `dead_letters`)
without reading or mutating `RuntimeSession`.
When a delivery is retried after a worker loses its lease, the orchestrator asks
the control plane to recover expired leased `RUNNING` executions before checking
ready nodes. The old execution remains in history as `ABORTED`, and the node is
returned to `QUEUED` for a fresh worker-owned execution.
`InMemoryQueueAdapter` can optionally redeliver unacked messages after a
visibility timeout and dead-letter messages after a configured delivery limit;
retry and dead-letter messages carry delivery metadata such as the retry reason,
source handle, delivery attempt, and dead-letter reason. `RedisStreamQueueAdapter`
provides the same adapter-native delivery evidence for Redis Streams, including
visibility reclaim and stale-handle protection.
For operator recovery, `RedisStreamQueueAdapter.replay_dead_letter()` requeues a
selected dead-letter `QueueMessage` to the main stream and records replay
metadata such as the dead-letter stream, source handle, original attempt, and
replay count. `replay_dead_letters(limit=...)` performs bounded stream-order
bulk replay. Replay defaults to resetting queue attempts to zero, can preserve
attempts for forensic workflows, and can either remove or retain the
dead-letter stream entry. These operations are queue-local and never read or
mutate `RuntimeSession`.
The same queue-local recovery surface is available through the runtime API and
CLI for Redis-backed operators:

```bash
hydramind worker dead-letters list \
  --queue redis --queue-redis-url redis://localhost:6379/0 --limit 10

hydramind worker dead-letters replay \
  --queue redis --queue-redis-url redis://localhost:6379/0 --limit 1
```

List is read-only. Replay is explicitly bounded by `--limit`, requeues only
session-id messages, and returns replay `QueueMessage` evidence. It is not a
workflow repair engine, side-effect compensation mechanism, retention policy,
approval workflow, or automatic daemon behavior.

`scripts/redis_live_acceptance.py` is the opt-in live proof for this adapter.
It starts a local `redis-server` process, uses the real `redis.asyncio` client
through `RedisStreamQueueAdapter(url=...)`, and verifies cross-client
visibility reclaim, stale-handle protection, dead-letter replay, and
`QueueExecutionHost` recovery of an expired control-owned worker lease. Run it
with:

```bash
uv run --extra dev --extra redis python scripts/redis_live_acceptance.py
```

The default unit suite remains service-free; live Redis acceptance is an
operator/checkpoint command.

## 6. Ownership Invariants

1. Queue messages carry only `session_id`. Workflow state lives in `SessionStore`.
2. Queue adapters never call `SessionService.create_session` — the caller does that before enqueuing.
3. Queue adapters never inspect `RuntimeSession` to decide retries — retries are about *delivery*, not workflow logic.
4. The orchestrator does not directly use a queue. The worker loop does.
5. Worker identity and lease token are runtime-host context, not queue-message state.
6. Queue delivery evidence is transport evidence, not workflow ownership; it must not contain workflow payloads or prompts.

## 7. What's Out of P0

- **Process-supervised worker daemon** — P1; the library-level
  `QueueExecutionHost.run_loop()` plus CLI bounded loops and foreground
  signal-aware Redis daemon commands now supply repeated polling, aggregate
  evidence, and graceful stop hooks. Backgrounding, restart policy, pid files,
  service-manager integration, and worker pools remain later work.
- **Priority lanes** — P2; FIFO only in P0.
- **Production dead-letter automation and retention policy** — P2; Redis
  Streams now has adapter-level and CLI-level manual dead-letter list/replay
  operations, but daemon automation, retention windows, audit export, and
  approval workflows remain operator runbook concerns.
- **Distributed lock for multi-worker correctness** — P1, builds on worker-host
  lease enforcement and versioned/CAS `SessionStore` writes. The S71 live Redis
  acceptance proves local broker visibility and worker lease recovery against a
  real Redis server; full distributed lock/CAS semantics and broader
  operational guidance remain later work.
- **Additional pollable production brokers** — P2; Redis Streams covers the
  first pollable broker adapter. Celery remains enqueue-only dispatch, not
  broker-native receive/ack/nack visibility semantics for `QueueExecutionHost`.

## 8. Heritage Diff vs Reference Project

| Reference (`short-video-maker`) | HydraMind P0 | Action |
|---|---|---|
| `task_queue.py` + `celery_app.py` (~200 LOC together) | `hydramind.queue` (~150 LOC) | Same separation, behind a Protocol |
| Hard-coded Redis broker | Pluggable adapter | Transport-neutral |
| `queued_task_execution_host.py` | `QueueExecutionHost` + `hydramind worker once` | Same session-id-only worker boundary, no queue-owned runtime state |
