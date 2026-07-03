# 95 — MAS / Execution Harness Architecture Correction Basis (SUPERSEDED / HISTORICAL)

> **Status: SUPERSEDED where it conflicts — historical record, no longer the live
> forward basis.** This was the Phase-0/1 correction basis; its layering stance is
> now superseded by **ADR-0012** (`90-decisions.md` — narrow harness: the harness is
> a data-plane executor, spawn is orchestration) and by
> **`96-agent-layering-and-harness-synthesis.md`** (the active layering vocabulary).
> Retained as HISTORY: the F1–F11 implementation facts (§5) and the Class 1–5
> acceptance taxonomy (§9) remain accurate and were the basis the refactor executed
> against. Do NOT cite it as the live next-stage basis — for the current stance read
> ADR-0012 + `96` first. (It still supersedes the older reading of `HarnessBackend`
> as the replaceable harness; that type was itself later retired — ADR-0010/0011,
> `96` §9.)
>
> **What `96` / ADR-0012 OVERRIDE here (read `96` for the authoritative form):**
> - This doc (§2, §4, §7-Phase-2, §8) places sub-agent + team **spawn / turn strategy
>   INSIDE the replaceable harness**. Under the narrow harness (ADR-0012; `96` §3 /
>   §4.2) the **spawn / instantiation ACT is orchestration-owned**; the harness owns
>   sub-agent *configuration / policy* and only **emits a typed delegation request** —
>   it never spawns. Realized by PLAN-20260622-001 (`SubagentSpawner`).
> - The "broad execution shell owns subagents" reading is reconciled as
>   *configuration*, not runtime instantiation (`96` §4.2 / ADR-0012 reconciliation).
> - `HarnessBackend` mentions below are **historical**; provider access is now
>   `ModelProvider` and the replaceable shell is `ExecutionHarness`.

## 1. Purpose

This document is the reference basis for the next HydraMind MAS / harness
refactor. It is not a release introduction, not a marketing summary, and not a
small wording cleanup.

The correction target is architectural:

- stop treating provider adapters, external Agent SDK adapters, and mock replay
  as interchangeable "harness backends";
- define the replaceable part as the agent execution shell;
- keep MAS contracts, task/tool/environment/evaluator contracts, provider
  selection, state records, traces, and acceptance cases stable around that
  shell;
- move durable runtime-influencing state behind clear control ownership;
- stop using deterministic mock/replay runs as agent or MAS acceptance evidence.

This document should be used before implementing the next refactor phase. If a
planned change conflicts with this document, either update this document first
with concrete code evidence, or supersede the plan.

## 2. Executive Conclusion

The current implementation contains useful MAS pieces, but the main abstraction
is misnamed and too low-level.

`HarnessBackend` currently behaves like a mixed boundary for:

- chat/model invocation;
- OpenAI-compatible provider routing and wire translation;
- a subagent message seam;
- mock/scripted/replay test execution.

That is not the same as a replaceable execution harness.

Correct meaning:

> HydraMind should fix task, model-provider selection, tools, environment,
> evaluators, MAS contracts, state records, traces, and acceptance cases, then
> allow the execution harness that drives the model through the task to be
> replaced.

The replaceable harness is the execution shell around the model:

- context and memory strategy;
- prompt/message driving policy;
- tool loop and tool permission policy;
- trace and evidence emission;
- budget, timeout, and cost policy;
- verifier/evaluator integration;
- retry, repair, and recovery behavior;
- subagent and team interaction strategy;
  <!-- SUPERSEDED by ADR-0012 / 96 §4.2: the spawn/instantiation ACT is
       orchestration-owned; the harness owns sub-agent CONFIG + emits a typed
       delegation request, it does not spawn. -->
- failure classification and resumability.

Switching DeepSeek to Kimi or GLM is provider selection. It is not harness
replacement. Running the same workflow with `MockBackend` is deterministic
plumbing/replay. It is not agent execution.

## 3. Non-Negotiable Corrections

1. `HarnessBackend` must stop being presented as the core replaceable harness
   abstraction.
2. Provider access must become `LLMProvider` / `ModelProvider` or equivalent.
3. `OpenAICompatibleBackend` cannot be merely renamed to a provider. It must be
   split because it already contains both provider behavior and harness behavior.
4. External Agent SDK support is not a near-term core direction. The current
   Claude SDK implementation is thin and does not currently own HydraMind's tool
   loop or MAS runtime, but expanding it in that direction would conflict with
   HydraMind's own execution harness.
5. `MockBackend` must not remain a normal runtime backend or representative
   acceptance path. Its record/replay functionality may survive as testing
   support, but never as proof of live agent quality.
6. Durable state that affects execution is not limited to `RuntimeSession`.
   Memory, interaction logs, repair counters, and replay/evidence surfaces must
   be classified by whether they can affect later model prompts, scheduling, or
   recovery.
7. Interaction state must become a durable control-owned aggregate before the
   runtime can claim interaction-first MAS execution.
8. Acceptance must report by `task + model/provider + harness + evaluator`, not
   by model alone and not by replay fixture.

## 4. Target Architecture

```text
HydraMind MAS Framework
  Stable contracts
    Goal / task contracts
    AgentSpec / TeamSpec / CollaborationProtocol
    Tools and execution environment contracts
    Evaluators, gates, and acceptance profiles
    Runtime state, interaction state, memory policy, trace schema
    Governance evidence and checkpoint records

Replaceable ExecutionHarness
  Owns execution strategy
    context construction and compaction policy
    memory retrieval policy and prompt injection boundary
    prompt/message driving strategy
    tool loop and tool retry policy
    permission, network, process, and artifact constraints
    subagent/team turn strategy
    trace/evidence/failure attribution
    verifier/evaluator integration
    budget, timeout, and recovery policy

LLMProvider / ModelProvider
  Supplies model calls
    DeepSeek
    Kimi
    GLM
    future OpenAI-compatible endpoints
    future local/self-hosted providers

Testing / Replay Support
  Supplies non-agent deterministic evidence
    replay fixtures
    scripted turns
    contract test doubles
    trace regression utilities
```

Providers are used by the execution harness. Providers do not define the
execution harness. Replay utilities are used by tests. Replay utilities do not
define runtime capability.

> **SUPERSEDED (ADR-0012 / `96` §4.2):** the `subagent/team turn strategy` line in
> the diagram above stays with the harness only as *configuration/policy*. The
> **spawn / instantiation ACT moved to orchestration** (`SubagentSpawner`); the
> harness emits a typed delegation request and never spawns.

## 5. Current Implementation Facts

This section records the current code reality that the refactor must respect.

### F1. `HarnessBackend` Is a Model-Turn Interface, Not an Execution Harness

`src/hydramind/harness/base.py` defines `HarnessBackend.invoke`,
`spawn_subagent`, `compact_context`, and `close`. This is mostly a model-call
and subagent-message seam. It does not own the full execution shell.

Current execution-shell responsibilities are scattered:

- `src/hydramind/orchestration/agent_context.py` builds prompts and memory
  context.
- `src/hydramind/orchestration/agent_execution.py` wraps model invocation and
  trace emission.
- `src/hydramind/orchestration/agent_invocation.py` dispatches direct,
  subagent, and team execution modes.
- `src/hydramind/orchestration/agent_tools.py` owns tool-call draining, allowed
  tool checks, tool ledger writes, trace events, effect reuse, and reasoning
  carry-forward.
- `src/hydramind/runtime_tools.py` assembles execution environment defaults,
  network flags, artifact roots, timeout defaults, and process policy.
- `src/hydramind/orchestration/collaboration_*.py` owns native team member
  execution and interaction logging.
- `src/hydramind/runtime_worker.py` owns queue delivery and worker retry
  mechanics.

Refactor implication: do not "improve HarnessBackend" in place as if it already
were the harness. First introduce the correct `ExecutionHarness` boundary around
an execution episode.

### F2. `OpenAICompatibleBackend` Mixes Provider and Harness Behavior

`OpenAICompatibleBackend` does provider/model routing and chat-completions wire
translation, but it also declares `HarnessCapabilities`, simulates subagents,
stores subagent message context, and maps reasoning/tool behavior into
HydraMind's invocation result.

Concrete implications:

- provider routing belongs under provider/model selection;
- payload serialization and `/chat/completions` transport belong to provider
  implementation;
- subagent handle semantics, context threading, compaction policy, and harness
  capabilities belong to the execution harness layer or to shared execution
  contracts;
- `ModelRouter` currently includes MAS role normalization and domain keywords,
  so it is not a clean provider-only router yet.

Refactor implication: split the class. A pure provider should not expose
`supports_subagents`, `supports_interaction`, or `compact_context`.

### F3. Claude Agent SDK Is a Future Conflict Risk, Not a Current Runtime Owner

The current `ClaudeAgentSDKBackend` is thin:

- `invoke()` composes a prompt and calls `sdk.query()`;
- it returns `tool_calls=()`;
- it does not own HydraMind's tool loop, trace ledger, control state, permission
  checks, or recovery policy;
- comments explicitly push real SDK tool-loop integration to P1.

Therefore, the current code should not be described as "Claude SDK already
owns the MAS runtime." That is false.

The risk is architectural: if a future SDK adapter starts owning sessions, tool
loops, hooks, permissions, tracing, subagents, or memory, it will overlap with
HydraMind's own MAS framework and execution harness.

Refactor implication: remove external Agent SDK adapters from the core roadmap
unless there is a precise integration contract that keeps HydraMind state,
tools, gates, traces, and recovery authoritative.

### F4. `MockBackend` Has Replay Value but No Agent-Acceptance Value

`MockBackend` now supports deterministic fingerprinted record/replay and can
drive offline fixtures. That is useful for:

- contract tests;
- state/control/tool/queue plumbing tests;
- fixture regression;
- replaying known traces;
- deterministic docs/examples where no model is allowed.

It still does not test agent behavior because no live model is making decisions.
It cannot validate:

- prompt adherence;
- semantic output quality;
- live tool-choice reliability;
- model/provider drift;
- cost and latency behavior;
- real model failure recovery;
- multi-agent collaboration quality.

Refactor implication: keep replay only as `ReplayFixture`, `TestDouble`, or
`hydramind.testing` support. Remove it from normal runtime backend selection and
from acceptance language.

### F5. Runtime Is Still Node-First, Not Interaction-First

The top-level runtime still schedules workflow nodes through `RuntimeSession`
and `WorkflowGraph`. Native MAS interaction runs under a workflow node.

`Interaction`, `Turn`, and `Message` exist in the kernel, and
`NativeTeamInteractionRuntime` advances them in memory. But `RuntimeSession`
does not contain durable `Interaction` aggregates. Current interaction logging
is an append-only projection into session metadata, not the authoritative
scheduling state.

Important nuance: interaction events can be control-owned records, but the
kernel `Interaction` aggregate remains orchestration-private and ephemeral.

Refactor implication: Phase 4 is not a rename. It requires additive durable
state, turn-level scheduling/lease/recovery semantics, and migration from
preview logs to authoritative interaction records.

### F6. Memory Is Durable Runtime-Influencing State Outside Control

Goal/agent memory is wired through observability:

- runtime memory assembly attaches `EpisodeProjectorObserver` and
  `AgentTurnMemoryObserver` to the emitter;
- observers write to `MemoryStore`;
- `StoreMemoryContextRetriever` reads that store;
- `AgentPromptContextBuilder` injects retrieved memory into later prompts.

This is durable state that can influence future execution. It bypasses the
single-writer control model if treated as a mere observer side effect.

Refactor implication: the single-writer rule must apply to all durable,
runtime-influencing state, not only `RuntimeSession`. Memory writes that affect
future prompts need an explicit ownership and versioning story.

### F7. `InvocationResult.raw` Has Become an Undeclared Contract

`InvocationResult.raw` is documented as backend-specific debug payload "never
relied on by callers." The tool loop currently reads keys such as:

- `subagent_id`;
- `reasoning_content`.

That turns debug payload into a hidden cross-layer contract. Provider or
harness replacement can silently break reasoning continuity or subagent-origin
attribution.

Refactor implication: promote required fields into typed contracts or remove
the dependency. Do not let debug payload carry runtime semantics.

### F8. `ToolRunner` Protocol Understates the Real Contract

The public `ToolRunner` protocol declares only `run_tool_calls(tool_calls)`.
The actual loop probes or calls additional behavior:

- `run_tool_calls(..., context=...)`;
- `context_for_node(node_key, role)`;
- `tool_execution_metadata(call, context=...)`;
- `TypeError` fallback when `context` is unsupported.

This allows silent degradation and makes replacement unsafe.

Refactor implication: tool execution must have a typed contract for context,
metadata, permission evidence, effect fingerprints, timeout/network policy, and
fallback behavior.

### F9. `AgentSpec` Mixes Stable Agent Contract With Prompt Strategy

`AgentSpec.instructions` is currently passed directly as subagent instructions.
`prompt_ref` exists but has no clear runtime reader. This mixes stable MAS role
declaration with prompt/harness strategy.

This does not mean `instructions` can never exist. It means the next refactor
must decide whether it is:

- durable role contract;
- prompt-template reference;
- harness-local execution instruction;
- example-only convenience.

Refactor implication: do not place `AgentSpec.instructions` and `prompt_ref` in
a "safe keep" bucket without defining their ownership. Prompt strategy must not
leak into stable MAS contracts by accident.

### F10. `ModelHint` Creates a Second Model Selection Axis

`ModelHint` is a coarse `fast/balanced/powerful` signal on `HarnessBackend.invoke`.
At the same time, `ModelRouter` resolves provider/model by role. Backends honor
these signals differently.

This creates split-brain model selection:

- role-based routing;
- hint-based routing;
- provider default model;
- backend-local mapping.

Refactor implication: model selection must become a provider/routing concern
with one explicit precedence model. The execution harness may request a model
profile, but it should not hide provider selection policy.

### F11. Docs and Acceptance Surfaces Overclaim Current Evidence

The most important misleading surfaces are:

- `README.md` headline and architecture table describing replaceable harness
  backends;
- README examples centered on `--backend mock`;
- ADR-0009 saying the agent-native rewrite is complete and reliable delivery is
  proven;
- `scripts/p0_acceptance.py` local path using mock/dry-run checks while README
  describes "Full acceptance";
- `examples/native_team/README.md` presenting mock fixture replay as the
  representative native-team proof.

Refactor implication: Phase 0 must rewrite public truth surfaces before code
splitting, otherwise the next refactor will continue optimizing against the
wrong promise.

## 6. What Must Not Be Misread

These clarifications prevent the next phase from fixing the wrong problem.

1. Do not claim the current Claude SDK backend already owns HydraMind's runtime.
   It does not. Treat it as a future ownership risk.
2. Do not claim `MockBackend` lacks replay support. It has replay support. The
   issue is that replay is not agent acceptance.
3. Do not claim there is no durable interaction evidence. There is an
   interaction log projection. The issue is that the authoritative `Interaction`
   aggregate is not durable scheduling state.
4. Do not claim all memory is observability-only. Some memory is retrieved back
   into prompts, so it can affect execution.
5. Do not solve this by renaming files. The current boundaries are behaviorally
   mixed.

## 7. Required Refactor Direction

### Phase 0 — Freeze Truth Surfaces

Scope:

- README;
- `docs/architecture/10-harness-backend.md`;
- `docs/architecture/90-decisions.md` ADR-0009;
- `scripts/p0_acceptance.py` labels/output;
- `examples/native_team/README.md`;
- half-updated overview references.

Required changes:

- mark `HarnessBackend` as legacy/misnamed;
- stop calling mock/dry-run paths full acceptance;
- classify current local checks as contract/plumbing/replay checks;
- remove "Claude/OpenAI/Mock harness backend" phrasing;
- state that live-agent/MAS acceptance is not yet proven by P0 local checks.

Done signal:

- no public doc claims that provider adapters or mock fixtures are replaceable
  execution harnesses.

### Phase 1 — Split Provider From Execution Contracts

Introduce provider contracts for:

- provider identity and model identity;
- endpoint/transport;
- context limits;
- usage/cost metadata;
- provider-specific reasoning/tool-call response parsing;
- role/profile routing with clear precedence.

Split or move:

- `OpenAICompatibleBackend`;
- `ModelRouter`;
- OpenAI-compatible support serialization;
- `ModelHint` precedence.

The provider contract should not expose subagent support, interaction support,
compaction, or execution recovery.

Done signal:

- model/provider code can be replaced without changing tool-loop, memory,
  control, gate, or MAS scheduling behavior.

### Phase 2 — Define `ExecutionHarness`

> **SUPERSEDED / realized (ADR-0012 + `96` §10):** this Phase-2 landed as
> `ExecutionHarness` + `HydraMindExecutionHarness` (PLAN-20260619-001 N1). Two
> corrections to the framing below: (1) under the **narrow harness**, sub-agent
> *spawn* is NOT a harness responsibility — the harness emits a typed delegation
> request and orchestration owns the spawn act (`SubagentSpawner`,
> PLAN-20260622-001). (2) the broad input list below was realized as the typed
> `ExecutionHarnessPolicy`; its inert `*_ref` carrier fields were later trimmed to
> the self-contained knobs actually owned by the harness (PLAN-20260623-001 A,
> ADR-0010 §F) — read `96` §10 for the current policy surface.

Add a harness contract around an execution episode, not one model turn.

Inputs should include:

- task/goal contract;
- agent or team contract;
- model/provider route or model profile;
- prompt/context policy;
- memory retrieval policy;
- tool registry and tool environment;
- permission/network/process/artifact constraints;
- evaluator/gate policy;
- budget/timeout policy;
- trace sink and evidence sink;
- recovery/resume context.

Outputs should include:

- final result;
- typed model invocations;
- tool evidence;
- verifier/evaluator evidence;
- trace events;
- failure classification;
- proposed state transitions;
- recovery/repair signals.

Hard boundary:

- the harness proposes outcomes and emits evidence;
- Control owns durable state transitions.

Done signal:

- existing prompt/context, tool-loop, trace, verification, and recovery pieces
  can be assembled behind one `HydraMindExecutionHarness` without relying on
  `InvocationResult.raw` debug semantics or duck-typed tool-runner behavior.

### Phase 3 — Bring Runtime-Influencing State Under Ownership

Expand the single-writer rule from `RuntimeSession` to all durable state that
can affect execution:

- interaction aggregates;
- memory records used in prompts;
- repair attempt counters;
- trace/evidence projections used for recovery;
- replay/evaluator evidence when used by gates.

This does not mean every append-only log must live inside `RuntimeSession`.
It means each durable state class needs:

- authoritative owner;
- version or append consistency;
- crash/restart semantics;
- read/write boundary;
- whether it can affect prompts, gates, scheduling, or recovery.

Done signal:

- no observer side effect can silently create prompt-affecting durable state
  outside the ownership model.

### Phase 4 — Make MAS Interaction Durable and Schedulable

Promote `Interaction`, `Turn`, and `Message` from in-memory kernel values to
durable control-owned runtime state.

Required design work:

- additive interaction schema;
- turn-level status, lease, retry, and recovery;
- authoritative message records, not only previews;
- protocol outcome records for vote/debate/coordinator modes;
- resumability after worker crash;
- compatibility path for workflow-node entrypoints.

Done signal:

- a native MAS team can resume from durable interaction state without replaying
  an entire workflow node from scratch.

### Phase 5 — Rebuild Acceptance

Use separate acceptance classes:

1. Contract tests.
2. Plumbing tests.
3. Replay tests.
4. Live-agent acceptance.
5. Live MAS acceptance.

Live-agent/MAS acceptance must report:

- task;
- model/provider;
- execution harness;
- tools and environment;
- evaluator/gate profile;
- success/failure;
- cost and latency;
- failure category;
- recovery behavior.

Done signal:

- "acceptance" no longer means "mock fixture completed"; live acceptance has
  model-driven evidence, and replay evidence is labeled as replay.

## 8. Module Disposition

### Keep, With Boundary Review

- `src/hydramind/mas/contracts.py`
- `src/hydramind/mas/capability.py`
- `src/hydramind/kernel/contracts.py`
- `src/hydramind/kernel/scheduler.py`
- control single-writer principle
- gate/verifier result contracts

Boundary review required:

- `AgentSpec.instructions`;
- `AgentSpec.prompt_ref`;
- `CollaborationProtocol.metadata["rounds"]`;
- coordinator invariants repeated across contracts/runtime/scheduler.

These are not automatically wrong, but they must be intentionally owned.

### Split / Reclassify

- `src/hydramind/harness/openai_compatible.py`
- `src/hydramind/harness/openai_compatible_support.py`
- `src/hydramind/harness/routing.py`
- `ModelHint`
- provider-related parts of `InvocationResult`, `Usage`, and response parsing

Target:

- provider/model package;
- shared model wire contracts only where necessary;
- no execution-harness capability flags on provider classes.

### Rebuild Behind `ExecutionHarness`

- `AgentPromptContextBuilder`
- `AgentExecutionRuntime`
- `AgentToolLoop`
- native team member execution
- trace/evidence emission policy
- verifier/evaluator invocation policy
- retry/repair/recovery policy
- tool environment and permissions

Target:

- default `HydraMindExecutionHarness`;
- future alternative harnesses can replace execution strategy without changing
  task, tools, providers, gates, or control state.

> **SUPERSEDED (ADR-0012 / `96` §4.2):** `native team member execution` is rebuilt
> behind the harness as the per-member *execution* (loop·model·tools), but the act
> of **instantiating a team member / sub-agent is orchestration-owned** — not a
> harness responsibility. The harness emits the typed delegation request only.

### Move Out of Normal Runtime

- `MockBackend` runtime selection;
- `--backend mock` as representative quickstart;
- mock fixture acceptance;
- external Agent SDK backend as first-class core direction.

Target:

- `hydramind.testing`, `tests/support`, or `ReplayFixture`;
- explicit non-agent semantics;
- no "production harness" wording.

## 9. Acceptance Taxonomy

Use these names precisely.

### Class 1 — Contract Tests

Validate schemas, type contracts, invariants, and serialization.

### Class 2 — Plumbing Tests

Validate control, queue, tool, artifact, and state wiring with deterministic
inputs.

### Class 3 — Replay Tests

Replay a known model/tool trace or fixture to detect regressions. Useful, but
not proof of current model behavior.

### Class 4 — Live-Agent Acceptance

Run a fixed task with a live model, fixed tools/environment, fixed harness, and
fixed evaluator. Report success, cost, latency, failures, and recovery.

### Class 5 — Live MAS Acceptance

Run a live multi-agent task where agents exchange context according to
`CollaborationProtocol`, choose tools, produce artifacts, and pass semantic
evaluators.

Current P0/local acceptance is Class 2/3. It must not be described as Class 4/5.

## 10. Terms

### Avoid Until Corrected

- replaceable `HarnessBackend`;
- Claude/OpenAI/Mock harness backend;
- mock acceptance;
- native-team proves live agent reliability;
- production-grade agent harness;
- agent-native rewrite complete;
- workflow-grade.

### Prefer

- `ExecutionHarness`;
- `LLMProvider` / `ModelProvider`;
- provider routing;
- replay fixture;
- test double;
- plumbing acceptance;
- replay regression;
- live-agent acceptance;
- live MAS acceptance;
- control-owned interaction state.

## 11. Refactor Negative Cases

The next refactor is not done if any of these remain true:

- public docs still call OpenAI-compatible, Claude SDK, or Mock a replaceable
  execution harness;
- `--backend mock` remains the primary quickstart or acceptance proof;
- provider classes expose subagent/interaction/compaction capability as if they
  were harnesses;
- `InvocationResult.raw` carries runtime semantics;
- tool-runner replacement depends on undocumented `getattr` or `TypeError`
  fallback behavior;
- prompt-affecting memory writes happen as unowned observer side effects;
- interaction state can be lost on crash except for preview logs;
- live acceptance results are reported by model alone instead of
  `task + model/provider + harness + evaluator`.

## 12. Evidence Index

Current implementation evidence to re-check during the refactor:

- `src/hydramind/harness/base.py`: `HarnessBackend`, `ModelHint`,
  `InvocationResult.raw`.
- `src/hydramind/harness/openai_compatible.py`: provider routing,
  `HarnessCapabilities`, `_OpenAICompatibleSubagentHandle`.
- `src/hydramind/harness/routing.py`: provider profiles, role normalization,
  model route resolution.
- `src/hydramind/harness/claude_sdk.py`: current thin SDK wrapper and P1
  comments.
- `src/hydramind/harness/mock.py`: fingerprinted record/replay and mock
  subagent behavior.
- `src/hydramind/orchestration/agent_context.py`: memory context injected into
  prompts.
- `src/hydramind/runtime_memory.py`: memory observers and retriever wiring.
- `src/hydramind/memory/projector.py`: observer-side memory writes and
  in-process episode buffers.
- `src/hydramind/orchestration/agent_tools.py`: tool loop, hidden ToolRunner
  contract, `InvocationResult.raw` reads.
- `src/hydramind/orchestration/collaboration_runtime.py`: in-memory
  `Interaction` aggregate.
- `src/hydramind/orchestration/collaboration_logging.py`: optional/preview
  interaction log projection.
- `src/hydramind/control/models.py`: `RuntimeSession` shape and
  `InteractionLogRecord`.
- `src/hydramind/control/session_interactions.py`: interaction logs stored in
  metadata.
- `src/hydramind/mas/contracts.py`: `AgentSpec.instructions`, `prompt_ref`,
  protocol metadata and coordinator invariants.
- `README.md`, `docs/architecture/10-harness-backend.md`,
  `docs/architecture/90-decisions.md`, `scripts/p0_acceptance.py`,
  `examples/native_team/README.md`: truth-surface corrections.

## 13. External Basis

This correction follows the harness interpretation used in agent benchmark and
harness-engineering work: agent capability should be evaluated as a
model-harness configuration because context handling, tool management, state,
constraints, permissions, tracing, and recovery materially change outcomes.

HydraMind takeaway:

- model replacement is provider routing;
- harness replacement is replacing the execution shell around the model;
- replay is regression evidence;
- live acceptance is model-driven evaluation under a named harness.
