# 80 — Governance Contracts

> Governance is the release and audit layer. It defines what evidence must exist before prompt, node, gate, memory, routing, or control changes are considered releasable. It does not mutate `RuntimeSession` and is not in the online run path.

## 1. Scope

S9 adds a minimal framework-level governance package:

| Contract | Purpose |
|---|---|
| `AssetVersionRef` | Versioned references for prompts, policies, tools, node registries, or release bundles |
| `ReplayInputPackage` | Frozen inputs and version refs needed to replay a prior run |
| `ReplayResult` | Replay output summary with behavior and gate/writeback diffs |
| `EvaluationCase` | Offline case contract with input, label, and metric-set references |
| `EvaluationResult` | Metric scores and pass/fail derivation for a single case |
| `ReleaseEvidence` | Evidence supplied before releasing runtime-affecting changes |
| `ReleaseDecision` | Deterministic verdict from release evidence checking |

This keeps governance concrete without shipping a heavyweight evaluation service in P0.

## 2. Release Evidence Rule

`evaluate_release_evidence()` applies a small evidence matrix:

| Change class | Required evidence |
|---|---|
| `prompt_change` | replay report, diff summary, rollback ref, prompt bundle version, evaluation report |
| `node_logic_or_contract_change` | replay report, diff summary, rollback ref, evaluation report |
| `gate_rule_or_threshold_change` | replay report, diff summary, rollback ref, policy bundle version, evaluation report |
| `memory_writeback_policy_change` | replay report, diff summary, rollback ref, policy bundle version, evaluation report |
| `routing_or_control_policy_change` | replay report, diff summary, rollback ref, policy bundle version, and either evaluation report or regression note |

The routing/control exception is intentionally narrow: if a change only affects runtime policy and the operator can show it does not alter semantic output, a regression note can replace a full evaluation report.

## 3. Boundaries

Governance contracts are deliberately outside online execution:

- They do not import provider SDKs.
- They do not execute model judges or replay engines.
- They do not mutate `RuntimeSession`.
- They do not own queue/worker retries.
- They provide typed evidence surfaces that external release tooling can consume.

This matches the four-layer architecture: orchestration decides, control mutates runtime state, gates authorize transitions, and governance defines release/audit evidence.

## 4. Reference Lessons

The contract shape is informed by read-only review of:

- `short-video-maker`: runtime evidence and queue/worker separation.
- `AI4S`: task contracts, gate policy, and runtime task envelopes.
- `Mellis`: harness schema, worker job boundaries, and replay/evaluation release evidence.

The P0 decision is to ship reusable contracts now and defer automated replay/evaluation infrastructure until later product needs justify it.
