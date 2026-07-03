"""S90 dead-surface freeze: projection + executor fail-closed (ADR-0007)."""

from __future__ import annotations

import pytest

from hydramind.harness import ModelHint
from hydramind.mas import (
    AgentSpec,
    CollaborationProtocol,
    SharedWorkspace,
    TeamSpec,
    UnexecutedProtocolError,
)
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.execution_harness import ProviderExecutionHarnessRuntime
from hydramind.orchestration.planning_contracts import PlanTaskSpec
from hydramind.orchestration.subagent_spawn import SubagentSpawner
from hydramind.testing import MockProvider
from hydramind.tools import ToolContext


def _members() -> tuple[AgentSpec, ...]:
    return (
        AgentSpec(id="writer", role="writer"),
        AgentSpec(id="reviewer", role="reviewer"),
    )


def _executed_team() -> TeamSpec:
    return TeamSpec(
        id="team",
        members=_members(),
        protocol=CollaborationProtocol(coordinator_id="reviewer"),
        workspace=SharedWorkspace(id="shared"),
    )


def _team() -> TeamSpec:
    return TeamSpec(
        id="team",
        members=_members(),
        protocol=CollaborationProtocol(coordinator_id="reviewer"),
        workspace=SharedWorkspace(id="shared"),
    )


def test_projection_accepts_executed_team() -> None:
    node = PlanTaskSpec(key="collaborate", role="coordinator", team=_executed_team()).to_workflow_node()
    assert node.config["execution_mode"] == "team"


def test_projection_rejects_unexecuted_team(monkeypatch: pytest.MonkeyPatch) -> None:
    # After S102 every declared collaboration capability is executed-or-removed, so
    # there is no naturally-unexecuted value. The freeze GUARD must still fail closed
    # for any FUTURE value added without a corresponding EXECUTED_* widening — proven
    # here by shrinking the executed-mode envelope so a normal TEAM becomes unexecuted.
    # The guard runs inside PlanTaskSpec's model validator (pydantic surfaces a ValueError).
    monkeypatch.setattr("hydramind.mas.capability.EXECUTED_MODES", frozenset())
    with pytest.raises(ValueError, match="not yet executed by the runtime") as exc:
        PlanTaskSpec(key="collaborate", role="coordinator", team=_team())
    assert "mode" in str(exc.value)


@pytest.mark.asyncio
async def test_executor_backstop_rejects_unexecuted_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bypass PlanTaskSpec entirely (hand-authored mas_team config) to prove the
    # executor backstop fails closed before any member is spawned. Shrink the executed
    # envelope so the guard fires (see the projection test for rationale).
    monkeypatch.setattr("hydramind.mas.capability.EXECUTED_MODES", frozenset())

    async def emit_trace(*args: object, **kwargs: object) -> None:
        return None

    async def execute_tool_round(*args: object, **kwargs: object) -> list:
        raise AssertionError("must fail closed before tool execution")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(MockProvider())),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=1,
        emit_trace=emit_trace,
        execute_tool_round=execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[],
        system="system",
        tools=[],
        agent_role="coordinator",
        node_config={"mas_team": _team().model_dump(mode="json")},
    )
    with pytest.raises(UnexecutedProtocolError):
        await executor.invoke_team(request)
