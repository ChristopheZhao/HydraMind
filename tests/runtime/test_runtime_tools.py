"""Runtime tool dependency assembly tests."""

from __future__ import annotations

from hydramind.control.models import WorkflowBlueprint, WorkflowNodeSpec
from hydramind.runtime_tools import (
    build_goal_tool_runtime,
    build_workflow_tool_runtime,
)


def test_goal_tool_runtime_preserves_execution_policy(tmp_path) -> None:
    runtime = build_goal_tool_runtime(
        artifact_root=tmp_path / "goal-artifacts",
        live_tools=False,
        approved_tools=("artifact.write_text",),
        allowed_process_commands=("python",),
        allowed_process_argv_prefixes=(("python", "-m"),),
    )

    environment = runtime.environment
    assert environment.artifact_root == tmp_path / "goal-artifacts"
    assert environment.dry_run is True
    assert environment.network_access is False
    assert environment.approved_tools == ("artifact.write_text",)
    assert environment.allowed_process_commands == ("python",)
    assert environment.allowed_process_argv_prefixes == (("python", "-m"),)
    assert "artifact.write_text" in runtime.tools.names()


def test_workflow_tool_runtime_uses_workflow_artifact_default_and_scoped_provider(
    tmp_path,
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    blueprint = WorkflowBlueprint(
        name="scoped",
        nodes=(
            WorkflowNodeSpec(
                key="write",
                role="writer",
                config={"tools": ["artifact.write_text"]},
            ),
            WorkflowNodeSpec(key="review", role="reviewer"),
        ),
    )

    runtime = build_workflow_tool_runtime(
        blueprint=blueprint,
        workflow_path=workflow_path,
        artifact_root=None,
        live_tools=False,
    )

    assert runtime.environment.artifact_root == tmp_path / "artifacts"
    assert runtime.environment.dry_run is True
    assert [tool.name for tool in runtime.tool_provider.tools_for("write")] == [
        "artifact.write_text"
    ]
    assert runtime.tool_provider.tools_for("review") == []
