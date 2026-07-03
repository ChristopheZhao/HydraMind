# Agent Operating Guide

This file is the contract for any AI agent (Claude Code, Codex, Cursor, etc.) operating on the HydraMind codebase.

## 1. Identity
- Git author identity: `zhaojj <398453241@qq.com>`. Do not change without explicit instruction.
- Project owner: zhaojj.

## 2. Workflow (Hard Requirement)
- **All non-trivial work uses the SDD skill + worktree pattern.** See `feedback_skill_worktree_workflow` memory.
- Sprint lifecycle:
  1. `sdd-plan-maintainer` — update plan under `dev-notes/plans/active/` (local, gitignored)
  2. `execution-contract-designer` — define this sprint's done signals + evidence
  3. Spawn `Agent(isolation: "worktree")` for implementation
  4. `checkpoint-gatekeeper` before sprint switch
  5. `layered-project-memory` at milestones

## 3. Architectural Invariants
- **Single ownership**: only the control layer mutates `RuntimeSession`. Orchestrator decides; never writes runtime state directly.
- **No harness leakage**: never `import anthropic` (or any other LLM SDK) outside `src/hydramind/harness/`. Cross-layer model access goes through `ModelProvider`; the execution episode goes through `ExecutionHarness`.
- **Prompts are config, not code**: never hardcode role prompts in `.py` files outside `src/hydramind/orchestration/builtin_prompts/`. User workflows supply prompts via YAML.
- **Gates are first-class**: state transitions are authorized by `GateResult`. Don't bypass with `if`-statements.

## 4. Reference Project
- Heritage source: `/mnt/d/code/agent/Opensource/vertical_application/short-video-maker`
- Treat read-only. Never modify the reference project from this repo.
- Key files to consult when abstracting: see `docs/architecture/00-overview.md` §4.

## 5. What NOT to do
- Don't add features beyond the current sprint scope (see active plan §5).
- Don't add backward-compat shims — we're pre-1.0 alpha.
- Don't add UI, RAG layers, or vector stores into the framework core.
- Don't write multi-paragraph docstrings or speculative abstractions.
