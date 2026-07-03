# Native-team example (offline replay / plumbing regression)

A runnable, medium-complexity multi-agent example: a **native team** of three
members runs a PIPELINE and writes a file artifact (`brief.md`) — reproducibly
and fully offline.

> **Evidence class: replay / plumbing — NOT live-agent or live-MAS acceptance.**
> This example runs on `--provider mock` + a recorded fixture
> (`MockProvider.from_fixture`). No live model decides anything, so it proves the
> *wiring*: a declared collaboration topology drives scheduled agent turns, a
> team member emits a tool call, the tool-drain executes it under the artifact
> root, and the runtime's verifier stack checks the produced artifact —
> deterministically and offline. It does **not** prove live multi-agent
> collaboration quality, prompt adherence, tool-choice reliability, cost, or
> recovery. Per [`../../docs/architecture/95-execution-harness-correction.md`](../../docs/architecture/95-execution-harness-correction.md)
> §9 this is Class 2/3 evidence. Live-agent (Class 4) and live-MAS (Class 5)
> acceptance are separate credential-gated runs and must be recorded as
> not-proven when credentials or network are unavailable.

## What it does

One workflow node (`collaborate`) declares a native MAS team via
`config.mas_team`. The team runs a **research → analysis → writing pipeline**:

| member       | role       | reads                      | produces                          |
| ------------ | ---------- | -------------------------- | --------------------------------- |
| `researcher` | researcher | the task                   | findings                          |
| `analyst`    | analyst    | the researcher's findings  | prioritized insights              |
| `writer`     | writer     | the analyst's insights     | `brief.md` via `artifact.write_text` |

- **Topology = PIPELINE**: member N reads member N-1's output (the kernel
  `PipelineStrategy` threads a peer's prior turn into the next member's context,
  speaker-attributed).
- **The writer produces a verified artifact**: it emits an `artifact.write_text`
  tool call; the tool-drain executes the write under the artifact root; the
  runtime's `TaskContractVerifierRunner` + `ArtifactContainmentVerifierRunner`
  verify `brief.md` (declared in `config.task_contract.expected_artifacts`).

## Run it (offline)

```bash
hydramind run examples/native_team/workflow.yaml \
  --provider mock \
  --mock-fixture examples/native_team/mock_fixture.json \
  --artifact-root /tmp/hm-native-team
cat /tmp/hm-native-team/brief.md
```

Or the wrapper:

```bash
examples/native_team/run_scenario.sh
```

The run prints `"status": "completed"` and writes `brief.md` under the artifact
root. No network or live provider calls are made.

## How offline determinism works

`--provider mock` is the in-process `MockProvider`. `--mock-fixture` loads an
**input-keyed record/replay corpus** (`MockProvider.from_fixture`): a JSON map
from `invocation_fingerprint` (a SHA-256 of the agent's input — messages, role,
etc.) to a recorded response. Each team member's turn matches its fingerprint
and returns the recorded output **regardless of order**, so the same fixture
always drives the same team behavior — including the writer's
`artifact.write_text` tool call.

The artifact-producing turn is keyed only on the team transcript
(path-independent), so the produced `brief.md` is **byte-identical across any
`--artifact-root`** — the reproducibility property of this offline replay
regression (not live-agent evidence). (The writer's
post-tool reply is left to the deterministic mock echo because the tool result
embeds the absolute artifact path; scripting it would couple the fixture to one
artifact root.)

Regenerate the fixture after changing the workflow or member prompts:

```bash
.venv/bin/python examples/native_team/generate_fixture.py
```

## The `mas_team` YAML schema

A workflow node becomes a native team by declaring `config.mas_team` (a
`TeamSpec`). The loader carries it verbatim; `resolve_node_execution_mode`
routes a `mas_team` node to TEAM execution.

```yaml
nodes:
  - key: collaborate
    role: coordinator
    tools: [artifact.write_text]        # node-level tool allowlist
    config:
      mas_team:
        id: brief-team
        protocol:
          mode: team                    # team | debate | vote | delegation
          topology: pipeline            # broadcast | pipeline | coordinator
          aggregation: collect          # collect | coordinator_summary | vote
          # arbitration: coordinator    # coordinator | none | majority
          # coordinator_id: writer      # required for coordinator/delegation
        tools: [artifact.write_text]    # team-level tool allowlist
        members:
          - id: researcher
            role: researcher
            instructions: "You are the researcher. …"   # prompt-as-config
          - id: analyst
            role: analyst
            instructions: "You are the analyst. …"
          - id: writer
            role: writer
            tools: [artifact.write_text]                 # member tool allowlist
            instructions: "You are the writer. … call artifact.write_text …"
      task_contract:
        objective: Produce a verified one-page brief artifact.
        expected_artifacts: [brief.md]  # verified by the runtime verifier stack
```

Notes:

- **Prompts are config**: member `instructions` live in YAML, not in Python.
- **Tool scoping**: a member may only use tools in both the node/team allowlist
  and its own `tools`. Only the writer can write the artifact.
- **Executed envelope**: `topology`/`aggregation`/`mode` values must be ones the
  kernel actually executes (PIPELINE/BROADCAST/COORDINATOR/DEBATE/VOTE/
  DELEGATION); unexecuted combinations fail closed.

## Files

| file                  | purpose                                                        |
| --------------------- | ------------------------------------------------------------- |
| `workflow.yaml`       | the workflow with the `mas_team` PIPELINE node + task contract |
| `mock_fixture.json`   | input-keyed offline record/replay corpus                       |
| `generate_fixture.py` | regenerates `mock_fixture.json`                                 |
| `run_scenario.sh`     | thin offline wrapper that runs the example and shows the artifact |
| `README.md`           | this file                                                       |
