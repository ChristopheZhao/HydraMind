# 10 - HarnessBackend Tombstone

This page is intentionally a tombstone.

`HarnessBackend` was the old model-turn/subagent boundary. It has been retired
from production source. Do not implement, import, extend, or document it as an
active extension point.

Current boundaries:

| Concern | Active owner |
|---|---|
| Provider/model access, role routing, model invocation, usage/cost parsing | `ModelProvider` / `LLMProvider` in `hydramind.harness` |
| Replaceable execution shell around an episode | `ExecutionHarness` in `hydramind.orchestration.execution_harness` |
| Default execution strategy | `HydraMindExecutionHarness` |
| Alternate swap-proof execution strategy | `ExplicitSubmitExecutionHarness` |
| Offline replay/test doubles | `hydramind.testing.MockProvider` and replay fixtures; Class 3 evidence only |
| Durable `RuntimeSession` mutation | Control layer / `SessionService` |
| Authoritative gate and acceptance verdicts | Gating / governance |

Read the active contract in [`10-execution-harness.md`](10-execution-harness.md).
ADR-0011 in [`90-decisions.md`](90-decisions.md) records the retirement
decision. The historical correction basis remains in
[`95-execution-harness-correction.md`](95-execution-harness-correction.md), but
that document is not the current extension contract.
