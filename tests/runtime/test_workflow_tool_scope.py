from __future__ import annotations

import pytest

from hydramind.control.states import SessionStatus
from hydramind.harness import StopReason, ToolCall
from hydramind.runtime import (
    build_runtime_bundle,
    load_workflow_blueprint,
    run_workflow_file,
)
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import ToolError


def test_load_workflow_blueprint_normalizes_node_tools(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: scoped_tools
nodes:
  - key: write
    role: writer
    tools: [artifact.write_text, artifact.write_text, " time.now "]
  - key: review
    role: reviewer
    config:
      tools: [search.web]
    requires: [write]
  - key: summarize
    role: writer
    requires: [review]
""",
        encoding="utf-8",
    )

    blueprint = load_workflow_blueprint(workflow)

    assert blueprint.nodes[0].config["tools"] == [
        "artifact.write_text",
        "time.now",
    ]
    assert blueprint.nodes[1].config["tools"] == ["search.web"]
    assert "tools" not in blueprint.nodes[2].config


def test_load_workflow_blueprint_rejects_scalar_node_tools(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: invalid_tools
nodes:
  - key: write
    role: writer
    tools: artifact.write_text
""",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="must be a list of tool names"):
        load_workflow_blueprint(workflow)


@pytest.mark.asyncio
async def test_workflow_runtime_exposes_no_tools_without_node_declaration(
    tmp_path,
) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: no_tools
nodes:
  - key: work
    role: writer
""",
        encoding="utf-8",
    )
    backend = MockProvider(scripted=[ScriptedTurn(content='{"ok": true}')])

    session = await run_workflow_file(workflow, provider=backend, env_file=None)

    assert session.status is SessionStatus.COMPLETED
    assert backend.invocations[0]["tools"] == []


@pytest.mark.asyncio
async def test_workflow_runtime_exposes_only_declared_node_tools(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: scoped_tools
nodes:
  - key: write
    role: writer
    tools: [artifact.write_text]
  - key: review
    role: reviewer
    requires: [write]
""",
        encoding="utf-8",
    )
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={"path": "write/out.txt", "content": "ok"},
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"used_tool": true}'),
            ScriptedTurn(content='{"reviewed": true}'),
        ]
    )

    session = await run_workflow_file(workflow, provider=backend, env_file=None)

    assert session.status is SessionStatus.COMPLETED
    assert [tool.name for tool in backend.invocations[0]["tools"]] == [
        "artifact.write_text"
    ]
    assert [tool.name for tool in backend.invocations[1]["tools"]] == [
        "artifact.write_text"
    ]
    assert backend.invocations[2]["tools"] == []
    assert (tmp_path / "artifacts" / "write" / "out.txt").exists()


def test_workflow_runtime_rejects_unknown_declared_tool(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: missing_tools
nodes:
  - key: work
    role: writer
    tools: [missing.tool]
""",
        encoding="utf-8",
    )

    with pytest.raises((ValueError, ToolError), match=r"missing\.tool"):
        build_runtime_bundle(workflow, provider=MockProvider(), env_file=None)


@pytest.mark.asyncio
async def test_workflow_undeclared_tool_call_does_not_execute(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: blocked_tool
nodes:
  - key: work
    role: writer
""",
        encoding="utf-8",
    )
    backend = MockProvider(
        scripted=[
            ScriptedTurn(
                content="",
                tool_calls=(
                    ToolCall(
                        id="tool-1",
                        name="artifact.write_text",
                        arguments={
                            "path": "blocked/out.txt",
                            "content": "should not write",
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content='{"should_not_run": true}'),
        ]
    )

    session = await run_workflow_file(workflow, provider=backend, env_file=None)

    assert session.status is SessionStatus.FAILED
    assert "unresolved tool calls: artifact.write_text" in (
        session.error_message or ""
    )
    assert backend.invocations[0]["tools"] == []
    assert len(backend.invocations) == 1
    attempt = session.nodes["work"].latest_attempt()
    assert attempt is not None
    assert attempt.tool_executions == []
    assert not (tmp_path / "artifacts" / "blocked" / "out.txt").exists()
