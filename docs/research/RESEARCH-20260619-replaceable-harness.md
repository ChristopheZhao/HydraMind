# RESEARCH-20260619 — What does a "replaceable harness" replace? (layer boundaries)

> Deep-research (fan-out web search → fetch 18 sources → adversarial 3-vote
> verification → synthesis). 90 claims extracted, 25 verified, 23 confirmed,
> 2 killed. Used to scope PLAN-20260619-001 (§0.1, §0.2, N4).

## Question
In LLM agent / MAS architecture, what precisely does a "replaceable agent
harness" replace; what are the standard layers; what is held FIXED vs SWAPPED;
is multi-turn intrinsic to the harness; and how does that map to HydraMind's
ModelProvider / ExecutionHarness / control+orchestration layers.

## Verified findings (all high-confidence, 3-0 unless noted)
1. A harness is the **model-external, editable execution shell** between the
   provider API and the environment: system prompt, tools + dispatch, context
   construction, tool invocation, state, permissions/workspace, budget, tracing,
   recovery. Distinct from the base model/provider. "Agent = Model + Harness"
   (model = engine, harness = car).
2. Harness decomposes into orthogonal editable components: system prompt, tool
   description, tool implementation, middleware, skill, sub-agent config,
   long-term memory. Some sources split "scaffolding" (pre-exec assembly) vs
   "harness" (runtime orchestration of tool dispatch/context/safety/session).
3. Model = FIXED axis, harness = SWAPPED axis; harness is model-agnostic by
   construction; provider/model routing + cost = config, not code. Report
   capability at the **model-harness configuration** level.
4. Replace-the-harness FIXED set = task/prompt/fixtures, sandbox/workspace state,
   budget/timeout, providers/model, evaluator/output-contract. SWAPPED set =
   agent loop, tool interface/schema + state policy, workspace/session/memory
   management, prompting/action format, stopping/retry/recovery. Each harness's
   native execution behavior is preserved, not normalized.
5. The **multi-turn agent loop is intrinsic to (owned by) the harness**: it runs
   the ReAct-style loop and owns termination ("final response with no tool calls"
   or a safety/budget cap), not the model.
6. The single-agent execution loop (agent↔environment, harness) and the
   multi-agent interaction loop (agent↔agent) are structurally distinct; the
   multi-agent loop is an **additional layer above** the harness → interaction
   protocols / role assignment / message exchange / coordination belong to the
   orchestration/MAS layer.
7. Decision protocols (voting vs consensus) are first-class **swappable
   orchestration-layer** variables — aggregation spans agents, so it cannot live
   in a per-agent shell (voting +13.2% reasoning; consensus +2.8% knowledge).
8. Harness-only changes (model + task fixed) move benchmarks materially and
   reorder rankings (Claw-SWE-Bench: harness 27.4pp vs model 29.4pp Pass@1;
   Terminal-Bench 2.0 rank 30→5) → empirical justification for a replaceable
   harness layer.

## Killed claims (0-3 refuted) — keep us honest
- "Multi-turn is intrinsic to ALL tested harnesses (avg 47–95 turns)" — refuted
  as an *empirical turn-count* fact. The supported position is *architectural*:
  the harness OWNS the loop + termination (finding 5). → multi-turn is a harness
  *capability*, not a guarantee every episode runs many turns.
- "The harness is the COMPLETE software infra wrapping the LLM (incl.
  orchestration loop)" — refuted; the multi-agent orchestration loop is a
  separate layer (finding 6), not part of the per-agent harness.

## Caveats
- Terminology not standardized: some sources fold subagent/orchestration into
  "harness"; cleanest 3-layer mapping = single-agent loop + tool-use +
  context/memory + subagent *delegation* = harness; multi-agent *interaction
  protocols / aggregation* (vote/consensus/turn scheduling/gates) = orchestration.
- "scaffolding" vs "harness" used inconsistently across sources.
- Model–harness **co-adaptation**: architectural decoupling does NOT guarantee
  performance portability when swapping the harness while keeping the provider.
- Fast-moving area (most primary sources 2026); emerging consensus, not settled.

## HydraMind mapping (see PLAN-20260619-001 §0.2)
- (a) Provider = `ModelProvider`/`LLMProvider` — FIXED on harness swap.
- (b) **ExecutionHarness = the replaced/swapped layer** = per-agent/per-turn
  agent↔environment shell (prompt/context, model invoke+trace, tool-use loop,
  memory injection, permissions/sandbox/timeout, stopping/retry/recovery).
- (c) Orchestration = FIXED on swap = kernel scheduler + CollaborationProtocol
  (agent↔agent loop), protocol_outcomes (vote/coordinator aggregation), gating,
  control (durable interaction/leases/idempotency), governance.

## Round 2 (2026-06-19) — harness-engineering native scope vs replaceable abstraction (INCONCLUSIVE: session limit)
> The round-2 workflow hit the provider session limit during the verify phase, so
> its claims are UNVERIFIED (the "14 refuted" is a 0-0 vote artifact of failed
> verifier agents, not real refutations). Recorded as leads; re-verify after reset.

Methodological point (from user): do NOT conflate "harness engineering" (native
scope) with "replaceable harness" (the abstraction). Findings (UNVERIFIED):
- The field SPLITS on harness scope, along whether a separate runtime/control
  plane exists:
  - Agent-centric / monolithic (Adnan "AI control plane" blog; round-1
    Harness-Bench "tracing, recovery"; awesome-harness-engineering "verification
    loops, memory systems"): NATIVE harness includes retry/recovery,
    observability/tracing, memory (short+long), guardrails.
  - Layered (LangChain harness-vs-runtime): harness = agent logic
    (prompts/tools/skills/loop/middleware, swappable); runtime = durable
    execution/recovery/memory/observability/HITL (fixed). Durable/cross-cutting
    concerns factored OUT of the swappable harness.
  - arXiv 2604.08224: harness as a coordination layer integrating externalized
    memory/skills/protocols.
- Resolution adopted (PLAN-20260619-001 §0.3): split by OWNERSHIP TYPE, not topic.
  Harness owns POLICY/STRATEGY + evidence EMISSION (tool loop, context, memory
  retrieval, in-episode recovery strategy, verifier integration, trace emission,
  budget/stopping). control/gating/governance own DURABLE STATE + AUTHORIZATION +
  AUDIT. Litmus: swapping the harness must NOT change who writes durable state or
  authorizes transitions. HydraMind cuts this way by invariant (AGENTS.md +
  95-doc), matching the LangChain harness/runtime split.
- Correction logged: the earlier "harness only drives one turn + proposes/emits"
  was an OVER-NARROWING (imported the replaceability goal into the definition);
  recovery/observability/memory ARE native harness concerns at the policy level.

Round-2 sources (UNVERIFIED): LangChain "the runtime behind production deep
agents" (https://www.langchain.com/blog/runtime-behind-production-deep-agents);
Adnan Masood "Agent Harness Engineering: the rise of the AI control plane"
(https://medium.com/@adnanmasood/agent-harness-engineering-the-rise-of-the-ai-control-plane-938ead884b1d);
arXiv 2604.08224 (https://arxiv.org/abs/2604.08224).

## Round 3 (2026-06-19) — direct primary-paper read (VERIFIED by fetch)
Fetched the two primary papers directly (not summaries) to settle whether
recovery/observability/memory/verification are harness-native.
- **Harness-Bench (arXiv 2605.27922)** verbatim: harness = "the system layer that
  conditions model calls and turns model outputs into actions"; "organizes
  context, tools, state, permissions, constraints, and recovery"; "**recovery and
  tracing are core harness responsibilities, supporting … auditable agent
  execution.**" Explicitly OUTSIDE: the model/provider ("Agent = Model + Harness")
  and the **evaluator** ("is also external", observes completed runs
  post-execution); multi-agent orchestration out of scope.
- **Agentic Harness Engineering (arXiv 2604.25850)**: seven harness components
  INCLUDE "**sub-agent configuration**" and "**long-term memory**"; middleware
  controls "context, execution, and **recovery**"; observability (InMemoryTracer),
  self-check behavioral rules, guardrails, state persistence are inside. OUTSIDE:
  base model (held fixed), the framework/runtime infra, evaluation infra.
- **Conclusion (verified):** the research-native harness is the BROAD single-agent
  envelope and DOES own recovery/retry, tracing/observability, memory (incl.
  long-term), self-verification + guardrails, state persistence, subagents. Only
  OUTSIDE: (1) model/provider, (2) the EXTERNAL acceptance evaluator, (3)
  multi-agent orchestration (+ the cross-agent durable single-writer SoT /
  control-plane, by extension). This refutes the earlier "harness ≠
  control/gating/governance/experience" framing. Recorded in PLAN-20260619-001 §0.4.

## Sources (primary first)
- Harness-Bench — https://arxiv.org/abs/2605.27922 , https://arxiv.org/html/2605.27922v1
- Agentic Harness Engineering — https://arxiv.org/html/2604.25850v3
- Claw-SWE-Bench (harness vs model) — https://arxiv.org/html/2606.12344
- Terminal-agent (scaffolding vs harness; loop ownership) — https://arxiv.org/html/2603.05344v1
- LLM Reasoning Survey (agent↔env vs agent↔agent loop) — https://arxiv.org/pdf/2504.09037
- Voting or Consensus? (decision protocols, ACL 2025) — https://arxiv.org/pdf/2502.19130
- awesome-harness-engineering — https://github.com/ai-boost/awesome-harness-engineering
