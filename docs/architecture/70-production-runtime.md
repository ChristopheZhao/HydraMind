# 70 — Production Runtime Closure

> S7 closes the gap between a runnable reference demo and a production-credible local runtime. The central rule remains unchanged: `RuntimeSession` is Source of Truth and only `SessionService` mutates it.

## 1. Runtime Surfaces

| Surface | Command/API | Purpose |
|---|---|---|
| Immediate run | `hydramind run <workflow.yaml>` | Create a session and run it in-process |
| Durable run | `hydramind run <workflow.yaml> --session-store sqlite --store-path var/hydramind.sqlite` | Persist `RuntimeSession` across process restarts |
| Queue handoff | `hydramind run <workflow.yaml> --enqueue-only --session-store sqlite --store-path var/hydramind.sqlite` | Create a queued session without executing it |
| Broker queue handoff | `hydramind run <workflow.yaml> --enqueue-only --queue redis --queue-redis-url redis://localhost:6379/0 --session-store sqlite --store-path var/hydramind.sqlite` | Create a queued session and publish its session id to Redis Streams |
| Worker host | `hydramind worker once <workflow.yaml> --session-id <id> --session-store sqlite --store-path var/hydramind.sqlite` | Consume one session id through the worker boundary |
| Bounded worker loop | `hydramind worker loop <workflow.yaml> --session-id <id> --session-id <id2> --max-idle-cycles 1 --session-store sqlite --store-path var/hydramind.sqlite` | Consume explicit queued session ids through a bounded local process loop and print aggregate `WorkerLoopResult` evidence |
| Broker worker loop | `hydramind worker loop <workflow.yaml> --queue redis --queue-redis-url redis://localhost:6379/0 --max-idle-cycles 1 --session-store sqlite --store-path var/hydramind.sqlite` | Consume Redis Stream session-id messages through the same bounded worker loop and print aggregate `WorkerLoopResult` evidence |
| Worker daemon | `hydramind worker daemon <workflow.yaml> --queue redis --queue-redis-url redis://localhost:6379/0 --session-store sqlite --store-path var/hydramind.sqlite` | Run a foreground Redis-backed workflow worker that handles SIGINT/SIGTERM through the worker loop stop hook and prints final `WorkerLoopResult` evidence |
| Goal worker daemon | `hydramind worker goal-daemon --queue redis --queue-redis-url redis://localhost:6379/0 --session-store sqlite --store-path var/hydramind.sqlite` | Run a foreground Redis-backed goal worker, inheriting queued goal runtime overrides such as memory-store binding |
| Queue health | `hydramind worker health --queue redis --queue-redis-url redis://localhost:6379/0` | Inspect Redis Stream transport liveness counts (`pending`, `in_flight`, `dead_letters`) without running a worker or touching `RuntimeSession` |
| Worker readiness | `hydramind worker readiness --queue redis --queue-redis-url redis://localhost:6379/0 --session-store sqlite --store-path var/hydramind.sqlite` | Preflight queue pollability and session-store durability for distributed worker launch suitability without reading or mutating queue/session state |
| Dead-letter recovery | `hydramind worker dead-letters list --queue redis --queue-redis-url redis://localhost:6379/0` / `hydramind worker dead-letters replay --queue redis --queue-redis-url redis://localhost:6379/0 --limit 1` | Inspect or manually replay bounded Redis dead-letter messages as queue-local recovery evidence, without touching `RuntimeSession` |
| Env check | `hydramind doctor env --env-file .env --include-missing-template` | Verify required provider/tool keys are present without printing values |
| Provider smoke | `hydramind doctor providers --env-file .env --roles planner,executor` | Verify configured LLM provider routes without logging secrets |
| Tool smoke | `hydramind doctor tools --env-file .env --tool search.web,artifact.write_json,artifact.read_json,artifact.write_text,artifact.read_text,artifact.exists,artifact.list,time.now` | Verify registered tools and optional live tool APIs |
| Goal run | `hydramind goal "<objective>" --quality-contract <path.json> [--enable-semantic-verifier] [--memory-store sqlite --memory-store-path var/memory.sqlite]` | Run a goal with a typed `GoalArtifactQualityContract` (length, required sections, refs, images, local-asset containment) and optional opt-in semantic rubric verification through the active `ExecutionHarness` path. The contract is persisted on the queued session and the worker `goal-once` path rebuilds the deterministic verifier composite from session metadata, with semantic verification reactivated by `--enable-semantic-verifier` on the worker. Explicit memory-store binding lets planner/executor memory-context retrieval and opt-in memory observers use a durable store across a queued worker process boundary. |
| Production blog scenario | `bash examples/scenarios/mas-production-blog/run_scenario.sh` | Drive the `hydramind goal` main path against a published `quality_contract.json` + semantic rubric for the ~20k-char Chinese MAS production blog; pair with `examples/scenarios/mas-production-blog/evidence_collector.py` for a redaction-safe evidence package. See `docs/operations/env-and-live-smoke.md` §9. |

## 2. SQLite Session Store

`SqliteSessionStore` persists the complete `RuntimeSession` Pydantic model as JSON in a `runtime_sessions` table. This is deliberately boring:

- `SessionService` remains the only mutation owner.
- Queue and worker code only pass `session_id`.
- Schema migrations are deferred until the JSON envelope proves stable.
- Operators can inspect status and update timestamps without parsing JSON.

This mirrors the reference projects' lesson: persistence is a runtime backbone, but it must not re-own orchestration semantics.

## 2.1 Memory Store Binding

Goal runtime memory is configured separately from session persistence:

- Python callers can pass any `MemoryStore` implementation directly.
- CLI/runtime callers can select `--memory-store memory` or
  `--memory-store sqlite --memory-store-path <path>`.
- Extension code can register a process-local memory store kind with
  `register_memory_store(kind, builder)` before runtime assembly, then use that
  kind anywhere `create_memory_store(kind, path)` is used.
- Queued goal sessions persist only the serializable memory-store kind/path in
  `RuntimeSession.metadata.runtime_overrides`; queue messages still carry only
  `session_id`.
- `worker goal-once`, `worker goal-loop`, and `worker goal-daemon` inherit the
  persisted binding unless explicit worker memory-store flags override it.

The binding is a runtime assembly concern. Planner/executor code receives only a
`MemoryContextRetriever`, and memory observers receive only a `MemoryStore`.
Retrieved entries remain prompt context and are not copied into session state,
plan metadata, node outputs, or queue payloads.
Custom registry entries are process-local: every worker process that should use
a custom `memory_store_kind` must register that kind before constructing the goal
runtime bundle.

## 2.2 Tool Runtime Assembly

Goal and workflow runtime tool dependencies are assembled at the runtime edge by
`hydramind.runtime_tools`. The runtime entrypoint delegates construction of
`ExecutionEnvironment`, the default `ToolRegistry`, default network host policy,
and workflow-scoped `WorkflowToolProvider`; it consumes prepared dependencies
rather than owning concrete tool registry setup. This keeps tool policy and
least-privilege behavior in the tools/runtime-tool boundary while preserving the
existing public runtime flags.

Workflow YAML remains least-privilege: nodes receive no tools unless they
declare an allowlist with top-level `tools` or `config.tools`. The workflow tool
provider validates declared names during runtime assembly and fails closed for
unknown tools.

## 3. Worker Host

`QueueExecutionHost` owns the worker-side loop for adapters that declare
pollable delivery capability:

1. `QueueAdapter.dequeue()` returns a `QueueMessage` carrying only `session_id`.
2. `OrchestratorAgent.run_session(session_id)` drives the session through control/gating.
3. The host `ack`s completed, failed, or waiting-gate workflow outcomes.
4. The host `nack`s delivery exceptions for retry.
5. The host returns `WorkerRunResult` delivery evidence: queue name, message
   handle, queue attempt, worker id, delivery action, timestamps/duration, retry
   flag, and error type.
6. The host exposes `health()` and public `hydramind.runtime.queue_health()` for
   queue liveness (`pending`, and optional adapter-native `in_flight` /
   `dead_letters`) without touching `RuntimeSession`. CLI `worker health`
   exposes that read-only snapshot for Redis queues; it is transport evidence,
   not workflow correctness, supervisor state, distributed worker membership, or
   an SLA claim.
7. Public `hydramind.runtime.worker_readiness()` and CLI `worker readiness`
   expose read-only preflight evidence for worker launch configuration. The
   snapshot is derived from declared queue capabilities and session-store
   kind/path only: Redis Stream pollability plus a persistent SQLite
   CAS-capable session store is `ready=true`, while in-memory queue or session
   storage remains local-only/not-ready for distributed workers. It does not
   call `dequeue()`, `pending()`, `ack()`, `nack()`, or any `SessionStore`
   method, so it is not a live broker test, cluster membership check, workflow
   correctness proof, throughput claim, or SLA.
8. The host also exposes `run_loop()` for repeated polling through the
   same `run_once()` path. `WorkerLoopResult` aggregates iterations, deliveries,
   acked messages, retry/drop nack delivery actions, delivery errors, idle
   cycles, stop reason, timestamps, and the last run result without storing
   unbounded history. It also carries
   supervisor-facing exit evidence: `exit_code=0` /
   `restart_recommended=false` for clean bounded or requested stops, and
   `exit_code=1` / `restart_recommended=true` when delivery errors occurred.
   The public `worker_loop_exit_contract()` helper derives the same contract for
   library callers. The loop can stop on max-iteration/max-idle bounds or a
   caller-supplied `stop_requested` hook.
9. CLI `worker loop` and `worker goal-loop` expose a bounded process wrapper over
   explicit session ids or a configured pollable Redis queue. Explicit-session
   mode uses a transient local queue; `--queue redis --queue-redis-url ...`
   consumes Redis Stream messages. Both modes exercise the same host loop and
   require `--max-iterations` or `--max-idle-cycles`; they are not an infinite
   daemon, process supervisor, distributed lock manager, or worker pool.
10. CLI `worker daemon` and `worker goal-daemon` expose foreground Redis-backed
   daemon wrappers over the same host loop. They require `--queue redis`, install
   temporary SIGINT/SIGTERM handlers, poll with a finite timeout so shutdown can
   be observed, and print final `WorkerLoopResult` JSON. They may run without a
   max bound, but they remain foreground commands rather than a background
   process supervisor or worker pool. CLI loop/daemon return codes come from the
   runtime-owned `WorkerLoopResult.exit_code`, so external supervisors can use a
   documented process outcome rather than a CLI-local convention.

The worker never edits `RuntimeSession` directly. A failed workflow is not a failed delivery; it is an acknowledged runtime outcome.
`InMemoryQueueAdapter` supports local visibility-timeout redelivery, stale-handle
ack protection, and delivery metadata on retry/dead-letter messages. The Celery
adapter remains enqueue-only and is rejected by `QueueExecutionHost` before the
host calls `dequeue()`. Pollable production workers must use an adapter that
declares and implements broker-native dequeue/ack/nack visibility semantics.
`RedisStreamQueueAdapter` is the built-in broker-backed pollable adapter: it
uses Redis Streams consumer groups, visibility reclaim, delivery-token
stale-handle protection, retry metadata, and a dead-letter stream while keeping
queue payloads limited to `session_id`. The adapter also has queue-local
dead-letter replay methods for requeueing selected or bounded batches of
dead-lettered session ids with replay evidence. Public
`queue_dead_letters()` / `replay_queue_dead_letters()` helpers and CLI
`worker dead-letters list|replay` expose those operations for Redis queues.
Those methods do not inspect or mutate `RuntimeSession`; the worker host still
drives recovered sessions through normal control-owned leases and gates after
the message is dequeued.
For opt-in live Redis acceptance, run
`uv run --extra dev --extra redis python scripts/redis_live_acceptance.py`.
That script starts a local Redis server process and verifies real-client
visibility reclaim, stale-handle protection, DLQ/replay, and worker-host
recovery of an expired control-owned lease.

## 4. Doctor Commands

Doctor commands are operational smoke checks, not unit tests:

- They load `.env` but never print key values.
- Environment diagnostics only report key names and boolean presence; the optional missing template contains empty `KEY=` lines only.
- Provider diagnostics report role, provider, model id, stop reason, and short content preview.
- Tool diagnostics report registry stats, schema health, and structured tool results.
- Tool environment requirements are declared by registered tools and surfaced through `ToolRegistry.env_requirements()`, so adding a live tool also updates env diagnostics.
- `doctor tools --live-tools` preflights required env even when no explicit `--tool` is selected, preventing empty live checks from passing.
- Dry-run remains the default for tools; live tool APIs require `--live-tools`.
- Built-in tools cover web search, image generation, JSON/text artifact read/write, artifact existence/listing, and UTC timestamping.
- `process.run` remains approval-gated and bounded by command allowlisting,
  optional argv-prefix allowlisting, artifact-root cwd containment, no shell
  invocation, scoped env, timeout limits, and output caps. It is not an OS or
  container sandbox.
- Live network tools use bounded request timeouts (`BRAVE_SEARCH_TIMEOUT_SECONDS`, `DOUBAO_IMAGE_TIMEOUT_SECONDS`) so diagnostics fail instead of hanging indefinitely.
- Live HTTP failures return structured `ToolExecutionResult` errors with provider, live flag, timeout, error type, and HTTP status code when available.

Live failures should be treated as diagnostics to investigate account, network, model id, or provider compatibility. They should not be papered over as green CI.

## 5. Verification Profile

S7 minimum local verification:

```bash
uv sync --extra dev --extra celery --link-mode=copy
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest --cov=src/hydramind --cov-report=term-missing --cov-fail-under=80 -q
.venv/bin/ruff check src tests examples
.venv/bin/mypy src/hydramind
.venv/bin/hydramind run examples/short_video/workflow.yaml --provider mock --input topic=Python --session-store sqlite --store-path /tmp/hydramind.sqlite
.venv/bin/hydramind doctor env --env-file .env --include-missing-template
```

Optional live verification:

```bash
.venv/bin/hydramind doctor providers --env-file .env --roles planner,executor --prompt "Reply with OK."
.venv/bin/hydramind doctor tools --env-file .env --tool search.web,artifact.write_json,artifact.read_json,artifact.write_text,artifact.read_text,artifact.exists,artifact.list,time.now
.venv/bin/hydramind doctor tools --env-file .env --live-tools --tool search.web,image.generate
```

See `docs/operations/env-and-live-smoke.md` for the operator runbook that defines secret placement, pass conditions, and the S7 checkpoint rerun order.

## 6. Remaining Production Edges

S7 is local-production closure, not a distributed control-plane release. Remaining later work:

- Distributed Redis worker correctness beyond the local S71 acceptance proof.
  Worker readiness now reports launch preflight evidence for pollable broker
  queues and persistent CAS-capable session storage, but full multi-host
  correctness, deployment topology, and operational guidance remain later work.
- Process-supervised worker daemon behavior around the library-level worker
  loop, including backgrounding, service-manager integration, and worker pools.
  The worker loop now emits exit/restart evidence for external supervisors, but
  HydraMind still does not manage OS processes or restart loops itself.
- Additional pollable production broker adapters; Celery remains enqueue-only.
- Production dead-letter automation/retention policy, approval workflow, audit
  export, and priority lanes. Manual bounded Redis dead-letter list/replay is
  available through the worker CLI.
- SQL schema migration strategy beyond JSON envelope storage.
- Cross-harness OpenTelemetry traces.

The S52d production-blog scenario (see `examples/scenarios/mas-production-blog/`) validates the quality-contract + semantic-verifier path end to end, but explicitly does NOT claim distributed-worker correctness, durable replay/recovery, OS or container sandboxing, or production broker visibility SLAs — those remain the items above.
