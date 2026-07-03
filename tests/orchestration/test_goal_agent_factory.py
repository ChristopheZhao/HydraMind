"""Goal agent factory and tool-scope boundary tests."""

from __future__ import annotations

from hydramind.control import ControlPlane, InMemorySessionStore, SessionService
from hydramind.harness import ToolSpec
from hydramind.orchestration.goal_agent_factory import (
    GoalAgentFactory,
    PlanScopedToolProvider,
)
from hydramind.orchestration.planning import ExecutionPlan, GoalSpec, PlanTaskSpec
from hydramind.testing import MockProvider


class BasicToolProvider:
    def __init__(self) -> None:
        self.requested_nodes: list[str] = []

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        self.requested_nodes.append(node_key)
        return [
            _tool("artifact.write_text"),
            _tool("artifact.write_json"),
            _tool("image.generate"),
        ]


class RegistryToolProvider:
    def __init__(self) -> None:
        self.requested_names: list[tuple[str, ...]] = []

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        raise AssertionError(f"tools_for should not be called for {node_key}")

    def specs_for(self, names: list[str]) -> list[ToolSpec]:
        self.requested_names.append(tuple(names))
        return [_tool(name) for name in names]


def test_plan_scoped_tool_provider_filters_task_tools_by_goal_inventory() -> None:
    base = BasicToolProvider()
    provider = PlanScopedToolProvider(
        _plan(),
        base,
    )

    tools = provider.tools_for("write")

    assert [tool.name for tool in tools] == ["artifact.write_text"]
    assert provider.tools_for("unknown") == []
    assert provider.tools_for("generate") == []
    assert base.requested_nodes == ["write"]


def test_plan_scoped_tool_provider_uses_sorted_specs_for_when_available() -> None:
    base = RegistryToolProvider()
    provider = PlanScopedToolProvider(
        _plan(
            tasks=(
                PlanTaskSpec(
                    key="write",
                    role="writer",
                    tools=("artifact.write_text", "search.web"),
                ),
            ),
            available_tools=("search.web", "artifact.write_text"),
        ),
        base,
    )

    tools = provider.tools_for("write")

    assert [tool.name for tool in tools] == ["artifact.write_text", "search.web"]
    assert base.requested_names == [("artifact.write_text", "search.web")]


def test_goal_agent_factory_builds_agent_from_plan() -> None:
    service = SessionService(InMemorySessionStore())
    factory = GoalAgentFactory(
        provider=MockProvider(),
        control=ControlPlane(service),
    )

    agent = factory.build(_plan())

    assert hasattr(agent, "start_session")
    assert hasattr(agent, "run_session")
    assert hasattr(agent, "resume_session")


def _plan(
    *,
    tasks: tuple[PlanTaskSpec, ...] = (
        PlanTaskSpec(
            key="write",
            role="writer",
            tools=("artifact.write_text", "image.generate"),
        ),
        PlanTaskSpec(
            key="generate",
            role="artist",
            tools=("image.generate",),
        ),
    ),
    available_tools: tuple[str, ...] = ("artifact.write_text",),
) -> ExecutionPlan:
    return ExecutionPlan(
        name="goal-agent-factory",
        goal=GoalSpec(
            objective="Write a report",
            available_tools=available_tools,
        ),
        tasks=tasks,
    )


def _tool(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool")
