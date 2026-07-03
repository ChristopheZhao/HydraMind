# 30 — Gating as a First-Class Contract

> Differentiation Anchor #2. In most MAS frameworks, "checkpoints" are callback hooks bolted onto a workflow. In HydraMind, gates are typed contracts evaluated by named evaluators, and they are the only authorized way to advance the state machine through a checkpoint.

## 1. Why Gating Deserves Its Own Layer

The reference project (short-video-maker) encoded gate authority as a hard-coded dict:

```python
_GATE_AUTHORITY = {
    "workflow_video_audio_delivery": {(AgentType.VIDEO_GENERATOR, "scene_video_completed")},
    ...
}
```

This conflated three orthogonal concerns:
1. **Which gate** can fire here (registration)
2. **What it checks** (evaluation logic)
3. **Who is authorized to trigger it** (policy)

HydraMind separates them:

| Concern | HydraMind primitive |
|---|---|
| Registration | `GateRegistry` |
| Evaluation | `GateEvaluator` Protocol |
| Trigger authority | `GateContract.triggers` + `applies_to_nodes` |
| Outcome | `Gate` (from `hydramind.control.models`) |
| Decision | `GateDecision` (also from control) |
| Task evidence | `VerifierResult` / `FeedbackRecord` on `AgentReport` |

The control plane (S2) accepts a single `GateFn`. Gating provides
`GateRegistry.to_gate_fn()` that composes any number of evaluators into that
single function. The control plane stays oblivious to evaluator details.

## 2. GateContract — the typed declaration

```python
class GateSeverity(StrEnum):
    ADVISORY    # outcome PASS or REQUIRES_DECISION; BLOCK is not allowed
    BLOCKING    # outcome can be BLOCK; failure terminates the session

class GateContract(BaseModel):
    name: str
    description: str = ""
    triggers: tuple[str, ...] = ()         # boundary_event names that fire this gate
    applies_to_nodes: tuple[str, ...] = () # empty = all nodes
    severity: GateSeverity = GateSeverity.ADVISORY
    timeout_seconds: float | None = None   # for TimeoutEvaluator-style logic
    metadata: dict[str, Any] = {}
```

A `GateContract` is data: it can live in YAML/JSON, be versioned, be linted in
CI, and be quoted in audit logs. An evaluator is the code that consumes a
contract and produces a `Gate`.

## 3. GateEvaluator — the runnable evaluator

```python
class GateEvaluator(Protocol):
    name: str
    contract: GateContract

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        """Return a Gate if this evaluator applies; None to skip."""
```

An evaluator can:
- Return `None` to opt out (e.g., wrong node or wrong boundary event).
- Return `Gate(outcome=PASS)` to authorize advance.
- Return `Gate(outcome=REQUIRES_DECISION)` to halt for human/system decision.
- Return `Gate(outcome=BLOCK)` to fail the session (only if `severity=BLOCKING`).

Evaluators should prefer typed `VerifierResult` / `FeedbackRecord` evidence
when they need to explain task delivery status or repair guidance. A `Gate`
answers whether the state machine may advance; verifier feedback answers why a
task is or is not acceptable.

`GateRegistry.to_gate_fn` walks evaluators with **halt-wins** semantics:

- `BLOCK` (only allowed from `BLOCKING`-severity evaluators) → return immediately.
- `REQUIRES_DECISION` → return immediately.
- `PASS` → remember as the first authorizing signal, but keep scanning — a later
  evaluator can still halt the run.
- All `PASS` / no evaluators fire → return the first `PASS` (or `None`).

This means a "schema OK" evaluator does not preempt a downstream "human review
required" evaluator. Order still matters only among evaluators that produce the
same outcome class.

## 4. Built-in Evaluators (P0)

| Evaluator | Purpose |
|---|---|
| `SchemaCheckEvaluator` | Validate `AgentReport.output` against a Pydantic model; PASS or REQUIRES_DECISION on schema violation |
| `PolicyEvaluator` | Declarative DSL: `(predicate, verdict)` pairs over the report payload |
| `TimeoutEvaluator` | If the latest attempt has been RUNNING longer than `contract.timeout_seconds`, return REQUIRES_DECISION |
| `HumanInLoopEvaluator` | Always returns REQUIRES_DECISION — the explicit "wait for human review" gate |
| `VerifierFeedbackEvaluator` | Converts failed typed `VerifierResult`s into REQUIRES_DECISION, checks goal-level required-tool progress from the control-owned tool ledger, and passes all-success verifier evidence |

Verifier evidence should be produced before the control decision, not inside the
gate. `TaskContractVerifierRunner` is the first deterministic runner: it checks
`TaskContract.expected_artifacts` under the run artifact root, appends
`VerifierResult` / `FeedbackRecord` to the `AgentReport`, and leaves state
mutation to `ControlPlane` + `SessionService`. The gate remains the authorization
surface that decides whether failed verifier feedback pauses the session.

Required-tool progress is the narrow exception that belongs in the gate
evaluator because its evidence source is already runtime state:
`GoalSpec.required_tools` declares mandatory tool use, and
`RuntimeSession.nodes[*].attempts[*].tool_executions` is the control-owned source
of truth for whether those tools succeeded. If a required tool is missing and no
pending plan node is already scoped to that tool, `VerifierFeedbackEvaluator`
emits a failed `required_tools.completed` verifier result plus a
`verifier.required_tools` feedback record. The doctor CLI may report this
evidence, but it does not own the routing policy.

User code adds custom evaluators by implementing the Protocol.

## 5. Composition with Control Plane

```python
from hydramind.control import ControlPlane, SessionService
from hydramind.gating import GateRegistry, SchemaCheckEvaluator, HumanInLoopEvaluator

registry = GateRegistry()
registry.register(SchemaCheckEvaluator(...))
registry.register(HumanInLoopEvaluator(
    contract=GateContract(name="final_review", applies_to_nodes=("publish",))
))

plane = ControlPlane(service, gate_fn=registry.to_gate_fn())
```

The control plane sees the same `GateFn` signature as in S2. Existing tests
keep passing. The richer semantics are entirely opt-in.

## 6. Ownership Invariants

1. Only `gating` defines `GateEvaluator`. `control` only sees `GateFn`.
2. `GateContract` is data; do not embed logic in it. Logic lives in the evaluator.
3. Evaluators do not mutate `RuntimeSession`. They only produce `Gate`s.
4. The control plane is the only consumer of `Gate.outcome`.
5. Gate authority is not a global registry — it's the `triggers` + `applies_to_nodes` fields of each `GateContract`. No `_GATE_AUTHORITY` dict.

## 7. What's Out of P0

- **LLM-as-judge verifier runner** — requires harness backend wired through; lands in a later verifier slice. `VerifierFeedbackEvaluator` only consumes typed results.
- **Distributed evaluator workers** — evaluators are local async calls; remote evaluators come in P2.
- **Gate DSL parser** — `PolicyEvaluator` accepts a Python callable in P0; a YAML/JSON DSL is P3.
- **Differential gates** — comparing N reports against each other for consensus is P3.

## 8. Heritage Diff vs Reference Project

| Reference (`short-video-maker`) | HydraMind P0 | Action |
|---|---|---|
| `_GATE_AUTHORITY` hard-coded dict | `GateContract.triggers` + `applies_to_nodes` | Data, not code |
| `AudioDeliveryGateEvaluator` class | `examples/short_video/` after S6 | Out of framework |
| Gate as ad-hoc dict in `report['gate_triggers']` | Typed `Gate` model + `GateContract` | Static checking |
| Gate evaluator constructed inside ControlPlane | Composed externally, injected via `registry.to_gate_fn()` | Inversion of control |
