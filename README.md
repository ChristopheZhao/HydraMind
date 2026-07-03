# HydraMind

> A native multi-agent (MAS) framework with production-grade architectural discipline — **agent–queue decoupling**, **gating as a first-class contract**, and a deliberate split between **model/provider selection** and the **execution harness** that drives a task.

**Status:** alpha (replaceable execution harness landed; live Class 4/5 acceptance credential-gated) · Python 3.11+ · Apache-2.0

> **Current architecture (2026-06-19).** HydraMind now separates provider/model
> access from the replaceable execution shell. `ModelProvider` owns model calls
> and provider routing. `ExecutionHarness` owns the agent execution episode:
> context and memory policy, multi-turn tool loop, permissions, trace/evidence,
> budget/recovery, verifier integration, and subagent/team strategy. The default
> harness is `HydraMindExecutionHarness`; the alternate proof harness is
> `ExplicitSubmitExecutionHarness`. The retired `HarnessBackend` boundary is documented
> only as history in [`docs/architecture/10-harness-backend.md`](docs/architecture/10-harness-backend.md).

---

## Why HydraMind

Current multi-agent frameworks (LangGraph, AutoGen, CrewAI, Letta, ADK) bundle their own runtime — you cannot swap the underlying agent execution shell. HydraMind takes a different stance:

- **Provider selection ≠ harness replacement.** Switching DeepSeek → Kimi → GLM is *model/provider routing*. Replacing the execution shell around the model — context/memory policy, tool loop, permissions, trace/evidence, budget/recovery, subagent strategy — is *harness replacement*. These are two distinct contracts: `LLMProvider`/`ModelProvider` vs `ExecutionHarness`.
- **Agent–queue decoupling.** Workflow runtime state lives in a Source of Truth (`RuntimeSession`); the queue only schedules eligibility checks. No business logic in the worker pool.
- **Gating as a first-class contract.** Quality gates, policy gates, human-in-loop gates are typed `GateContract` artifacts checked by `GateEvaluator` — not callback hooks bolted on later.
- **Layered single-ownership.** Orchestration / Control / Gating / Governance layers, with clear single-ownership of durable runtime-influencing state.

## Architecture (Layered MAS)

```
┌──────────────────────────────────────────────────────────┐
│  Governance — contracts, quotas, audit (not in run path)  │
├──────────────────────────────────────────────────────────┤
│  Orchestration — OrchestratorAgent + WorkflowGraph        │
├──────────────────────────────────────────────────────────┤
│  Control — RuntimeSession (SoT) + SessionService          │
├──────────────────────────────────────────────────────────┤
│  Gating — GateEvaluator + GateResult/Contract             │
├──────────────────────────────────────────────────────────┤
│  ExecutionHarness — replaceable execution shell           │
│  default HydraMindExecutionHarness; alternate             │
│  ExplicitSubmitExecutionHarness; both run over ModelProvider        │
├──────────────────────────────────────────────────────────┤
│  ModelProvider — provider/model routing and model calls   │
└──────────────────────────────────────────────────────────┘
```

`ModelProvider` is held fixed during a harness swap. `ExecutionHarness` is the
swappable layer; N4 proves this by running the same native-team path under
`HydraMindExecutionHarness` and `ExplicitSubmitExecutionHarness` with provider, tools,
control, gates, and orchestration held fixed.

See [`docs/architecture/00-overview.md`](docs/architecture/00-overview.md) for full design.

## Status

HydraMind extracted production-proven MAS components from a reference project
iterated for ~6 months in pre-launch state, then closed the local runtime gaps
needed for a credible alpha. The current focus is acceptance closure for the
replaceable `ExecutionHarness`: provider access has been split to
`ModelProvider`, the retired backend abstraction is absent from production
source, and acceptance is reported as contract / plumbing / replay / live-agent /
live-MAS classes. Basis:
[`docs/architecture/95-execution-harness-correction.md`](docs/architecture/95-execution-harness-correction.md).
(The detailed SDD plan/checkpoint trail is kept in the maintainers' internal
dev-notes, not the public tree.)

| Phase | Scope | Status |
|---|---|---|
| Core extraction | Framework core + single reference impl | landed (alpha) |
| Harness correction | Split provider vs `ExecutionHarness`; retired backend abstraction; second multi-turn harness proof | landed (PLAN-20260619-001 N1-N4) |
| Live acceptance | Live-agent + live-MAS acceptance by `task + model/provider + harness + evaluator` | credential-gated; record not-proven when not run |

## Install (alpha)

```bash
# Not yet on PyPI. From source:
git clone https://github.com/ChristopheZhao/HydraMind
cd HydraMind
uv venv && source .venv/bin/activate
uv sync --extra dev --extra celery
```

## Quickstart (deterministic plumbing / replay)

The commands below are a minimal **plumbing/replay smoke**: `--provider mock`
selects the in-process deterministic `MockProvider`, not a live model. It
exercises CLI, control/session, queue, and tool wiring with reproducible inputs.
It is **not** live-agent or live-MAS acceptance — no model makes decisions, so it
proves nothing about prompt adherence, output quality, tool-choice reliability,
cost, or recovery. Mock is replay/test support, not a representative harness.
For acceptance taxonomy see
[`docs/architecture/95-execution-harness-correction.md`](docs/architecture/95-execution-harness-correction.md) §9.

```bash
uv run hydramind run examples/short_video/workflow.yaml \
  --provider mock \
  --input topic=Python
```

Durable local run:

```bash
uv run hydramind run examples/short_video/workflow.yaml \
  --provider mock \
  --input topic=Python \
  --session-store sqlite \
  --store-path var/hydramind.sqlite
```

Queue handoff through the worker host:

```bash
uv run hydramind run examples/short_video/workflow.yaml \
  --provider mock \
  --input topic=Python \
  --session-store sqlite \
  --store-path var/hydramind.sqlite \
  --enqueue-only

uv run hydramind worker once examples/short_video/workflow.yaml \
  --provider mock \
  --session-store sqlite \
  --store-path var/hydramind.sqlite \
  --session-id <session-id-from-json>
```

## Run a native-team example (offline replay regression)

HydraMind can run a **native multi-agent team** as a first-class workflow node.
Declare a team in a node's `config.mas_team` (members, collaboration protocol,
topology); the kernel scheduler drives the members as scheduled agent turns and
a team member can emit tool calls to produce artifacts.

> This example runs on `--provider mock` + a recorded fixture, so it is a
> **deterministic replay / plumbing regression**, not live-MAS acceptance. It
> proves the kernel wiring threads scheduled turns and a tool-call artifact
> reproducibly offline; it does **not** prove live multi-agent collaboration
> quality (no model decides). Live-MAS acceptance is a separate Class 5 run and
> must be recorded as not proven when live credentials or network are unavailable.

The `examples/native_team/` example replays a PIPELINE team
(researcher → analyst → writer) where the writer persists `brief.md`
via an `artifact.write_text` tool call — fully offline and reproducibly:

```bash
uv run hydramind run examples/native_team/workflow.yaml \
  --provider mock \
  --mock-fixture examples/native_team/mock_fixture.json \
  --artifact-root /tmp/hm-native-team
cat /tmp/hm-native-team/brief.md
```

`--provider mock` + `--mock-fixture` drive the team from an input-keyed
record/replay corpus, so the same fixture deterministically reproduces the
verified artifact with no network calls. See
[`examples/native_team/README.md`](examples/native_team/README.md) for the
`mas_team` YAML schema (members / protocol / topology) and how offline
determinism works.

## Local Provider Routing

Copy `.env.example` to `.env`, fill in keys, and load it before starting the Python process; or export the same variables in your shell. The default routing for domestic OpenAI-compatible providers is:

| Logical role | Default provider | Default model |
|---|---|---|
| `orchestrator` / `reviewer` | DeepSeek | `deepseek-v4-pro` |
| `planner` / `compactor` | Kimi | `kimi-k2.6` |
| `executor` | GLM | `glm-5.1` |

All model calls go through `ModelProvider`; orchestration passes the logical role
and the selected `ExecutionHarness` drives the execution episode. Provider
switching is model routing, not harness replacement.

Provider and tool diagnostics:

```bash
uv run hydramind doctor env --env-file .env --include-missing-template
uv run hydramind doctor providers --env-file .env --roles planner,executor --prompt "Reply with OK." --max-tokens 16 --timeout-seconds 30
uv run hydramind doctor tools --env-file .env --tool search.web,artifact.write_json,artifact.read_json,artifact.write_text,artifact.read_text,artifact.exists,artifact.list,time.now
uv run hydramind doctor tools --env-file .env --tool search.web,image.generate --live-tools
```

Doctor commands load `.env` but never print secret values. `doctor env` only reports whether required keys are present; `--include-missing-template` emits empty `KEY=` lines for missing variables. Tool diagnostics default to dry-run unless `--live-tools` is set; live mode preflights registered tool env requirements even before executing a tool. Built-ins include web search, image generation, JSON/text artifact read/write, artifact exists/list, and UTC timestamping.

To fill missing live tool keys without placing values in shell history:

```bash
uv run python scripts/set_env_secrets.py --env-file .env
```

For exact local alpha verification steps, see [`docs/operations/env-and-live-smoke.md`](docs/operations/env-and-live-smoke.md).

Local contract / plumbing / replay checks (no live model, no live tools):

```bash
uv run python scripts/p0_acceptance.py --mode local
```

`--mode local` exercises CLI, control/session, queue, tool registry, and
deterministic mock/replay wiring. It is **not** live-agent or live-MAS
acceptance. `--mode full` additionally runs a **live provider/tool smoke** plus
credential-gated Class 4/5 acceptance attempts (it first runs the local checks,
then fails fast at env preflight unless provider and live tool keys are present).
Class 4/5 live acceptance passes only when the live steps report provider and
harness identity; missing credentials/network mean "not proven":

```bash
uv run python scripts/p0_acceptance.py --mode full --env-file .env
```

## Project Layout

```
HydraMind/
├── src/hydramind/         # Framework core
│   ├── harness/           # ModelProvider contracts and provider implementations
│   ├── control/           # RuntimeSession + SessionService
│   ├── gating/            # GateEvaluator + contracts
│   ├── orchestration/     # OrchestratorAgent + WorkflowGraph + ExecutionHarness
│   ├── memory/            # Layered memory (short/long-term)
│   ├── observability/     # OTel-compatible event sink
│   ├── governance/        # Release/replay/evaluation evidence contracts
│   ├── queue/             # QueueAdapter (Celery, in-memory)
│   ├── runtime.py         # CLI/runtime assembly helpers
│   └── runtime_worker.py  # Queue execution host
├── examples/              # Reference implementations
│   └── short_video/       # First ref impl (P0)
├── scripts/               # Operator acceptance helpers
├── docs/
│   ├── architecture/      # Design documents
│   ├── operations/        # Operator runbooks
│   └── plans/             # SDD plans (governed by sdd-plan-maintainer)
└── tests/
```

## Contributing

P0 is closed development by the project owner: the project is not accepting
external pull requests yet. [`CONTRIBUTING.md`](CONTRIBUTING.md) documents the
alpha-phase contribution model and takes effect from P1.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).

## Acknowledgements

HydraMind's layered MAS architecture is distilled from a production short-video generation MAS. Design heritage is documented in `docs/architecture/00-overview.md`.
