# HydraMind Architecture — Overview

> Four-layer harness MAS framework. This document is the index; deep designs live in `10-…`, `20-…`, etc.

> Current note (2026-06-19): provider/model access and the replaceable execution
> shell are now distinct. `ModelProvider` owns provider calls and routing;
> `ExecutionHarness` owns the agent execution episode. The retired
> `HarnessBackend` page is historical only; see
> [`10-execution-harness.md`](10-execution-harness.md) and ADR-0011.

## 1. Design Stance

HydraMind takes four positions that distinguish it from existing MAS frameworks (LangGraph, AutoGen, CrewAI, Letta, Google ADK):

1. **Harness ≠ Framework, and provider ≠ harness.** The MAS framework orchestrates **on top of** a replaceable agent execution shell (context/memory policy, tool loop, permissions, trace/evidence, budget/recovery, subagent strategy). That shell is `ExecutionHarness`, and it is distinct from *model/provider selection* (`LLMProvider`/`ModelProvider`). Provider switching proves routing flexibility; harness replacement is proven by swapping `HydraMindExecutionHarness` with `ExplicitSubmitExecutionHarness` while holding provider, tools, control, gates, and orchestration fixed. External Agent SDK adapters (Claude SDK, OpenAI Agents SDK) are not a near-term core direction.
2. **Goal-driven, workflow-compatible.** The primary user input is a `GoalSpec`, which a planner turns into an `ExecutionPlan`. YAML workflows remain useful recipes and test fixtures, but they are no longer the conceptual source of planning truth.
3. **Agent–Queue Decoupling.** Runtime state is a typed SoT (`RuntimeSession`) owned by the control layer. The queue (`QueueAdapter`) only handles eligibility scheduling — no business logic, no orchestration state in the worker pool.
4. **Gating and Feedback as First-Class Contracts.** Quality gates, policy gates, HITL gates, timeout gates, verifier results, and task feedback are typed artifacts. They are not optional callbacks bolted onto a workflow — they are how state transitions and repair/replan decisions are authorized.

## 2. Four-Layer Harness Architecture

```
Governance      ─ contracts, replay/eval evidence, audit, closeout criteria
Orchestration   ─ goal planning, task topology, subagent coordination, harness/tool loop driving
Control         ─ narrow execution control + RuntimeSession SoT mutation
Gating          ─ GateEvaluator + GateResult + GateContract
Runtime         ─ queue/worker/session-store mechanics, never semantic SoT ownership
Harness         ─ ExecutionHarness: replaceable execution episode shell
Provider        ─ ModelProvider: provider/model routing and model calls
```

The **four semantic layers** — Governance, Orchestration, Control, Gating — are
what "four-layer" names: they hold the reasoning, state ownership, and policy.
They run on the replaceable **Harness** substrate and lean on the **Runtime**
mechanics row, neither of which owns semantic state. The agent-native kernel
(`Agent` / `Message` / `Team`, ADR-0007/0009) is the execution unit *within* the
Orchestration and Control layers, not a separate layer.

**Single-ownership rules** (avoid the dual-mainline bug we saw in the reference project):

| Surface | Owner | Notes |
|---|---|---|
| Runtime state mutation | Control layer (`SessionService`) | Orchestrator/planner decide, Control writes |
| Model invocation | Provider layer (`ModelProvider`) | Orchestration & Control never `import anthropic` |
| Agent execution episode | Harness layer (`ExecutionHarness`) | Multi-turn loop, tools, trace/evidence, recovery strategy |
| Workflow eligibility | Queue layer (read-only on SoT) | Queue may check, never mutate |
| Policy enforcement | Gating layer | Other layers consume `GateResult` |
| Detailed trajectory | Observability/Governance | Runtime and orchestration emit events; trace is evidence, not SoT |
| Episodic summaries | Memory projector | Derived from trace; stores summaries plus `trace_id` / `execution_id` references |
| Lifecycle of plans | Governance (SDD plan index via `sdd-plan-maintainer`, kept in maintainers' internal dev-notes) | Out of runtime; managed by skill |

## 3. Cross-Cutting Subsystems

- **Memory** (`hydramind.memory`) — Layered: short-term working memory plus long-term episodic snapshots. Episodic memory is a trace-derived summary store, not raw trajectory SoT.
- **Observability** (`hydramind.observability`) — Standard event types, trace correlation fields, JSONL/list/log/OTel observers, redaction helpers, and trajectory evidence for model/tool/control events.
- **Governance** (`hydramind.governance`) — Release evidence, replay package, and evaluation result contracts for audit/release workflows. Contract layer only; no P0 auto-evaluation engine.
- **Production runtime** (`hydramind.runtime`, `hydramind.runtime_worker`) — Goal/workflow helpers, queued goal sessions, SQLite `RuntimeSession` store, queue execution host, and doctor commands.
- **Configuration** — `GoalSpec`/`ExecutionPlan` for primary runtime input, YAML for reusable workflow recipes, environment for secrets. Prompts and schemas are config, not code.

## 4. Public API Surface

HydraMind keeps the package root intentionally small: `hydramind.__version__`
is the only root export. Framework users import stable alpha contracts from
the layer namespaces:

| Namespace | Public role |
|---|---|
| `hydramind.mas` | Native MAS contracts (`AgentSpec`, `TeamSpec`, collaboration protocol, workspace) |
| `hydramind.control` | `RuntimeSession` data contracts, `SessionService`, `ControlPlane`, apply intents, state enums |
| `hydramind.gating` | Gate contracts, registry, and built-in gate evaluators |
| `hydramind.orchestration` | Goal/workflow planning, orchestration agents, verifiers, memory-context contracts, `ExecutionHarness` implementations |
| `hydramind.queue` | Session-id-only queue protocol and built-in adapters |
| `hydramind.runtime` / `hydramind.runtime_worker` | Runtime assembly helpers, queued-session helpers, worker result/liveness contracts |
| `hydramind.harness` | `LLMProvider`/`ModelProvider`, provider routing, and vendor-agnostic model wire types |
| `hydramind.tools` | Tool registry, policy, execution environment, and built-in tool registration |
| `hydramind.memory` / `hydramind.observability` | Memory stores/projectors and typed event observers |
| `hydramind.governance` | Release/replay/evaluation evidence contracts |

Each public namespace carries an explicit `__all__` snapshot enforced by
contract tests. Internal helpers, legacy collaboration projections such as raw
`subagent_group`, transport-specific implementation details, and prompt
rendering internals are not root-level public API.

## 5. Heritage & Diff vs Reference Project

HydraMind distills the four-layer architecture from a production-aged short-video-generation MAS that has been iterated for ~6 months. The heritage maps as:

| Reference module | HydraMind module | Generalization |
|---|---|---|
| `backend/app/services/runtime_session_service.py` | `hydramind.control.session` | Drop video-specific fields, keep state machine + SoT semantics |
| `backend/app/services/orchestration_control_plane.py` | `hydramind.control.plane` | Same; gate/apply loop is generic |
| `backend/app/services/audio_delivery_gate_evaluator.py` | `hydramind.gating.evaluators.example` | Become reference impl of `GateEvaluator` |
| `backend/app/agents/orchestrator.py` | `hydramind.orchestration.agent` | Generic `OrchestratorAgent`; prompts externalized |
| `backend/app/agents/memory/**` | `hydramind.memory.**` | Direct migration; interfaces already clean |
| `backend/app/task_queue.py` + `celery_app.py` | `hydramind.queue.*` | Behind `QueueAdapter` interface |

Known abstraction debts from the reference project (`mas_architecture_deviation_inventory_20260323.md`) are addressed by:
- Single-mainline design (orchestrator owns, control serves) — no dual `episode_orchestrator.py` parallel path.
- Prompt/schema externalized from day one.
- `ModelProvider` plus the harness package boundary prevent the `import anthropic` leak that the reference project had to refactor away; `ExecutionHarness` remains vendor-SDK-free.

## 6. Subsequent Documents

| Doc | Status |
|---|---|
| `10-harness-backend.md` | Historical tombstone · superseded by `10-execution-harness.md` |
| `10-execution-harness.md` | Current provider/harness split and replaceability contract |
| `20-control-plane.md` | Landed (P0-S2) |
| `30-gating.md` | Landed (P0-S3) |
| `40-orchestration.md` | Landed (P0-S4) |
| `50-memory-and-observability.md` | Landed (P0-S5) |
| `60-queue-adapter.md` | Landed (P0-S6) |
| `70-production-runtime.md` | Landed (P0-S7) |
| `80-governance.md` | Landed (P0-S9) |
| `90-decisions.md` (ADR log) | Landed (P0-S9) |
| `95-execution-harness-correction.md` | Corrective anchor (2026-06-18) |

## 7. Non-Goals (P0)

- Framework-core Web UI / Dashboard. External examples may render trace artifacts for operator/demo inspection.
- Auto-evaluation engine or replay service (P3+ if at all; P0 ships typed governance contracts only)
- Built-in vector stores / RAG layer (use external; only memory abstractions ship)
