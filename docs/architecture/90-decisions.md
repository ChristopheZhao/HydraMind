# HydraMind Architecture — Decision Log (ADRs)

> Architecture Decision Records. Each ADR is immutable once **Accepted**; revisions
> are recorded as new ADRs that supersede the old one. These decisions encode the four
> design positions of `00-overview.md` into enforceable boundaries, and most are
> machine-checked by `tests/contract/test_architecture_invariants.py`. The recurring
> motivation is the production-aged short-video-generation MAS we distilled HydraMind
> from, whose abstraction debts are inventoried in
> `mas_architecture_deviation_inventory_20260323.md`.

---

## ADR-0001 — Harness is a replaceable backend, not a bundled runtime

**Status:** Accepted (P0-S1) · **Corrected by [ADR-0010](#adr-0010--execution-harness-correction-provider--harness--replay-are-distinct) (2026-06-18)**

> Correction note: the no-vendor-SDK boundary in this ADR still holds. What is a
> known deviation is the reading that the current `HarnessBackend` *is* the
> replaceable execution harness. In the code it is a model-turn + subagent seam,
> and OpenAI-compatible provider routing plus Mock replay leaked into it. The
> corrected split (`LLMProvider`/`ModelProvider` vs `ExecutionHarness`) is in
> ADR-0010 and `95-execution-harness-correction.md`.

**Context.** The reference project imported `anthropic` directly inside orchestration and
service code. Vendor types leaked into business logic, so the tool loop, model selection,
and retry behaviour were welded to one provider; switching backends or testing without a
live SDK meant editing the orchestrator. This is the "import-anthropic leak" called out in
`00-overview.md` §4.

**Decision.** The agent execution shell — tool loop, context compaction, subagent spawn,
hooks, sessions — lives behind a single `HarnessBackend` boundary (`hydramind.harness`).
All LLM/tool invocation goes through that boundary; no other layer constructs or imports a
vendor SDK. Claude Agent SDK is the first reference backend, with the abstraction designed
for OpenAI Agents SDK, Codex CLI, and self-hosted harnesses. A `Mock` backend ships for
tests. Vendor SDKs stay optional extras (`pyproject.toml` `[project.optional-dependencies]`
`claude`), never core dependencies.

**Consequences.**
- HydraMind orchestrates *on top of* harnesses; the framework owns coordination, the
  harness owns execution mechanics.
- `tests/contract/test_architecture_invariants.py` forbids `import anthropic` /
  `openai` / `claude_agent_sdk` outside `src/hydramind/harness/`; a leak fails CI.
- Backend swap is a config/wiring change, not a code edit. Tests run offline against the
  mock backend.
- Cost: every backend must satisfy the `HarnessBackend` contract
  (`tests/contract/test_harness_contract.py`), so adding a backend is more work up front
  than a direct SDK call.

---

## ADR-0002 — Single-mainline control ownership of `RuntimeSession`

**Status:** Accepted (P0-S2)

**Context.** The reference project grew two parallel mainlines — `orchestrator.py` and a
later `episode_orchestrator.py` — that both mutated runtime state. State transitions raced,
diverged, and were impossible to audit; the dual-mainline bug is the headline entry in the
deviation inventory.

**Decision.** `RuntimeSession` is a typed Source of Truth, and **only the control layer
(`SessionService`, `hydramind.control.session`) mutates it**. Orchestration and planners
*decide* and request transitions; control *writes* them through the gate/apply loop
(`hydramind.control.plane`). There is exactly one mainline; no second orchestrator owns a
parallel copy of state.

**Consequences.**
- Every state change funnels through one auditable place, so transitions are serialized and
  replayable.
- `tests/contract/test_architecture_invariants.py` asserts that only modules under
  `src/hydramind/control/` mutate `RuntimeSession`; a stray write elsewhere fails CI.
- Orchestration stays stateless with respect to SoT — it holds plans and decisions, not
  authoritative runtime state.
- Cost: orchestration cannot take shortcuts by poking session fields directly; it must
  express intent and let control apply it.

---

## ADR-0003 — Prompts and schemas are externalized configuration

**Status:** Accepted (P0-S4)

**Context.** The reference project scattered inline `"You are ..."` system prompts and
ad-hoc JSON schemas through agent code. Prompts could not be diffed, versioned, or A/B
tested independently of logic, and behaviour changes hid inside code changes.

**Decision.** Prompts and structured-output schemas are config, not code. System/role
prompts live in `hydramind.orchestration.builtin_prompts`; schemas live alongside
`GoalSpec`/`ExecutionPlan` and workflow config. Business logic *references* a prompt/schema;
it never inlines persona text.

**Consequences.**
- `tests/contract/test_architecture_invariants.py` runs an AST check that rejects inline
  `"You are"` prompt strings outside `src/hydramind/orchestration/builtin_prompts/`; a
  scattered prompt fails CI.
- Prompts are reviewable and versionable as artifacts, decoupled from the code that uses
  them.
- Cost: adding or tuning a prompt means touching the prompt module/config, a deliberate and
  visible edit rather than a one-line literal.

---

## ADR-0004 — Gating is a first-class typed contract

**Status:** Accepted (P0-S3)

**Context.** In the reference project, quality/policy/HITL checks were optional callbacks
bolted onto the workflow. Whether a transition was authorized depended on whether someone
remembered to wire the callback, and gate outcomes were untyped booleans with no evidence
trail.

**Decision.** Gating is a typed layer (`hydramind.gating`) with explicit artifacts:
`GateContract` (what must hold), `GateEvaluator` (how it is checked), and `GateResult`
(the typed outcome plus evidence). Quality gates, policy gates, HITL gates, timeout gates,
and verifier results are all expressed this way. Other layers **consume** `GateResult`;
state transitions and repair/replan decisions are *authorized by* gate results, not by
ad-hoc inline checks. The reference `audio_delivery_gate_evaluator` becomes the example
implementation (`hydramind.gating.evaluators.example`).

**Consequences.**
- Authorization is data, not control flow: a transition either has a passing `GateResult`
  or it does not, and the evidence is inspectable.
- Gates are independently testable; the evaluator contract is reusable across domains.
- Gate outcomes feed naturally into observability/governance evidence.
- Cost: every authorized transition must produce a real `GateResult`, so "just let it
  through" is no longer expressible without an explicit pass.

---

## ADR-0005 — Agent–queue decoupling; the queue owns no Source of Truth

**Status:** Accepted (P0-S6)

**Context.** Queue/worker code in the reference project accreted business logic and
fragments of orchestration state, so the worker pool became a second place where runtime
truth lived — directly contradicting single-mainline ownership (ADR-0002).

**Decision.** Runtime state is the control layer's typed SoT; the queue
(`QueueAdapter`, `hydramind.queue`) handles **eligibility scheduling only**. It may *read*
the SoT to decide what is runnable, but it never mutates it and holds no business logic or
orchestration state. Celery/Redis sit behind the `QueueAdapter` interface as optional
extras, so the scheduling mechanism is swappable.

**Consequences.**
- The worker pool is stateless with respect to semantic SoT; scaling or replacing workers
  carries no hidden state.
- Per the single-ownership table in `00-overview.md` §2, the queue is read-only on SoT;
  any mutation belongs to control (ADR-0002, enforced by
  `tests/contract/test_architecture_invariants.py`).
- The queue backend (in-memory, Celery, etc.) can change without touching orchestration or
  control.
- Cost: the queue cannot "fix up" state inline; it must surface eligibility and let control
  act.

---

## ADR-0006 — Goal-driven primary input; YAML workflows are recipes

**Status:** Accepted (P0-S4)

**Context.** Workflow-first frameworks (and the reference project's YAML pipelines) treat a
hand-authored DAG as the conceptual source of planning truth. That couples *intent* to a
specific *topology*, making goals hard to re-plan and repair when reality diverges from the
recipe.

**Decision.** The primary user input is a `GoalSpec`, which a planner turns into an
`ExecutionPlan` (`hydramind.orchestration`). YAML workflows remain first-class but
demoted to **reusable recipes and test fixtures** — useful starting topologies, not the
conceptual source of planning truth. Planning, repair, and replan operate over the goal and
its derived plan.

**Consequences.**
- The planner can derive, repair, and replan an `ExecutionPlan` from a `GoalSpec` instead
  of editing a static DAG.
- YAML recipes still load and run, giving a deterministic, reviewable on-ramp and stable
  test fixtures.
- Gate-driven repair/replan (ADR-0004) acts on the plan derived from the goal, closing the
  loop between intent and execution.
- Cost: a planner must exist and be trusted; for trivial fixed pipelines the goal→plan
  indirection is more machinery than a raw YAML run.

---

## ADR-0007 — Agent-native actor/message kernel; the scheduled unit is the agent interaction

**Status:** Accepted (PLAN-20260604-001 / S90)

**Context.** HydraMind was distilled from a linear pipeline tool and its kernel models work
as a DAG of single-agent task nodes: the node is simultaneously the unit of scheduling,
state, authorization, observability, and memory (`session_id, node_key`). Multi-agent
collaboration was retrofitted as opaque subroutine behavior *inside* one node
(`NativeTeamExecutor.invoke` — a fixed `for member in team.members` single-pass fan-out
where members never read each other's output). The systemic root-cause analysis
(assessment `EVAL-20260604-003-native-mas-architecture-rootcause`, in the maintainers'
internal dev-notes) found, across
five independent architecture axes, that `agent`/`message`/`turn`/`vote`/`arbitration`/
`workspace` have no representation in the state model, control/gate plane, observability, or
memory, and that the rich `mas/contracts.py` ontology (modes/topologies/aggregation/
arbitration) is consumed by zero runtime branches. This is a model mismatch, not a missing
feature: the kernel cannot express scheduled, governed agent interaction. All five axes were
judged `kernel_redesign`.

**Decision.** HydraMind's runtime kernel becomes **agent-native**: `Agent`, `Message`, and
`Turn` are first-class, durable, control-owned, scheduled entities, and the unit the
top-level scheduler reasons about is the **agent interaction**, not an opaque node. A plain
task node degrades to a single-member interaction, unifying the two former layers under one
message-driven scheduler. `CollaborationProtocol` is an *interpreted* policy that drives
turn routing and locates arbitration points (type-directed dispatch, not magic-string
config sniffing). The **agent (LLM via `HarnessBackend`) is the core decision driver**:
planning, verification, and repair are harness invocations; deterministic Python rule-base
logic that *substitutes for agent intelligence* (`StaticGoalPlanner`, `FallbackGoalPlanner`,
rule-based content verifiers, `goal_feedback` repair policies) is removed. Determinism is
retained only for **safety/boundary** concerns: harness boundary, tool sandbox,
artifact-root containment, schema/contract validation, capability and queue/DLQ semantics.
Offline determinism is preserved by **MockBackend record/replay** of agent decisions rather
than rule-based planners.

This ADR refines, and where they conflict supersedes, the node-centric reading of ADR-0006:
the goal→plan path is retained, but its conceptual runtime unit is the agent interaction.
ADR-0001 (harness boundary) and ADR-0002 (single-writer control) **continue to hold**; the
opaque parent→child subagent contract is relaxed *only within an interaction* so members can
read each other's messages, exposed as a typed `HarnessBackend` interaction primitive — no
vendor SDK leaves `hydramind.harness`, and all interaction state is written solely by the
control layer.

**Consequences.**
- Agent interactions are observable, gateable, replayable, and memorable at interaction
  granularity; `MemoryScope.AGENT` is activated and agent/message/turn/handoff/vote become
  first-class observability events.
- Every advertised `CollaborationMode`/topology/aggregation/arbitration value is either
  executed by the kernel or removed; a lock-step contract test asserts the dispatch table
  covers the advertised enum surface (no dead-contract re-accumulation).
- The control plane authorizes interaction transitions (turn/vote/handoff/coordinator) via
  typed apply intents and `GateResult`; "just let it through" stays inexpressible (ADR-0004).
- Cost: a kernel rewrite, staged additive-primitives → one vertical slice → node migration →
  rule-base removal, each sprint checkpoint-gated; offline tests depend on recorded agent
  decisions, so fixtures must be captured and maintained.
- The rewrite is sequenced by `PLAN-20260604-001` (S90–S102).

---

## ADR-0008 — Decision authority is the orchestrator agent; cross-cutting guarantees are layer responsibilities, not a state machine

**Status:** Accepted (PLAN-20260604-001, clarifies ADR-0007)

**Context.** While sequencing the agent-native rewrite (S91→S92) the question arose: if
HydraMind is native multi-agent, why keep a *state machine* at all? The term conflates two
orthogonal concerns: (1) **decision authority** — who decides what happens next — and (2)
**durable state + cross-cutting guarantees** — replay, recovery, audit, safety. The owner's
position: a centralized MAS is fine, and orchestrating work as a *dynamic DAG* is fine; the
defining requirement is that decisions are produced by an **orchestrator agent** (which may
plan and dynamically re-plan that DAG), not by a deterministic state-machine / rule matrix.
Replay, recovery, audit, and safety gating are **not** properties of a decision state
machine — they are responsibilities distributed across the runtime, control, gating, and
governance layers, operating on durable state those layers own. (Durable state must still
exist *somewhere* for replay/recovery — but as a layer-owned substrate, never as a brain
that drives behavior.)

**Decision.**
- **Decision authority is agent-driven.** The orchestrator is a native agent that plans and
  dynamically re-plans the work graph (a dynamic DAG of tasks / agents / interactions). The
  DAG is an agent-produced artifact, not a hardcoded prescriptive transition matrix.
  Adaptive decisions — plan/replan, repair, verify-good-enough, and who-acts-next in
  delegation/debate/coordinator modes — are produced by agents through `HarnessBackend`.
  Structural recipes a user *declares* (DAG edges, broadcast/pipeline topology) are config,
  not decisions.
- **No deterministic rule engine substitutes for an agent decision.** Rule-base that
  *decides* (`StaticGoalPlanner` synthesis, `FallbackGoalPlanner`, `goal_feedback` repair
  policies, magic-string collaboration dispatch) is removed. Determinism remains only for
  mechanism and safety/boundary.
- **Cross-cutting guarantees are layer responsibilities, not state-machine features:**
  runtime layer → durable execution / replay; control layer → single-writer durable
  recording, lease / recovery; gating layer → safety/boundary authorization (`GateResult`);
  governance + observability → audit evidence. `RuntimeSession` is the substrate these
  layers operate on, not a decider; control **records** agent-driven events and does not
  choose business flow.

**Consequences.**
- ADR-0002's single-writer rule continues, with its purpose stated as **durability and
  audit, not decision**. ADR-0004 gates continue as **safety/governance authorization**, not
  a decision engine.
- S92+ are reframed: control-owned "interaction state" is an **append-only recording** of
  agent-driven message/turn/handoff/vote events, not a new decision state machine. The
  agent-driven orchestrator/planner is the spine; deterministic planners/verifiers/feedback
  policies are deleted in favor of agent-driven replacements.
- This sharpens ADR-0007; S90 (dead-surface freeze) and S91 (kernel vocabulary) remain
  valid under the clarified framing.

---

## ADR-0009 — The agent-native kernel rewrite is complete (S92–S102)

**Status:** Accepted (PLAN-20260604-001, S92–S102 closed 2026-06-06) · **Corrected by [ADR-0010](#adr-0010--execution-harness-correction-provider--harness--replay-are-distinct) (2026-06-18)**

> Correction note: the kernel/contract work recorded below landed, but the
> framing "rewrite complete / reliable delivery proven" overclaims. (1) The
> DoD-2 capstone and offline `MockBackend` record/replay are **plumbing/replay
> evidence, not live-agent or live-MAS acceptance** — no model decides. (2)
> `HarnessBackend` remains a model-turn seam, not the replaceable execution
> harness. (3) `Interaction`/`Turn`/`Message` are advanced in memory; durable,
> control-owned, lease/recovery-capable interaction state is **not** yet
> realized. See ADR-0010 and `95-execution-harness-correction.md` §5.

**Context.** ADR-0007/0008 set the target: an agent-native actor/message kernel where
decision authority is the orchestrator agent and the layers record/recover/audit/gate
without deciding business flow. PLAN-20260604-001 executed that target in staged,
checkpoint-gated, test-green sprints. This ADR records the realized end state so the
codebase, the invariants, and the docs agree.

**Decision (what was realized).**
- **S92 control-as-recorder** — control records agent-driven outcomes; it no longer scans
  `all_terminal` or completes the session itself. `ControlPlane.record_session_complete` is
  the only session-terminal path; the orchestrator decides completion.
- **S93 harness interaction primitive** — the `parent_context.seed_messages` seam is honest
  across all backends; input-keyed `MockBackend` record/replay is the offline-determinism
  backbone; backend capability declarations are honest.
- **S94/S95/S100 kernel scheduler** — collaboration runs through the S91 kernel scheduler as
  scheduled agent turns; `CollaborationProtocol` is an interpreted, type-directed
  `SchedulingStrategy` covering BROADCAST/PIPELINE/COORDINATOR/DEBATE/VOTE/DELEGATION with
  real aggregation (COLLECT/COORDINATOR_SUMMARY/VOTE) and gate-authorized arbitration
  (COORDINATOR/MAJORITY). The MAJORITY/VOTE tally is deterministic counting *over agent
  votes* (mechanism), not a rule decision.
- **S96/S97 agent-driven brain** — `StaticGoalPlanner`/`FallbackGoalPlanner` and the
  rule-based content verifier + `VerifierFeedbackRepairPolicy` are deleted; the planner agent
  and the semantic verifier are the only planning/verify-good-enough deciders; repair is the
  planner agent's `revise_plan`. Determinism remains only for safety/boundary (artifact
  existence, artifact-root containment, schema).
- **S98 typed node dispatch** — `NodeExecutionMode` + a dispatch table replace magic-string
  `execution_mode`; the legacy `subagent_group` path is removed.
- **S99 observability/memory first-classing** — agent/message/turn/handoff/vote are
  first-class observation events; `MemoryScope.AGENT` has production writes; episode
  projection carries the who-acted-next agent dimension.
- **S101 DoD-2 proof** — `examples/native_team/` runs a medium-complexity native team
  end-to-end and delivers a verified artifact reproducibly offline.
- **S102 final sweep** — `ApplyPayload`/`PolicyEvaluator` removed; governance is audit
  evidence (no auto-approval decision); `SharedWorkspace` artifact/memory refs removed so
  every declared collaboration capability is executed-or-removed.

**Invariant guard set (enforced by `tests/contract/test_architecture_invariants.py`).**
No vendor SDK outside `harness/`; only `control/` mutates `RuntimeSession`; prompts are
config; control's apply path records no session-completion decision; no rule planner
(`StaticGoalPlanner`/`FallbackGoalPlanner`), no rule verify/repair
(`ContentQualityVerifierRunner`/`VerifierFeedbackRepairPolicy`), no `subagent_group`, and no
`ApplyPayload`/`PolicyEvaluator` may re-accumulate in `src/`; node dispatch is the typed
table (no `execution_mode` string ladder); the executed-capability envelope equals the full
advertised `CollaborationProtocol` surface and the kernel dispatch table is lock-step with it
(the freeze guard still fails closed for any future out-of-envelope value).

**Consequences.** ADR-0001/0002/0003/0004/0007/0008 all continue to hold and are now
machine-guarded. Both Definition-of-Done conditions are met: the native-agent architecture
is implemented (DoD-1) and reliable delivery is proven (DoD-2). Future capabilities
(workspace shared-state, live-backend tool loops, concurrency for independent turns) are
deliberately out of this plan and should be added with their own execution + invariants.

---

## ADR-0010 — Execution-harness correction: provider, harness, and replay are distinct

**Status:** Accepted (PLAN-20260618-001, 2026-06-18). Corrects ADR-0001's
"`HarnessBackend` is the replaceable harness" reading and ADR-0009's
"rewrite complete / reliable delivery proven" claim. ADR-0001's no-vendor-SDK
boundary and ADR-0002's single-writer control rule continue to hold.

**Context.** A multi-agent architecture quality review (basis:
[`95-execution-harness-correction.md`](95-execution-harness-correction.md))
found that HydraMind's headline differentiator sat on the wrong abstraction. The
project treated "do not leak vendor SDKs upward" as equivalent to "replaceable
harness", then let provider adapters and mock fixtures occupy the harness slot.
Concretely: `HarnessBackend` is mostly a model-turn + subagent seam;
`OpenAICompatibleBackend` mixes provider routing/wire translation with harness
capabilities and subagent handling; `MockBackend` is deterministic replay treated
as a runtime backend and acceptance path; native MAS interaction runs under a
workflow node with in-memory `Interaction` aggregates and a metadata projection,
not durable schedulable state; and runtime-influencing memory/trace/repair/
protocol outcomes lack typed ownership.

**Decision.**
- **Provider replacement is not harness replacement.** Model/provider access
  (`LLMProvider`/`ModelProvider`) — provider/model identity, endpoint/transport,
  context limits, usage/cost, response parsing, role/profile routing — is a
  separate contract from the `ExecutionHarness`. Provider classes must not expose
  subagent/interaction/compaction/recovery capability flags.
- **`ExecutionHarness` operates on an execution episode, not one model turn.** It
  owns context/memory policy, prompt/message driving, tool loop and permissions,
  evaluator/gate integration, trace/evidence emission, budget/timeout/recovery,
  and subagent/team strategy. **The harness proposes outcomes and emits evidence;
  Control owns durable state transitions.**
- **Replay is test evidence, not agent execution evidence.** `MockBackend`
  record/replay survives only as `ReplayFixture`/`hydramind.testing`/test-double
  support with explicit non-agent semantics; it is removed from representative
  runtime selection and from acceptance language.
- **Durable runtime-influencing state needs an owner.** Prompt-affecting memory,
  repair-budget authority, protocol vote/coordinator/debate outcomes, and native
  MAS interaction state each need an authoritative owner, versioning/append
  semantics, crash/restart semantics, and idempotency rules. Protocol outcomes
  must be typed and canonical — equivalent model wording must not change a
  majority outcome unless the typed vote/evaluator result differs.
- **Acceptance reports by `task + model/provider + harness + evaluator`**, split
  into contract / plumbing / replay / live-agent / live-MAS classes (see
  `95-...` §9). Offline replay is never reported as live acceptance.
- **External Agent SDK adapters are not a near-term core direction.** The current
  Claude SDK backend is thin and does not own HydraMind's tool loop/state;
  expanding it to own sessions/tools/recovery would conflict with the
  `ExecutionHarness` and is out of scope unless a precise integration contract
  keeps HydraMind state, tools, gates, traces, and recovery authoritative.

**Consequences.**
- The refactor is sequenced by `PLAN-20260618-001` (S0 truth-surface freeze →
  provider/harness split → typed `ExecutionHarness` contract → stable
  evidence/protocol outcomes → runtime-state ownership → durable native MAS
  interaction → replay separation → live acceptance).
- `tests/contract/test_architecture_invariants.py` and new contract tests will
  guard: provider contracts expose no subagent/interaction/compaction/recovery;
  `ExecutionHarness` inputs/outputs are typed; cross-layer event detail is
  versioned; protocol outcomes are typed and canonicalized.
- Cost: `OpenAICompatibleBackend` is split or retired from the core harness
  boundary; durable interaction state is additive new work; acceptance must be
  rebuilt; docs and acceptance language are frozen first (S0) so later slices do
  not optimize against the old promise.

---

## ADR-0011 — HarnessBackend retired; ExecutionHarness is the replaceable shell

**Status:** Accepted (PLAN-20260619-001 N5, 2026-06-19). Implements ADR-0010's
provider/harness split and supersedes active use of the old backend boundary.

**Context.** PLAN-20260619-001 N1-N4 completed the code-side correction:
provider access is `ModelProvider`; episode execution is `ExecutionHarness`;
production source no longer defines the old backend abstraction; and a second
multi-turn harness (`ExplicitSubmitExecutionHarness`) proves harness replacement by
running the same native-team path with provider, tools, control, gates, and
orchestration held fixed.

**Decision.**
- The retired backend boundary is not an active extension contract. Its old
  architecture page is a tombstone that points to `10-execution-harness.md`.
- `ModelProvider` / `LLMProvider` is the only provider/model access seam.
  Provider switching (DeepSeek/Kimi/GLM or `--provider mock`) is model routing,
  not harness replacement.
- `ExecutionHarness` is the only replaceable execution shell. The active
  implementations are `HydraMindExecutionHarness` and `ExplicitSubmitExecutionHarness`;
  both must remain multi-turn/tool-loop capable.
- Mock/replay support lives in `hydramind.testing` and can prove Class 1-3
  contract/plumbing/replay evidence only. It must not be described as
  live-agent or live-MAS acceptance.
- Live Class 4/5 acceptance must report task, provider/model, harness identity,
  evaluator, and success/failure. If credentials or network are unavailable, the
  evidence state is not-proven, not silently passed.

**Consequences.**
- Public/operator docs must use `--provider mock` for deterministic local runs
  and avoid presenting mock replay as a representative runtime harness.
- Truth-surface checks fail active claims that reintroduce the old backend
  boundary, `--backend mock`, mock replay as live acceptance, or provider
  switching as harness replacement.
- Historical ADR/correction text may keep old terms as record, but active
  extension docs and acceptance surfaces must point to the provider/harness split
  above.

---

## ADR-0012 — Narrow-harness stance: harness is a data-plane executor; spawn is orchestration

**Status:** Accepted (2026-06-21, user-confirmed). Refines ADR-0010/0011 and the
broad single-agent-envelope reading in `RESEARCH-20260619-replaceable-harness.md`
§0.4 / PLAN-20260619-001: it narrows the harness on ONE point — subagent
spawn/instantiation — and adopts `96-agent-layering-and-harness-synthesis.md` as
the authoritative layering synthesis (supersedes `95-execution-harness-correction.md`
where they conflict).

**Context.** Two project layering docs diverged on how broad the harness is. The
broad reading (Harness-Bench 2605.27922, AHE 2604.25850) has the harness OWN
subagents; the narrow reading (HuggingFace glossary; Faramesh 2601.17744
propose-vs-commit; control/data-plane consensus) makes the harness a pure
data-plane executor that proposes/requests and never commits durable state or
spawns. They reconcile on ~everything (propose-vs-commit, control owns durable
state, gates first-class, scaffold = behaviour config, loop ⊂ harness); the live
conflict was **subagent spawn ownership**.

**Decision — adopt the narrow harness with a config/instantiation reconciliation:**
- **Harness = data-plane executor.** It runs the single-agent loop, calls the
  model, dispatches tools, produces side effects, is configured by Scaffold, and
  EMITS proposals + evidence + **delegation requests**. It never commits durable
  state and never spawns/instantiates agents.
- **Spawn/instantiation belongs to Orchestration**, recorded durably via Control
  (durable interaction + turn-lease), so a crashed MAS resumes from authoritative
  state. The spawned sub-agent runs through its OWN harness (recursive two-scale,
  96 §5).
- **Reconciliation (resolves the broad-vs-narrow tension):** the harness owns
  sub-agent *configuration/policy* (which member, tools, instructions — Scaffold /
  `AgentSpec`) and emits the delegation request; Orchestration owns the *spawn act*.
  AHE's "harness owns sub-agents" is read as *configuration*, not runtime
  instantiation, so the broad sources are not contradicted.
- **Scaffold** is a first-class NAMED behaviour-config layer fed to the harness —
  NOT an independent swap axis. The replaceable axis is the harness (vs the fixed
  model); scaffold elements are components within it. (Closes the earlier, retracted
  "scaffold as independent swap axis" framing.)

**Three-dimension rationale.**
- *Frontier:* rests on the genuinely-settled control/data-plane split (IBM/ETCLOVG/
  Futurum; roots in networking/K8s, not 2026 preprints), and is consistent with
  Macedo 2606.10106 / LangChain *Anatomy* placing subagent spawning in Orchestration.
  The contested, future-dated 2026 citations are weighted BELOW this consensus.
- *Project requirements:* HydraMind's value is durable, recoverable, control-owned
  native MAS; that requires spawn to be a control-owned durable event, not an
  in-harness ephemeral tool-call — which is exactly the narrow stance.
- *Project fit:* the code is already ~narrow (harness emits `proposed_transitions`
  only; durable interaction/turn-lease are control-owned). The sole inconsistency is
  that `spawn_subagent` sits on the harness surface (`ExecutionHarnessRuntime`),
  called from orchestration — a placement smell, not a durability bypass.

**Honest calibration.** This is a deliberate MINORITY/normative choice against the
mainstream SDK "loop-spawns-via-tool" model (Claude Agent SDK, OpenAI handoffs,
CrewAI delegation) and against Golem's fused runtime. Chosen because HydraMind
optimises for persistence/recovery/control-ownership, not for SDK-ecosystem
familiarity; integrations with loop-spawn SDKs need an adapter layer.

**Consequences.**
- **Phase-2 (bounded refactor):** relocate `spawn_subagent` off the harness surface
  onto orchestration; the harness emits a typed delegation request; orchestration
  instantiates via Control. Verify whether trackB's current call path is a true
  ownership bypass or only a naming/placement issue, and pin the result with a
  contract test in `tests/contract/test_architecture_invariants.py`.
- `96-agent-layering-and-harness-synthesis.md` is the active layering vocabulary
  (control/data plane master cut + four judgment questions); `95-…-correction.md`
  is superseded where it conflicts.
- **Phase-2 binding item DONE (PLAN-20260622-001):** `spawn_subagent` relocated off
  the harness surface onto the orchestration-owned `SubagentSpawner`; pinned by
  `test_spawn_is_orchestration_owned_not_on_harness_surface`.
- **Related Phase-2 residual closure (PLAN-20260623-001, tracked in `96` §10 — NOT
  scoped by this ADR):** the load-bearing-policy residual was closed by TRIM —
  `ExecutionHarnessPolicy`'s inert `*_ref` carrier fields were dropped, keeping the
  self-contained knobs as the legitimate typed expression of harness-owned policy
  (ADR-0010 §F), pinned by the no-`*_ref` contract guard
  `test_execution_harness_policy_carries_no_unresolved_ref`.
- **Rename note (2026-06-27, PLAN-20260627-001):** the alternate proof harness was
  renamed `ReActExecutionHarness` → **`ExplicitSubmitExecutionHarness`** (and its
  per-member strategy `ReActMemberTurnStrategy` → `ExplicitSubmitMemberTurnStrategy`,
  label `react` → `explicit-submit`). Rationale: the swappable axis is the *harness*
  (vs the fixed model); a harness must therefore be named after what genuinely
  *varies* at the壳层 — its loop granularity (single action vs batch tool-drain) and
  termination contract (explicit submit vs empty-tool-call stop). "ReAct" names an
  *agent reasoning paradigm* that is portable across harnesses (any harness can host
  a ReAct-prompted agent; it is Scaffold/config, not a harness identity), so naming
  the壳 after it mis-attributed an invariant to the replaceable seam and re-imported
  the agent-paradigm baggage the narrow stance separates out. Pre-rename docs/records
  (PLAN-20260619/0621/0623, SHO-20260622, ACC-20260621, and the internal memory trail)
  keep the old name as historical record; this note is the provenance pointer.
- No code changes land under this ADR itself; it records the stance and scopes the
  Phase-2 work.

---

## Conventions

- **Status values:** `Proposed` → `Accepted` → `Superseded by ADR-NNNN` (or `Deprecated`).
- **Immutability:** Accepted ADRs are not edited in place; record changes as a new ADR that
  supersedes the prior one and update its status line.
- **Enforcement first:** prefer a machine check (a contract test or typed contract) over a
  prose rule. The canonical enforcer for these boundaries is
  `tests/contract/test_architecture_invariants.py`.
