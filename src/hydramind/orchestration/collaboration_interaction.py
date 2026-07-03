"""Native team interaction payload and trace shaping helpers."""

from __future__ import annotations

import json
from typing import Any

from hydramind.harness.base import (
    InvocationResult,
    Message,
    MessageRole,
    StopReason,
    Usage,
)
from hydramind.kernel.contracts import Message as InteractionMessage
from hydramind.kernel.contracts import Turn as InteractionTurn
from hydramind.mas import (
    AgentSpec,
    AggregationStrategy,
    TeamSpec,
)
from hydramind.mas.protocol_outcomes import (
    CoordinatorOutcome,
    MemberVoteRecord,
    VoteOutcome,
    build_vote_outcome,
    member_vote_record,
)
from hydramind.observability.event_details import EVENT_DETAIL_SCHEMA_VERSION


def team_detail(team: TeamSpec) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "execution_mode": "team",
        "team_id": team.id,
        "protocol": team.protocol.model_dump(mode="json"),
        "member_ids": [member.id for member in team.members],
    }
    if team.workspace is not None:
        detail["workspace_id"] = team.workspace.id
    return detail


def team_turn_detail(
    *,
    team: TeamSpec,
    member: AgentSpec,
    turn: InteractionTurn,
    interaction_id: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "schema_version": EVENT_DETAIL_SCHEMA_VERSION,
        "interaction_id": interaction_id,
        "turn_index": turn.index,
        "team_id": team.id,
        "role": member.role,
        "topology": team.protocol.topology.value,
    }
    if workspace_id is not None:
        detail["workspace_id"] = workspace_id
    return detail


def team_origin(
    team: TeamSpec,
    member: AgentSpec,
    subagent_id: str,
) -> dict[str, Any]:
    origin: dict[str, Any] = {
        "execution_mode": "team",
        "team_id": team.id,
        "member_id": member.id,
        "subagent_id": subagent_id,
        "subagent_role": member.role,
        "protocol_mode": team.protocol.mode.value,
    }
    if team.workspace is not None:
        origin["workspace_id"] = team.workspace.id
    return origin


def team_member_content(result: dict[str, Any]) -> str:
    return result["content"] or result["summary"] or ""


def member_result_from_durable(
    *,
    agent_id: str,
    role: str,
    content: str,
) -> dict[str, Any]:
    """Reconstruct a member result dict from a durable message (S5b resume).

    A turn completed in a prior attempt is NOT re-run on resume; its
    authoritative content is reloaded from durable state so the aggregation /
    typed-outcome step still sees ALL members' results. ``content`` is the full
    authoritative durable message content.
    """

    return {
        "agent_id": agent_id,
        "role": role,
        "subagent_id": f"resumed-{agent_id}",
        "content": content,
        "summary": "",
        "model_id": "resumed",
        "stop_reason": StopReason.END_TURN.value,
        "tool_call_count": 0,
        "usage": Usage().model_dump(),
        "resumed": True,
    }


def harness_message_from_interaction(message: InteractionMessage) -> Message:
    """Convert a kernel message into a harness-attributed peer message."""

    return Message(
        role=MessageRole.ASSISTANT,
        content=message.content,
        name=message.sender,
    )


def team_invocation_result(
    *,
    team: TeamSpec,
    coordinator_role: str,
    results: list[dict[str, Any]],
) -> InvocationResult:
    collaboration = team_collaboration_payload(
        team=team,
        coordinator_role=coordinator_role,
        results=results,
    )
    payload = {"collaboration": collaboration}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return InvocationResult(
        content=content,
        stop_reason=StopReason.END_TURN,
        model_id="team",
        usage=Usage(),
        raw={"mas_team": collaboration},
    )


def team_collaboration_payload(
    *,
    team: TeamSpec,
    coordinator_role: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": "team",
        "team_id": team.id,
        "protocol": team.protocol.model_dump(mode="json"),
        "workspace": (
            team.workspace.model_dump(mode="json")
            if team.workspace is not None
            else None
        ),
        "coordinator_role": coordinator_role,
        "member_count": len(results),
        "results": results,
        "aggregation": aggregate_results(team, results),
    }


def aggregate_results(
    team: TeamSpec, results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Reduce member results per the protocol's aggregation strategy."""

    if team.protocol.aggregation is AggregationStrategy.VOTE:
        return vote_aggregation(team, results)
    if team.protocol.aggregation is AggregationStrategy.COORDINATOR_SUMMARY:
        coordinator_id = team.protocol.coordinator_id
        coordinator_result = next(
            (r for r in results if r["agent_id"] == coordinator_id), None
        )
        summary = ""
        if coordinator_result is not None:
            summary = team_member_content(coordinator_result)
        return CoordinatorOutcome(
            coordinator_id=coordinator_id,
            summary=summary,
        ).as_payload()
    return {"strategy": AggregationStrategy.COLLECT.value}


def team_typed_outcome(
    team: TeamSpec, results: list[dict[str, Any]]
) -> VoteOutcome | CoordinatorOutcome | None:
    """Return the TYPED protocol outcome for durable recording (S5a).

    Reuses the same S3b builders that produce the wire payload, so the durable
    record and the collaboration payload stay canonical-consistent. Returns
    ``None`` for the default COLLECT aggregation (no typed protocol outcome).
    """

    if team.protocol.aggregation is AggregationStrategy.VOTE:
        declared_options = team.protocol.vote_options
        votes = [
            member_vote_record(
                agent_id=result["agent_id"],
                raw=team_member_content(result),
                declared_options=declared_options,
            )
            for result in results
        ]
        return build_vote_outcome(
            votes,
            arbitration=team.protocol.arbitration,
            declared_options=declared_options,
        )
    if team.protocol.aggregation is AggregationStrategy.COORDINATOR_SUMMARY:
        coordinator_id = team.protocol.coordinator_id
        coordinator_result = next(
            (r for r in results if r["agent_id"] == coordinator_id), None
        )
        summary = ""
        if coordinator_result is not None:
            summary = team_member_content(coordinator_result)
        return CoordinatorOutcome(coordinator_id=coordinator_id, summary=summary)
    return None


def member_vote(result: dict[str, Any]) -> MemberVoteRecord:
    """Produce a typed, canonical per-voter record for one member result.

    The agent's vote is its produced content (falling back to its summary); the
    record carries the raw text alongside its canonical form and whether it is a
    valid declared option. This replaces the prior raw ``content.strip()`` key so
    equivalent wording maps to one outcome and free-form text cannot silently
    become its own majority key.
    """

    raw = team_member_content(result)
    return member_vote_record(
        agent_id=result["agent_id"],
        raw=raw,
        declared_options=(),
    )


def vote_aggregation(
    team: TeamSpec, results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build a typed, canonical :class:`VoteOutcome` wire payload.

    Votes are canonicalized before tally; when ``protocol.vote_options`` is
    declared, only canonical declared options are valid (invalid/free-form votes
    are surfaced, never tallied, never eligible to win). Ties are explicit.
    """

    declared_options = team.protocol.vote_options
    votes = [
        member_vote_record(
            agent_id=result["agent_id"],
            raw=team_member_content(result),
            declared_options=declared_options,
        )
        for result in results
    ]
    outcome = build_vote_outcome(
        votes,
        arbitration=team.protocol.arbitration,
        declared_options=declared_options,
    )
    return outcome.as_payload()
