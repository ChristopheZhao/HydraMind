# 40 — Orchestration

> Where harness + control + gating come together. The orchestrator is the user-facing entry point that drives a workflow to its terminal state.

## 1. Position in the Stack

```
User code → OrchestratorAgent.run_session(...)
              │
              ▼ for each ready node:
            ExecutionHarness.run_episode(...)
              │
              ▼ uses ModelProvider for model calls
              │
              ▼ build AgentReport
            ControlPlane.open_runtime_decision(session_id, report)
              │
              ▼ (CONTINUE / AWAIT_GATE / COMPLETE / FAIL)
```

The orchestrator is intentionally thin. It does **not** own state — that's the
control plane's job. It does **not** decide what's a gate — that's gating's job.
It does **not** know how to call a vendor SDK — provider/model access belongs
to `ModelProvider`. It does **not** own the tool loop, subagent/team strategy,
or trace/evidence policy — those belong to `ExecutionHarness`. It sequences
these layers and translates harness output into the wire types the control plane
expects.

## 2. Public Surface

```python
class OrchestratorAgent:
    def __init__(
        self,
        provider: ModelProvider,
        control: ControlPlane,
        workflow: WorkflowBlueprint,
        *,
        prompts: PromptLibrary | None = None,
        tool_provider: ToolProvider | None = None,
        execution_harness_factory: ExecutionHarnessFactory | None = None,
        max_steps: int = 100,
    ): ...

    async def start_session(
        self,
        *,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeSession: ...

    async def run_session(self, session_id: str) -> RuntimeDecision: ...

    async def resume_session(
        self, session_id: str, decision: GateDecisionInput | None = None
    ) -> RuntimeDecision: ...
```

`start_session` creates a fresh session from the blueprint. `run_session`
loops until the session reaches a terminal state or a gate halts it.
`resume_session` (optionally with a `GateDecisionInput`) restarts a halted run.

## 3. Algorithm

```
loop step 1..max_steps:
    session = control.service.get_session(session_id)
    if session.status in {COMPLETED, FAILED, CANCELLED}: return last_decision
    if session.status is WAITING_GATE: return AWAIT_GATE (caller must apply_decision)
    next_node = first node in topological order with status == QUEUED and all `requires` COMPLETED
    if next_node is None: return COMPLETE (no work left)
    role_prompt = prompts.lookup(blueprint.node_spec(next_node).role)
    tools = tool_provider.tools_for(next_node) if tool_provider else None
    outcome = await execution_harness.run_episode(ExecutionEpisodeRequest(...))
    report = outcome.report
    decision = await control.open_runtime_decision(session_id, report)
    if decision.kind != CONTINUE: return decision
```

The loop is intentionally simple: ready-node scheduling, one harness episode per
node, one control decision per episode. Replanning, branching, and parallel
execution are P1+ — they extend this loop without changing its shape.

## 4. Topological Scheduling

`WorkflowGraph` derives a deterministic ready-node order from `WorkflowBlueprint`:

```python
class WorkflowGraph:
    def __init__(self, blueprint: WorkflowBlueprint): ...
    def topological_order(self) -> tuple[str, ...]: ...
    def ready_nodes(self, session: RuntimeSession) -> list[str]: ...
```

Cycles are rejected at construction. `ready_nodes` returns nodes whose
`requires` are all in `COMPLETED` (or `STALE` for revision flows).

## 5. Prompts as Config

Role prompts live in a `PromptLibrary` — a name → template lookup. Templates
support `{variable}` interpolation against the agent report payload and session
input. The library can be loaded from YAML so prompts are versioned alongside
the workflow blueprint.

```python
class PromptLibrary:
    @classmethod
    def from_yaml(cls, path: str | Path) -> PromptLibrary: ...
    def lookup(self, role: str) -> PromptTemplate: ...
```

This addresses the reference project's `template_manager.py` debt where role
prompts were hard-coded Python strings.

## 6. Output Parsing

The active `ExecutionHarness` returns an `ExecutionEpisodeOutcome` carrying an
`AgentReport` plus typed model invocation, tool, verifier, trace, failure, and
recovery evidence. The default `HydraMindExecutionHarness` builds that outcome
from the existing node-invocation path. The lower-level model invocation returns
free-form `InvocationResult.content` plus structured `tool_calls`; the default
report-building strategy is:

1. If `tool_calls` is non-empty and a `ToolRunner` is configured → execute the calls, feed `ToolResultBlock`s back into the harness, and continue until a non-tool turn or `max_tool_rounds`.
2. If `tool_calls` remains non-empty without a runner → surface the calls as structured output.
3. If `content` parses as JSON → use as `AgentReport.output`.
4. Otherwise → `AgentReport.output = {"text": content}`.

Users can supply a custom `ReportBuilder` if their workflow needs richer parsing.

For goal-driven runs, `GoalDrivenOrchestratorAgent` wires a `CompositeVerifierRunner`
in which determinism is reserved for SAFETY/BOUNDARY only — `TaskContractVerifierRunner`
(artifact existence) → `ArtifactContainmentVerifierRunner` (artifact-root containment) —
followed by the agent `SemanticArtifactVerifierRunner`, which is the default
verify-good-enough decision-maker (it no-ops when the quality contract carries no
`semantic_rubric`, keeping offline runs deterministic). The default stack order is
assembled at the runtime edge by `hydramind.runtime_verification`; `runtime.py`
delegates to that boundary and does not own concrete verifier runner ordering. The
rule-based content-quality verifier and the rule repair policy were removed in S97;
verify-good-enough and repair are agent decisions. The quality contract on
`TaskContract.quality_contract` (loaded via
`--quality-contract <path.json>`) supplies the semantic rubric. The
`VerifierFeedbackEvaluator` gate MECHANISM remains (authorization, not a decision); on a
verifier-failure gate the orchestrator asks the planner agent's `revise_plan` to repair,
bounded only by a deterministic max-iterations safety guard.

## 7. Native MAS Collaboration

Native multi-agent collaboration is expressed with public contracts under
`hydramind.mas`:

- `AgentSpec` describes one role-bearing participant, including its declared
  tool subset.
- `TeamSpec` groups agents and validates unique member ids plus coordinator
  membership.
- `CollaborationProtocol` describes routing and arbitration policy without
  embedding prompts in code.
- `SharedWorkspace` is a lightweight scoped identity/metadata marker for a team.
  (Its `artifact_refs`/`memory_refs` fields were removed in S102 — collaboration
  data flows through the message-passing kernel seam, peer transcripts, not
  workspace references.)

Goal-derived tasks carry these specs as typed fields (`PlanTaskSpec.agent` and
`PlanTaskSpec.team`). Projection to `WorkflowNodeSpec.config` writes
`mas_agent`/`mas_team` JSON and selects `execution_mode="team"` for teams. A team
executes through the S91 kernel scheduler (`select_strategy`/`select_next_turn`)
inside `NativeTeamExecutor`: each topology/mode is an interpreted `SchedulingStrategy`
(BROADCAST/PIPELINE/COORDINATOR/DEBATE/VOTE/DELEGATION), and node dispatch is
type-directed via `NodeExecutionMode` (no magic-string `execution_mode` sniffing).

The legacy `config.subagents` / `execution_mode="subagent_group"` path was RETIRED
(S98) — it is removed from source and fails closed; a node with an out-of-envelope
collaboration value is rejected by `require_executed_team` before execution. Teams
are expressed only via the native MAS contracts (`TeamSpec`/`AgentSpec`).

`OrchestratorAgent` should stay responsible for ready-node scheduling, harness
episode invocation, and control-plane handoff. Direct model calls go through the
fixed `ModelProvider` held by `ProviderExecutionHarnessRuntime`; subagent/team
strategy, member tool filtering, collaboration payload shaping, and member
tool-call draining are execution-harness policy composed from the collaboration
module.

The tool boundary is explicit: if an agent or team member declares tools, the
enclosing task must allow those same tools. Runtime execution filters the tools
handed to each member and fails closed if a raw workflow attempts to request a
member tool unavailable to the node.

Workflow YAML follows the same least-privilege rule. Nodes receive no default
tools unless they declare an allowlist with either top-level `tools` or
`config.tools`:

```yaml
nodes:
  - key: write
    role: writer
    tools: [artifact.write_text]
  - key: review
    role: reviewer
    config:
      tools: [artifact.read_text]
    requires: [write]
```

The runtime normalizes duplicate and padded names, validates that declared tools
exist, and passes only those specs to the harness for the matching node. A
workflow node that omits both declarations receives `[]`, and undeclared tool
calls fail before any tool runner executes.

## 8. Ownership Invariants

1. The orchestrator does not write to `SessionStore`. Only `SessionService` does.
2. The orchestrator does not import any vendor SDK. Provider modules under `hydramind.harness` do.
3. The orchestrator does not evaluate gates. Only `GateEvaluator`s do.
4. The orchestrator does not treat provider switching as harness replacement.
5. The orchestrator injects a replaceable `ExecutionHarness` without moving durable state mutation or gate authorization into that harness.

## 9. What's Out of P0

- **Parallel node execution** — workflow nodes are scheduled one at a time in P0; concurrency is P1.
- **Replanning** — orchestrator does not re-derive the blueprint; that's P2.
- **Full protocol engine** — S53a/S53b establish typed native MAS specs and a
  collaboration executor bridge. Rich protocol execution for
  debate/vote/arbitrated routing is a later slice.

## 10. Heritage Diff vs Reference Project

| Reference | HydraMind P0 | Action |
|---|---|---|
| `orchestrator.py` (2290 LOC) | `agent.py` (~250 LOC target) | Drop video-specific decision logic; that lived in apply_deriver |
| Hard-coded role-prompt strings | `PromptLibrary` config | External, versioned |
| `agents/<role>.py` per role | `WorkflowNodeSpec.role` string lookup | Data-driven |
| Decision graph in code | `WorkflowGraph` topological from blueprint | Declarative |
