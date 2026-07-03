# Changelog

All notable changes to HydraMind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a0] - 2026-05-30

First alpha of the HydraMind multi-agent-system (MAS) framework, extracted and
generalized from the short-video-maker reference into a harness-first
architecture with production-grade architectural discipline.

### Added

- **Four-layer harness architecture** — four semantic layers — `control` (state
  ownership), `gating` (admission/quality gates), `orchestration` (goal-driven
  flow), and `governance` (evidence/audit) — running on a replaceable `harness`
  (model/tool I/O) substrate, with `runtime` carrying queue/worker/session-store
  mechanics that own no semantic state. All
  layering rules are AST-enforced (see `tests/contract/test_architecture_invariants.py`):
  provider SDKs (`anthropic`/`openai`/`claude_agent_sdk`) may only be imported
  inside `src/hydramind/harness/`, only `src/hydramind/control/` mutates
  `RuntimeSession`, and inline `"You are"` prompts are confined to
  `orchestration/builtin_prompts/`.
- **Replaceable `HarnessBackend`** — a pluggable model/tool boundary with
  OpenAI-compatible, Claude SDK, and mock backends, plus role-based provider
  routing so different agent roles can map to different providers/models.
- **Goal-driven runtime** — end-to-end flow from `GoalSpec` to a model-produced
  planner, to an `ExecutionPlan`, to execute → verify → bounded auto-repair,
  keeping repair attempts capped to avoid unbounded loops.
- **Verifier composite** — deterministic verifiers combined with semantic
  (LLM-judge) verifiers under explicit quality contracts, so plan output is
  validated against typed acceptance criteria before being accepted.
- **Typed gating** — gates modeled as `GateContract`/`GateEvaluator`, covering
  policy, schema, timeout, human-in-the-loop (HITL), and verifier-feedback
  gates with typed verdicts.
- **Control-owned `RuntimeSession` source of truth** — single-writer state with
  compare-and-swap (CAS) updates, an execution lease with heartbeat, and
  expired-lease recovery so crashed executors release their work safely.
- **Agent–queue decoupling** — a `QueueAdapter` abstraction with an in-memory
  implementation providing visibility-timeout redelivery and a dead-letter
  queue (DLQ), decoupling agent execution from task transport.
- **Durable session store** — a SQLite-backed store for persisting
  `RuntimeSession` state across restarts.
- **Layered memory and observability** — working memory plus an episodic
  trace-projector; typed events with trace correlation, redaction of sensitive
  fields, and JSONL/OTel observers.
- **Hardened tool suite** — tools with sandbox-path enforcement, host
  allowlisting, timeouts, and credential-scope controls.
- **Governance evidence contracts** — typed evidence/audit contracts for
  capturing decisions and quality signals.
- **Reference implementations** — `examples/short_video` reference impl and a
  `mas-production-blog` scenario demonstrating the full framework.
- **CLI** — `run`, `worker`, `goal`, and `doctor` subcommands.

### Notes

- 749 tests; `ruff` and `mypy --strict` clean.
