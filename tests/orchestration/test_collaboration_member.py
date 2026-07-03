"""Native team member runtime boundary tests."""

from __future__ import annotations

import pytest

from hydramind.harness import Message, MessageRole, ToolSpec
from hydramind.mas import AgentSpec, SharedWorkspace, TeamSpec
from hydramind.orchestration.collaboration import CollaborationExecutionRequest
from hydramind.orchestration.collaboration_member import (
    member_parent_metadata,
    tools_for_agent,
)


def test_tools_for_agent_filters_declared_member_subset_in_order() -> None:
    tools = [
        ToolSpec(name="search.web", description="search"),
        ToolSpec(name="artifact.write_text", description="write"),
        ToolSpec(name="artifact.read_text", description="read"),
    ]
    agent = AgentSpec(
        id="writer",
        role="writer",
        tools=("artifact.read_text", "search.web"),
    )

    filtered = tools_for_agent(tools, agent)

    assert filtered is not None
    assert [tool.name for tool in filtered] == ["artifact.read_text", "search.web"]


def test_tools_for_agent_fails_closed_when_member_tool_is_unavailable() -> None:
    tools = [ToolSpec(name="search.web", description="search")]
    agent = AgentSpec(
        id="writer",
        role="writer",
        tools=("artifact.write_text",),
    )

    with pytest.raises(
        RuntimeError,
        match=r"agent 'writer' declares unavailable tool\(s\): "
        r"\['artifact\.write_text'\]",
    ):
        tools_for_agent(tools, agent)


def test_tools_for_agent_requires_provider_when_member_declares_tools() -> None:
    agent = AgentSpec(id="writer", role="writer", tools=("search.web",))

    with pytest.raises(
        RuntimeError,
        match="agent 'writer' declares tools but node has no tool provider",
    ):
        tools_for_agent(None, agent)


def test_member_parent_metadata_preserves_team_context_shape() -> None:
    team = TeamSpec(
        id="team",
        members=(AgentSpec(id="writer", role="writer"),),
        workspace=SharedWorkspace(id="shared"),
    )
    request = CollaborationExecutionRequest(
        session_id="sess",
        node_key="write",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="work")],
        system="system",
        tools=None,
        agent_role="coordinator",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    assert member_parent_metadata(
        team=team,
        member=team.members[0],
        request=request,
    ) == {
        "session_id": "sess",
        "node_key": "write",
        "execution_mode": "team",
        "team_id": "team",
        "member_id": "writer",
        "workspace_id": "shared",
    }
