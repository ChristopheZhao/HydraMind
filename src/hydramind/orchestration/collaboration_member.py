"""Native team member runtime helpers for collaboration execution."""

from __future__ import annotations

from typing import Any

from hydramind.harness.base import (
    InvocationResult,
    Message,
    ToolCall,
    ToolSpec,
)
from hydramind.mas import AgentSpec, TeamSpec
from hydramind.orchestration.collaboration_contracts import (
    CollaborationExecutionRequest,
)
from hydramind.orchestration.collaboration_interaction import team_origin
from hydramind.orchestration.collaboration_member_strategy import MemberTurnStrategy
from hydramind.orchestration.execution_harness import (
    SubagentContext,
    SubagentSpawnRequest,
)
from hydramind.orchestration.subagent_spawn import SubagentSpawner


class NativeTeamMemberRunner:
    """Runs one native team member turn through the orchestration spawn seam.

    Spawning the subagent + first send is fixed runtime; the per-member tool
    loop/termination is delegated to a swappable ``MemberTurnStrategy`` supplied
    per invocation so a swapped harness (e.g. explicit-submit) drives each member. The
    spawn ACT is orchestration-owned (``SubagentSpawner``, ADR-0012).
    """

    def __init__(
        self,
        *,
        subagent_spawner: SubagentSpawner,
    ) -> None:
        self._subagent_spawner = subagent_spawner

    async def run(
        self,
        *,
        member: AgentSpec,
        team: TeamSpec,
        request: CollaborationExecutionRequest,
        available_tools: list[ToolSpec] | None,
        seed_messages: tuple[Message, ...] = (),
        member_strategy: MemberTurnStrategy,
    ) -> dict[str, Any]:
        tools = tools_for_agent(available_tools, member)
        instructions = member.instructions or request.system
        subagent = await self._subagent_spawner.spawn(
            SubagentSpawnRequest(
                role=member.role,
                instructions=instructions,
                tools=tuple(tools or ()),
                parent_context=SubagentContext(
                    seed_messages=seed_messages,
                    metadata=member_parent_metadata(
                        team=team,
                        member=member,
                        request=request,
                    ),
                ),
            )
        )
        origin = team_origin(team, member, subagent.id)
        result = await subagent.send(request.messages[-1])
        if result.tool_calls and not tools:
            raise RuntimeError(
                "team member tool calls require a configured tool runner: "
                f"{team.id}/{member.id}"
            )
        result, drained_tool_calls = await member_strategy.drive_member(
            subagent=subagent,
            request=request,
            first_result=result,
            tools=list(tools or ()),
            agent_role=member.role,
            tool_origin=origin,
        )
        if result.tool_calls:
            raise RuntimeError(
                "unresolved team member tool calls: "
                f"{team.id}/{member.id}: {tool_call_names(result.tool_calls)}"
            )
        summary = await subagent.close()
        return member_result_payload(
            member=member,
            subagent_id=subagent.id,
            result=result,
            summary=summary,
            drained_tool_calls=drained_tool_calls,
            member_strategy=member_strategy.name,
        )


def member_parent_metadata(
    *,
    team: TeamSpec,
    member: AgentSpec,
    request: CollaborationExecutionRequest,
) -> dict[str, Any]:
    return {
        "session_id": request.session_id,
        "node_key": request.node_key,
        "execution_mode": "team",
        "team_id": team.id,
        "member_id": member.id,
        "workspace_id": team.workspace.id if team.workspace else None,
    }


def member_result_payload(
    *,
    member: AgentSpec,
    subagent_id: str,
    result: InvocationResult,
    summary: str,
    drained_tool_calls: int,
    member_strategy: str,
) -> dict[str, Any]:
    return {
        "agent_id": member.id,
        "role": member.role,
        "subagent_id": subagent_id,
        "content": result.content,
        "summary": summary,
        "model_id": result.model_id,
        "stop_reason": result.stop_reason.value,
        "tool_call_count": drained_tool_calls,
        "member_strategy": member_strategy,
        "usage": result.usage.model_dump(),
    }


def tools_for_agent(
    tools: list[ToolSpec] | None,
    agent: AgentSpec,
) -> list[ToolSpec] | None:
    if tools is None:
        if agent.tools:
            raise RuntimeError(
                f"agent {agent.id!r} declares tools but node has no tool provider"
            )
        return None
    if not agent.tools:
        return tools
    tools_by_name = {tool.name: tool for tool in tools}
    missing = sorted(set(agent.tools).difference(tools_by_name))
    if missing:
        raise RuntimeError(
            f"agent {agent.id!r} declares unavailable tool(s): {missing}"
        )
    return [tools_by_name[name] for name in agent.tools]


def tool_call_names(tool_calls: tuple[ToolCall, ...]) -> str:
    return ", ".join(call.name for call in tool_calls) or "<unknown>"


__all__ = ["NativeTeamMemberRunner"]
