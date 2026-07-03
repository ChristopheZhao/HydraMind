# 10 - ExecutionHarness

`ExecutionHarness` is HydraMind's replaceable agent execution shell. It is not
the model provider, not the queue, and not the durable state owner.

## Current Split

| Layer | Holds fixed on harness swap | Swappable on harness swap |
|---|---|---|
| Provider | `ModelProvider` / `LLMProvider`, role routes, provider credentials, concrete model id | Nothing |
| Harness | Harness contract and typed episode I/O | Context policy, prompt/message driving, multi-turn tool loop, subagent/team strategy, trace/evidence emission, verifier integration, budget/recovery strategy |
| Orchestration | `OrchestratorAgent`, native MAS protocol scheduling, aggregation, tool registry, task inputs | The injected `ExecutionHarness` implementation |
| Control/gating | `RuntimeSession`, durable interaction state, leases, gate authorization | Nothing |
| Governance | Acceptance taxonomy and authoritative verdicts | Nothing |

Provider switching is model/provider routing. Harness replacement is changing the
execution shell while holding provider/model, task, tools, control, gates, and
orchestration fixed.

## Active Implementations

| Harness | Purpose |
|---|---|
| `HydraMindExecutionHarness` | Default harness. It composes the existing `AgentNodeInvoker` path and preserves the normal tool-drain loop. |
| `ExplicitSubmitExecutionHarness` | Alternate proof harness. It differs from the default in two harness-level control knobs only: one tool action per turn (act/observe, not batch tool-drain) and explicit `{"done": true, "submit": ...}` termination (instead of stopping on an empty tool-call turn). The single-action loop reads as "ReAct-style", but ReAct is an agent/scaffold-level pattern any harness can host — the harness is named after its control knobs, not the paradigm (ADR-0012 rename note). |

Both implementations are multi-turn/tool-loop capable. A single-shot shell that
only calls `ModelProvider.complete()` is not a valid HydraMind execution harness.

### Deferred capabilities (declared, not implemented)

Context **compaction** is correctly homed on the harness layer
(`ExecutionHarnessRuntime.compact_context` + the `COMPACTION` capability), because
in-episode context management is a harness responsibility — it is NOT on the
provider/model layer. Its implementation is **deferred**: no runtime path requires
it today (the multi-turn loop is bounded by `max_tool_rounds`, so the running
context never grows unbounded in practice), and `supports_compaction=False` gates
it off. `compact_context` therefore fails loud via the capability gate rather than
silently no-op'ing. This declared-but-unimplemented, correctly-placed state is
pinned by `test_runtime_compaction_fails_through_harness_capability_surface` and by
the provider-surface tests that assert the provider exposes no `compact_context`.
When a real long-context need appears, the implementation lands here (optionally
using the `COMPACTOR` route for summarization, with any durable persistence routed
through Control) without moving the capability to another layer.

## Episode Contract

The harness runs one execution episode for one scheduled node/member turn. Its
typed request and outcome live in
`src/hydramind/orchestration/execution_harness.py`.

The harness owns execution policy:

- prompt/context construction;
- memory retrieval/injection policy;
- model invocation through the supplied `ModelProvider`;
- tool-loop strategy and termination;
- subagent/team execution strategy;
- in-loop verifier integration;
- trace/evidence emission;
- timeout, budget, retry, and recovery strategy.

The harness does not own durable authority:

- it does not mutate `RuntimeSession`;
- it does not write queue messages;
- it does not authorize state transitions;
- it does not grade authoritative acceptance.

Those durable writes and verdicts remain in control, queue, gating, and
governance.

## Replay and Acceptance

`MockProvider` and replay fixtures are deterministic test support under
`hydramind.testing`. They can prove local Class 1-3 evidence:

- Class 1: contract/type/invariant checks;
- Class 2: plumbing checks across control, queue, tools, and state;
- Class 3: deterministic replay/fixture regression.

They cannot prove Class 4 or Class 5 acceptance. Live acceptance must use a live
provider, report model/provider identity, report harness identity, and record
not-proven when credentials or network are unavailable.

## Invariants

- Vendor SDK imports stay under `src/hydramind/harness/`.
- Providers expose no subagent, compaction, tool-loop, durable-state, or gate
  authority surface.
- `ExecutionHarness` remains the only replaceable execution shell.
- Provider switching must not be described as harness replacement.
- Local mock/replay runs must not be described as live-agent or live-MAS
  acceptance.
