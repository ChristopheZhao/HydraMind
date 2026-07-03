# Contributing to HydraMind

> **Effective from P1.** During the current **P0 (production closure)** sprint the
> project is closed development by the owner and is not accepting external pull
> requests. This guide describes the contribution model that takes effect at P1;
> it is published now so the process is transparent ahead of time.

Thanks for your interest in HydraMind. This document describes how the project is
developed and what we expect from contributions during the **alpha** phase.

> **Status:** HydraMind is in alpha. The public API (`ModelProvider`,
> `ExecutionHarness`, `RuntimeSession`, `GateContract`, `OrchestratorAgent`) is
> stabilizing but may still change between `0.1.x` releases. Breaking changes are
> called out in [`CHANGELOG.md`](CHANGELOG.md).

## Code of Conduct

This project adopts the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to uphold it.

## Development setup

HydraMind uses [uv](https://docs.astral.sh/uv/) and targets Python 3.11+.

```bash
git clone https://github.com/ChristopheZhao/HydraMind
cd HydraMind
uv venv && source .venv/bin/activate
uv sync --extra dev --extra celery --extra otel
```

## The green gate

Every change must pass the same gate that CI enforces. Run it locally before
opening a pull request:

```bash
uv run ruff check src tests        # lint
uv run mypy src                    # strict type check
uv run pytest                      # full suite (asyncio auto mode)
uv run pytest --cov=hydramind --cov-report=term-missing   # coverage
```

A PR is mergeable only when all of the above are green. Warnings are treated as
errors in the test configuration (`-W error` via `--strict-config`).

## Architecture boundaries are enforced, not advisory

HydraMind's value is its layer separation. These invariants are checked by
`tests/contract/test_architecture_invariants.py` and run as a dedicated CI job:

1. **No vendor SDK leak.** `anthropic`, `openai`, and `claude_agent_sdk` may be
   imported only under `src/hydramind/harness/`. Every other layer reaches the
   model through `ModelProvider` (and the execution episode through
   `ExecutionHarness`).
2. **Single SoT ownership.** Only the control layer (`SessionService`) mutates
   `RuntimeSession` state. Orchestration and the queue read it; they never write
   it.
3. **Prompts are config.** Role/system prompts live in
   `orchestration/builtin_prompts/` or external config — never inline in logic.

If your change needs to cross a boundary, that is a design discussion (open an
issue first), not a test to delete.

See [`docs/architecture/00-overview.md`](docs/architecture/00-overview.md) and the
ADR log [`docs/architecture/90-decisions.md`](docs/architecture/90-decisions.md)
for the rationale.

## How changes are planned

Non-trivial work is tracked as an SDD plan and advances in verifiable sprints
rather than one-shot commits:

1. **Plan** — a sprint slice with done-signals and an evidence checklist.
2. **Implement** — in an isolated branch/worktree, keeping the green gate green.
3. **Checkpoint** — a gate verdict recorded before the slice is considered closed.

Maintainers keep the full plan/checkpoint trail in an internal dev-notes area
outside the public tree; as a contributor you do **not** need to produce these
artifacts. A clear PR description tied to an issue is enough.

Small fixes (typos, docs, one-file bug fixes) do not need a plan — just a PR with
a clear description.

## Pull requests

- Branch from `main`; keep PRs focused on one logical change.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for messages
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).
- Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Add or update tests; new public behavior needs coverage.
- Describe what changed, why, and how you verified it (paste the green-gate
  output).

## Reporting bugs and proposing features

Open a [GitHub issue](https://github.com/ChristopheZhao/HydraMind/issues). For bugs,
include a minimal reproduction and the output of `uv run hydramind doctor env`
(it never prints secret values). For features, describe the use case and which
layer it belongs to before writing code.

## License

By contributing you agree that your contributions are licensed under the
[Apache-2.0](LICENSE) license that covers the project.
