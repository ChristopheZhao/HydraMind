"""Goal planning contracts and contract-level projection helpers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hydramind.control import (
    GoalArtifactQualityContract,
    TaskContract,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.mas import AgentSpec, TeamSpec, require_executed_team
from hydramind.orchestration.memory_context import MemoryContextPolicy


class NodeExecutionMode(StrEnum):
    """How a workflow node is invoked by the orchestrator.

    The single source of truth for node dispatch (ADR-0007: type-directed
    dispatch, not magic-string config sniffing). The planner advertises these
    values, ``resolve_node_execution_mode`` parses node config into this enum at
    the boundary, and ``OrchestratorAgent`` dispatches over a table keyed on it;
    a lock-step contract test asserts the table covers exactly these members.
    """

    DIRECT = "direct"
    SUBAGENT = "subagent"
    TEAM = "team"


def resolve_node_execution_mode(node_config: dict[str, Any]) -> NodeExecutionMode:
    """Parse a node config into its typed execution mode (fail-closed).

    A node carrying a ``mas_team`` block is a TEAM. Otherwise the
    ``execution_mode`` string (default ``"direct"``) maps to a
    :class:`NodeExecutionMode` member; any unknown/removed value (e.g. a retired
    legacy collaboration mode or a typo) raises ``ValueError`` rather than
    silently degrading or routing to a deleted path.
    """

    if "mas_team" in node_config:
        return NodeExecutionMode.TEAM
    raw = str(node_config.get("execution_mode") or NodeExecutionMode.DIRECT.value)
    try:
        return NodeExecutionMode(raw)
    except ValueError as exc:
        valid = "|".join(mode.value for mode in NodeExecutionMode)
        raise ValueError(
            f"unknown node execution_mode {raw!r}; expected one of {valid}"
        ) from exc


class PlanTaskSpec(BaseModel):
    """One executable task in a goal-derived plan."""

    model_config = ConfigDict(frozen=True)

    key: str
    role: str = "executor"
    description: str = ""
    agent: AgentSpec | None = None
    team: TeamSpec | None = None
    requires: tuple[str, ...] = ()
    contract: TaskContract = Field(default_factory=TaskContract)
    tools: tuple[str, ...] = ()
    execution_mode: str = "direct"
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_native_mas_contracts(self) -> Self:
        if self.agent is not None and self.team is not None:
            raise ValueError("PlanTaskSpec cannot declare both agent and team")
        if self.agent is not None:
            require_declared_tools_allowed(
                declared=self.agent.tools,
                allowed=self.tools,
                owner=f"agent {self.agent.id!r}",
            )
        if self.team is not None:
            require_declared_tools_allowed(
                declared=self.team.declared_tools(),
                allowed=self.tools,
                owner=f"team {self.team.id!r}",
            )
            require_executed_team(self.team)
        return self

    def to_workflow_node(self) -> WorkflowNodeSpec:
        config = dict(self.config)
        if self.agent is not None:
            config.setdefault("mas_agent", self.agent.model_dump(mode="json"))
        if self.team is not None:
            config["mas_team"] = self.team.model_dump(mode="json")
            config["execution_mode"] = "team"
        if self.tools:
            config.setdefault("tools", list(self.tools))
        if self.execution_mode and self.team is None:
            config.setdefault("execution_mode", self.execution_mode)
        if self.contract != TaskContract():
            config.setdefault("task_contract", self.contract.model_dump(mode="json"))
        return WorkflowNodeSpec(
            key=self.key,
            role=self.role,
            description=self.description,
            requires=self.requires,
            config=config,
        )


class GoalSpec(BaseModel):
    """User-facing goal input for dynamic MAS execution."""

    model_config = ConfigDict(frozen=True)

    objective: str
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    available_tools: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    agents: tuple[AgentSpec, ...] = ()
    teams: tuple[TeamSpec, ...] = ()
    suggested_tasks: tuple[PlanTaskSpec, ...] = ()
    memory_context: MemoryContextPolicy | None = None
    quality_contract: GoalArtifactQualityContract | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_native_mas_inventory(self) -> Self:
        validate_unique_ids(
            (agent.id for agent in self.agents),
            label="goal agents",
        )
        validate_unique_ids(
            (team.id for team in self.teams),
            label="goal teams",
        )
        return self


class ExecutionPlan(BaseModel):
    """Runtime plan produced from a goal."""

    model_config = ConfigDict(frozen=True)

    name: str
    goal: GoalSpec
    tasks: tuple[PlanTaskSpec, ...]
    version: str = "1"
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_blueprint(self) -> WorkflowBlueprint:
        return WorkflowBlueprint(
            name=self.name,
            version=self.version,
            nodes=tuple(task.to_workflow_node() for task in self.tasks),
        )

    def as_session_metadata(self) -> dict[str, Any]:
        return {
            "goal": self.goal.model_dump(mode="json"),
            "execution_plan": self.model_dump(mode="json"),
        }

    def apply_delta(self, delta: PlanDelta) -> ExecutionPlan:
        tasks_by_key = {task.key: task for task in self.tasks}
        for key in delta.remove_task_keys:
            if key not in tasks_by_key:
                raise ValueError(f"cannot remove unknown task {key!r}")
            del tasks_by_key[key]
        for task in delta.update_tasks:
            if task.key not in tasks_by_key:
                raise ValueError(f"cannot update unknown task {task.key!r}")
            tasks_by_key[task.key] = task
        for task in delta.add_tasks:
            if task.key in tasks_by_key:
                raise ValueError(f"cannot add duplicate task {task.key!r}")
            tasks_by_key[task.key] = task
        ordered_keys = [task.key for task in self.tasks if task.key in tasks_by_key]
        ordered_keys.extend(task.key for task in delta.add_tasks)
        updated_tasks = tuple(tasks_by_key[key] for key in ordered_keys)
        validate_requires(updated_tasks)
        metadata = dict(self.metadata)
        if delta.rationale:
            metadata["last_plan_delta_rationale"] = delta.rationale
        diagnostics = delta.metadata.get("planner_diagnostics")
        if isinstance(diagnostics, dict):
            metadata["last_plan_delta_diagnostics"] = dict(diagnostics)
        return with_goal_quality_contract(
            with_goal_expected_artifacts(
                self.model_copy(update={"tasks": updated_tasks, "metadata": metadata})
            )
        )


class PlanDelta(BaseModel):
    """A proposed runtime plan change.

    P0 stores the shape and validates it in tests. Later control slices can
    authorize and persist these deltas as graph patches.
    """

    model_config = ConfigDict(frozen=True)

    add_tasks: tuple[PlanTaskSpec, ...] = ()
    remove_task_keys: tuple[str, ...] = ()
    update_tasks: tuple[PlanTaskSpec, ...] = ()
    rationale: str = ""
    feedback_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlannerProvider(Protocol):
    """Produces or revises executable plans from goals and feedback."""

    async def initial_plan(self, goal: GoalSpec) -> ExecutionPlan: ...

    async def revise_plan(
        self,
        goal: GoalSpec,
        current_plan: ExecutionPlan,
        feedback: tuple[str, ...] = (),
    ) -> PlanDelta: ...


def with_goal_expected_artifacts(plan: ExecutionPlan) -> ExecutionPlan:
    tasks = tasks_with_goal_expected_artifacts(plan.tasks, plan.goal)
    if tasks == plan.tasks:
        return plan
    return plan.model_copy(update={"tasks": tasks})


def with_goal_quality_contract(plan: ExecutionPlan) -> ExecutionPlan:
    if plan.goal.quality_contract is None:
        return plan
    if not plan.tasks:
        raise ValueError("goal quality_contract requires at least one task")
    last_task = plan.tasks[-1]
    if last_task.contract.quality_contract is not None:
        return plan
    contract = last_task.contract.model_copy(
        update={"quality_contract": plan.goal.quality_contract}
    )
    return plan.model_copy(
        update={
            "tasks": (
                *plan.tasks[:-1],
                last_task.model_copy(update={"contract": contract}),
            )
        }
    )


def tasks_with_goal_expected_artifacts(
    tasks: tuple[PlanTaskSpec, ...],
    goal: GoalSpec,
) -> tuple[PlanTaskSpec, ...]:
    if not goal.expected_artifacts:
        return tasks
    if not tasks:
        raise ValueError("goal expected_artifacts require at least one task")
    declared = {
        artifact
        for task in tasks
        for artifact in task.contract.expected_artifacts
    }
    missing = tuple(
        artifact
        for artifact in goal.expected_artifacts
        if artifact not in declared
    )
    if not missing:
        return tasks
    last_task = tasks[-1]
    expected_artifacts = last_task.contract.expected_artifacts + tuple(
        artifact
        for artifact in missing
        if artifact not in last_task.contract.expected_artifacts
    )
    contract = last_task.contract.model_copy(
        update={"expected_artifacts": expected_artifacts}
    )
    return (*tasks[:-1], last_task.model_copy(update={"contract": contract}))


def validate_requires(tasks: tuple[PlanTaskSpec, ...]) -> None:
    known = {task.key for task in tasks}
    for task in tasks:
        missing = [key for key in task.requires if key not in known]
        if missing:
            raise ValueError(
                f"task {task.key!r} requires unknown task(s): {missing}"
            )


def require_declared_tools_allowed(
    *,
    declared: tuple[str, ...],
    allowed: tuple[str, ...],
    owner: str,
) -> None:
    if not declared:
        return
    if not allowed:
        raise ValueError(f"{owner} declares tools but task allows no tools")
    unknown = sorted(set(declared).difference(allowed))
    if unknown:
        raise ValueError(
            f"{owner} declares tool(s) outside task tools: {unknown}"
        )


def validate_unique_ids(values: Any, *, label: str) -> None:
    ids = list(values)
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"{label} must not contain duplicate id(s): {duplicates}")
