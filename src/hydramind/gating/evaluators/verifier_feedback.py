"""Gate evaluator that consumes typed verifier feedback."""

from __future__ import annotations

from typing import Any

from hydramind.control import (
    AgentReport,
    DecisionAction,
    FeedbackRecord,
    Gate,
    GateOutcome,
    NodeState,
    NodeStatus,
    RuntimeSession,
    ToolExecutionStatus,
    VerifierResult,
)
from hydramind.gating.base import GateContract, GateSeverity


class VerifierFeedbackEvaluator:
    """Convert failed verifier results into a gate halt."""

    name = "verifier_feedback"

    def __init__(
        self,
        *,
        contract: GateContract | None = None,
    ) -> None:
        self.contract = contract or GateContract(
            name=self.name,
            description="Halt when an agent report carries failed verifier results.",
            severity=GateSeverity.ADVISORY,
        )

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        verifier_results = list(report.verifier_results)
        feedback = list(report.feedback)
        progress = _required_tool_progress(session, node)
        if progress is not None:
            verifier_results.append(_required_tool_result(progress))
            feedback.append(_required_tool_feedback(node.key, progress))
        if not verifier_results:
            return None
        failed = [item for item in verifier_results if not item.passed]
        detail: dict[str, Any] = {
            "verifier_results": [
                item.model_dump(mode="json") for item in verifier_results
            ],
            "failed_verifiers": [
                item.model_dump(mode="json") for item in failed
            ],
            "feedback": [
                item.model_dump(mode="json") for item in feedback
            ],
        }
        if progress is not None:
            detail["required_tool_progress"] = progress
        return Gate(
            name=self.contract.name,
            node_key=node.key,
            outcome=(
                GateOutcome.REQUIRES_DECISION
                if failed
                else GateOutcome.PASS
            ),
            detail=detail,
        )


def _required_tool_progress(
    session: RuntimeSession,
    current_node: NodeState,
) -> dict[str, Any] | None:
    required_tools = _goal_required_tools(session)
    if not required_tools:
        return None
    completed_tools = _completed_required_tools(session, required_tools)
    missing_tools = tuple(tool for tool in required_tools if tool not in completed_tools)
    pending_tool_nodes = _pending_tool_nodes(session, current_node.key, missing_tools)
    progress = {
        "required_tools": list(required_tools),
        "completed_tools": list(completed_tools),
        "missing_tools": list(missing_tools),
        "pending_tool_nodes": pending_tool_nodes,
        "current_node_key": current_node.key,
    }
    if not missing_tools or pending_tool_nodes:
        return None
    return progress


def _goal_required_tools(session: RuntimeSession) -> tuple[str, ...]:
    goal = session.metadata.get("goal")
    if isinstance(goal, dict):
        required = goal.get("required_tools")
        if isinstance(required, list | tuple):
            return _string_tuple(required)
    required = session.metadata.get("required_tools")
    if isinstance(required, list | tuple):
        return _string_tuple(required)
    return ()


def _completed_required_tools(
    session: RuntimeSession,
    required_tools: tuple[str, ...],
) -> tuple[str, ...]:
    required = set(required_tools)
    completed: list[str] = []
    for node in session.nodes.values():
        for attempt in node.attempts:
            for tool in attempt.tool_executions:
                if (
                    tool.tool_name in required
                    and tool.status is ToolExecutionStatus.SUCCEEDED
                    and tool.is_error is not True
                    and tool.tool_name not in completed
                ):
                    completed.append(tool.tool_name)
    return tuple(tool for tool in required_tools if tool in completed)


def _pending_tool_nodes(
    session: RuntimeSession,
    current_node_key: str,
    missing_tools: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not missing_tools:
        return []
    missing = set(missing_tools)
    plan = session.metadata.get("execution_plan")
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        return []
    pending: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        key = str(task.get("key") or "")
        if not key or key == current_node_key:
            continue
        node = session.nodes.get(key)
        if node is None or node.status not in {NodeStatus.QUEUED, NodeStatus.RUNNING}:
            continue
        tools = set(_string_tuple(task.get("tools")))
        covered = sorted(tools & missing)
        if covered:
            pending.append({"node_key": key, "tools": covered})
    return pending


def _required_tool_result(progress: dict[str, Any]) -> VerifierResult:
    missing = _string_tuple(progress.get("missing_tools"))
    completed = _string_tuple(progress.get("completed_tools"))
    return VerifierResult(
        name="required_tools.completed",
        passed=False,
        evidence_refs=completed,
        detail=progress,
        repair_instruction=(
            "Call missing required tool(s): "
            f"{', '.join(missing)}. Completed required tool(s): "
            f"{', '.join(completed) if completed else 'none'}."
        ),
    )


def _required_tool_feedback(
    node_key: str,
    progress: dict[str, Any],
) -> FeedbackRecord:
    missing = _string_tuple(progress.get("missing_tools"))
    completed = _string_tuple(progress.get("completed_tools"))
    completed_text = ", ".join(completed) if completed else "none"
    return FeedbackRecord(
        source="verifier.required_tools",
        message=(
            f"missing required tool(s): {', '.join(missing)}; "
            f"completed required tool(s): {completed_text}"
        ),
        target_node_key=node_key,
        severity="error",
        suggested_action=DecisionAction.REPLAN,
        evidence_refs=missing,
        detail=progress,
    )


def _string_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw)
    return ()
