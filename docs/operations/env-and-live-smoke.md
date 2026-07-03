# Env and Live Smoke Runbook

This runbook is for local alpha operators who need to verify HydraMind with live LLM and tool providers.

## 1. Secret Placement

Copy `.env.example` to `.env` in the repository root and fill values locally. The `.gitignore` contract keeps `.env` and `.env.*` out of Git, while allowing `.env.example`.

Required live provider keys:

- `DEEPSEEK_API_KEY`
- `KIMI_API_KEY`
- `GLM_API_KEY`

Required live tool keys:

- `BRAVE_SEARCH_API_KEY`
- `DOUBAO_API_KEY`

Optional live tool overrides:

- `DOUBAO_IMAGE_MODEL` defaults to `doubao-seedream-5-0-260128`
- `DOUBAO_IMAGE_API_URL` defaults to `https://ark.cn-beijing.volces.com/api/v3/images/generations`

Do not place secret values in shell commands, test assertions, docs, checkpoint artifacts, or commit messages. The doctor commands print only key names and boolean presence.

## 2. Preflight

Run the env doctor first:

```bash
.venv/bin/hydramind doctor env \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --include-missing-template
```

Expected pass condition:

- `profiles.providers.ok` is `true`
- `profiles.tools.ok` is `true`
- `missing_template` is absent or empty

If `missing_template` includes `BRAVE_SEARCH_API_KEY=` or `DOUBAO_API_KEY=`, add those variables to the ignored `.env` file and rerun the preflight.

To avoid putting secret values in shell history, use the interactive helper:

```bash
.venv/bin/python scripts/set_env_secrets.py \
  --env-file /mnt/d/code/agent/framework/hydramind/.env
```

It prompts for `BRAVE_SEARCH_API_KEY` and `DOUBAO_API_KEY` with hidden input, updates the ignored `.env`, and prints only key names plus `added`/`updated` status.

## 3. Provider Smoke

Run all role routes:

```bash
.venv/bin/hydramind doctor providers \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --roles orchestrator,planner,executor \
  --prompt "Reply with OK." \
  --max-tokens 16 \
  --timeout-seconds 30
```

Expected pass condition:

- `orchestrator` returns `ok: true` with `deepseek-v4-pro`
- `planner` returns `ok: true` with `kimi-k2.6`
- `executor` returns `ok: true` with `glm-5.1`

## 4. Tool Smoke

Run dry-run and local artifact tools:

```bash
.venv/bin/hydramind doctor tools \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --artifact-root /tmp/hydramind-s7-checkpoint-tools \
  --tool search.web,artifact.write_json,artifact.read_json,artifact.write_text,artifact.read_text,artifact.exists,artifact.list,time.now
```

Expected pass condition:

- `total_tools` is at least `10`
- all selected executions return `ok: true`
- `search.web` runs in dry-run mode unless `--live-tools` is set

Run live network tools:

```bash
.venv/bin/hydramind doctor tools \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --artifact-root /tmp/hydramind-s7-live-tools \
  --live-tools \
  --tool search.web,image.generate
```

Expected pass condition:

- `missing_env` is empty
- `search.web` returns `ok: true`
- `image.generate` returns `ok: true`

Live HTTP failures should be treated as diagnostics for provider account, network, model id, or endpoint compatibility. Do not turn live failures into green tests.

## 5. Provider-Tool Loop Smoke

Run a low-risk provider-driven tool loop:

```bash
.venv/bin/hydramind doctor tool-loop \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --tool time.now \
  --trace-path /tmp/hydramind-tool-loop-trace.jsonl \
  --timeout-seconds 60
```

Expected pass condition:

- `ok` is `true`
- `reason` is `passed`
- `tool_call_started` is at least `1`
- `tool_call_completed` is at least `1`
- `model_invoke_completed` is at least `2`
- `trace_path` points to a JSONL trace artifact

This differs from `doctor tools`: the provider must emit the ToolCall, the
framework must execute it, the tool result must be fed back to the model, and
observability must record the trajectory. A text-only provider response should
return `reason: no_tool_call` and fail the smoke.

## 6. Goal Scenario Smoke

Normal `hydramind goal` runs also accept `--required-tool`; use it when a real
goal must prove tool use from the runtime ledger instead of relying on prompt
wording alone. Required tools must also be listed in `--tool`, and immediate
goal output includes `required_tool_evidence` after execution. The doctor
command below remains the focused smoke wrapper for provider/tool validation.
Normal goal runs also accept `--expected-artifact` and `--artifact-root` when
the delivery contract is "this path must exist under the run artifact root".
That path is enforced by the existing task-contract verifier; it does not judge
artifact content quality.
For controlled local process execution, `process.run` must be listed in
`--tool`, explicitly approved with `--approved-tool process.run`, and constrained
with `--allow-process-command <argv0>`. Operators may further restrict allowed
argument shapes with repeated `--allow-process-argv-prefix '<argv0> <arg>...'`
entries. The worker path uses the same controls on `hydramind worker goal-once`;
this is command and argv-prefix allowlisting, not OS sandboxing.

Run a goal-driven scenario that validates required tools from the
control-owned `ToolExecution` ledger and uses the JSONL trace only as detailed
trajectory evidence:

```bash
.venv/bin/hydramind doctor goal-scenario \
  --env-file /mnt/d/code/agent/framework/hydramind/.env \
  --backend env \
  --planner auto \
  --live-tools \
  --tool search.web,image.generate \
  --required-tool search.web,image.generate \
  --trace-path /tmp/hydramind-goal-scenario-trace.jsonl \
  --timeout-seconds 120
```

Expected pass condition:

- `ok` is `true`
- `reason` is `passed`
- `session_status` is `completed`
- each `required_tools[*].succeeded` is `true`
- `tool_executions` contains `search.web` and `image.generate` records from
  `RuntimeSession`
- `trace_path` points to a JSONL trace artifact with model/tool events

This differs from `doctor tool-loop`: the task enters through `GoalSpec` and
the normal goal runtime, so planner/tool scope/runtime/ledger wiring are checked
together. A provider that answers without calling a required tool should return
`reason: missing_required_tool`. During execution, missing required tools are
also fed through the framework's `verifier_feedback` repair loop; the doctor
command only reports the resulting ledger/trace evidence and does not decide the
next task itself.

## 7. S7 Checkpoint

After provider and live tool smoke pass, rerun the S7 checkpoint:

```bash
python3 /home/zhaojj/.codex/skills/checkpoint-gatekeeper/scripts/gate_ops.py \
  --root . \
  check \
  --id PLAN-20260517-001 \
  --checkpoint S7-production-runtime
```

Expected pass condition:

- verdict is `pass` or `auto_fixed_pass`
- quality commands pass
- live tool env preflight passes
- live `search.web,image.generate` doctor passes

Only after that should `hm/s7-prod-closure` be merged into `main`.

## 8. Acceptance Wrapper

The same local smoke path can be run through the checked-in wrapper:

```bash
.venv/bin/python scripts/p0_acceptance.py --mode local
```

Full acceptance includes live provider/tool smoke and the S7 checkpoint:

```bash
.venv/bin/python scripts/p0_acceptance.py \
  --mode full \
  --env-file /mnt/d/code/agent/framework/hydramind/.env
```

The wrapper prints commands, JSON diagnostics, and pass/fail status. It relies on the same doctor commands, so it may print key names and presence booleans but not secret values. Full mode stops after env preflight if required provider or live tool keys are missing; once env preflight passes, provider smoke is bounded by both provider HTTP timeout and a subprocess timeout.

## 9. MAS Production Blog Scenario (S52d)

The `examples/scenarios/mas-production-blog/` directory bundles the
production-blog scenario for `PLAN-20260523-001` S52d. Unlike the doctor
smoke commands above, this is the **acceptance scenario** for the
quality-contract path: it must produce a roughly 20,000-character Chinese
technical blog that satisfies a published `GoalArtifactQualityContract`
(length, required sections, reference URLs, image refs, local-asset
containment) and a 3-check semantic rubric (`technical_depth`,
`source_grounding`, `non_mechanical_expression`).

### Prerequisites

- All keys from §1 present and verified by `hydramind doctor env`:
  `DEEPSEEK_API_KEY`, `KIMI_API_KEY`, `GLM_API_KEY`,
  `BRAVE_SEARCH_API_KEY`, `DOUBAO_API_KEY`.
- Live tool smoke (`hydramind doctor tools --live-tools --tool
  search.web,image.generate`) returns `ok: true`.
- At least ~200 MB of free disk space for artifacts, images, and the JSONL
  trace; SQLite session store path writable.
- Outbound network reachability to Brave Search and Doubao Image APIs.

### Command

```bash
bash examples/scenarios/mas-production-blog/run_scenario.sh
```

Environment overrides (all optional):

- `HYDRAMIND_ENV_FILE` — path to the `.env` file (default `<repo>/.env`)
- `HYDRAMIND_ARTIFACT_ROOT` — artifact root override
  (default `artifacts/scenarios/mas-production-blog/<utc-timestamp>`)
- `HYDRAMIND_STORE_PATH` — SQLite session store path (default `var/blog.sqlite`)
- `HYDRAMIND_BIN` — `hydramind` binary override (default `<repo>/.venv/bin/hydramind`)

After the run, collect a redaction-safe evidence package:

```bash
python examples/scenarios/mas-production-blog/evidence_collector.py \
  --session-store sqlite --store-path var/blog.sqlite \
  --session-id <session-id-from-run> \
  --artifact-root artifacts/scenarios/mas-production-blog/<run-id>/ \
  --trace-path artifacts/scenarios/mas-production-blog/<run-id>/trace.jsonl \
  --output-dir artifacts/scenarios/mas-production-blog/<run-id>/evidence/
```

### What counts as pass

- `hydramind goal` returns `session.status == completed` and no
  deterministic verifier failure on the final attempt.
- Every semantic-rubric check returns a typed `VerifierResult` with
  `passed=true` (each `score >= min_score = 0.6`).
- `evidence_collector.py` exits `0` and writes `evidence/manifest.json`
  with `redaction_check: passed`.
- The evidence directory contains the blog markdown copy
  (`evidence/blog.md`), the referenced local images under
  `evidence/assets/`, the trace copy (if a trace path was provided), and
  the `ledger.json` / `verifier_results.json` / `planner_diagnostics.json`
  JSON sidecars.

A pass on this scenario validates the full quality-contract path:
deterministic checks, semantic rubric routed through the active `ExecutionHarness`
path over `ModelProvider`,
verifier-feedback repair loop, and control-owned `ToolExecution` ledger.

### What is OUT of scope of this scenario

This scenario **does not** prove and **does not** claim:

- Distributed-worker correctness (cross-process lease arbitration,
  DLQ/priority lanes, broker visibility SLAs).
- Durable replay or cross-session result recovery; tool side effects are
  not compensated on restart.
- OS / container sandboxing — `process.run` is approval-gated command and
  argv-prefix allowlisting only.
- A trace flag on `hydramind goal` itself. The CLI does not yet expose a
  `--trace-path`; to capture a JSONL trace, drive `runtime.run_goal` from
  a Python entry point with `Emitter([JsonlObserver(path)])` and pass the
  resulting path to `evidence_collector.py`.

If the scenario fails because of one of the above edges, the response is
to escalate to the next sprint, not to weaken the published
`quality_contract.json`.
