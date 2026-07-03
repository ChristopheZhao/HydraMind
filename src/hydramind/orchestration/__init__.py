"""Orchestration — drives a workflow from start to terminal state.

See ``docs/architecture/40-orchestration.md``. Public surface:

    OrchestratorAgent — user-facing entry point
    WorkflowGraph     — topological-order helper over WorkflowBlueprint
    PromptLibrary     — role → template lookup with YAML loading
    PromptTemplate    — single role's system prompt
    GoalSpec          — goal-driven MAS runtime input
    ExecutionPlan     — planner output projected to runtime blueprint
    MemoryContextPolicy — opt-in planner/executor memory projection policy
    ToolProvider      — Protocol: node_key → list[ToolSpec]
    ReportBuilder     — Protocol: harness InvocationResult → AgentReport.output
"""

from hydramind.control import (
    GoalArtifactQualityContract,
    SemanticRubric,
    SemanticRubricCheck,
)
from hydramind.mas import (
    AgentSpec,
    CollaborationProtocol,
    SharedWorkspace,
    TeamSpec,
)
from hydramind.orchestration.agent import (
    OrchestratorAgent,
    ReportBuilder,
    ToolProvider,
    default_report_builder,
)
from hydramind.orchestration.goal_agent import (
    GoalDrivenOrchestratorAgent,
)
from hydramind.orchestration.graph import GraphCycleError, WorkflowGraph
from hydramind.orchestration.memory_context import (
    MemoryContext,
    MemoryContextEntry,
    MemoryContextPolicy,
    MemoryContextQuery,
    MemoryContextRequest,
    MemoryContextRetriever,
    StoreMemoryContextRetriever,
)
from hydramind.orchestration.planning import (
    ExecutionPlan,
    GoalSpec,
    ModelGoalPlanner,
    NodeExecutionMode,
    PlanDelta,
    PlannerProvider,
    PlanTaskSpec,
)
from hydramind.orchestration.prompts import PromptLibrary, PromptTemplate
from hydramind.orchestration.quality_contract import load_goal_quality_contract
from hydramind.orchestration.verification import (
    ArtifactContainmentVerifierRunner,
    CompositeVerifierRunner,
    SemanticArtifactVerifierRunner,
    TaskContractVerifierRunner,
    VerifierRunner,
)

__all__ = [
    "AgentSpec",
    "ArtifactContainmentVerifierRunner",
    "CollaborationProtocol",
    "CompositeVerifierRunner",
    "ExecutionPlan",
    "GoalArtifactQualityContract",
    "GoalDrivenOrchestratorAgent",
    "GoalSpec",
    "GraphCycleError",
    "MemoryContext",
    "MemoryContextEntry",
    "MemoryContextPolicy",
    "MemoryContextQuery",
    "MemoryContextRequest",
    "MemoryContextRetriever",
    "ModelGoalPlanner",
    "NodeExecutionMode",
    "OrchestratorAgent",
    "PlanDelta",
    "PlanTaskSpec",
    "PlannerProvider",
    "PromptLibrary",
    "PromptTemplate",
    "ReportBuilder",
    "SemanticArtifactVerifierRunner",
    "SemanticRubric",
    "SemanticRubricCheck",
    "SharedWorkspace",
    "StoreMemoryContextRetriever",
    "TaskContractVerifierRunner",
    "TeamSpec",
    "ToolProvider",
    "VerifierRunner",
    "WorkflowGraph",
    "default_report_builder",
    "load_goal_quality_contract",
]
