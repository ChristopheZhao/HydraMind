"""Native multi-agent collaboration contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_DEBATE_ROUNDS = 2
_DEBATE_ROUNDS_METADATA_KEY = "rounds"


class CollaborationMode(StrEnum):
    """Execution shape for a native MAS team."""

    TEAM = "team"
    DELEGATION = "delegation"
    DEBATE = "debate"
    VOTE = "vote"


class CollaborationTopology(StrEnum):
    """Message routing shape inside a team."""

    BROADCAST = "broadcast"
    COORDINATOR = "coordinator"
    PIPELINE = "pipeline"


class AggregationStrategy(StrEnum):
    """How member results are combined."""

    COLLECT = "collect"
    COORDINATOR_SUMMARY = "coordinator_summary"
    VOTE = "vote"


class ArbitrationStrategy(StrEnum):
    """Who resolves conflicting member outputs."""

    COORDINATOR = "coordinator"
    MAJORITY = "majority"
    NONE = "none"


class WorkspaceScope(StrEnum):
    """Visibility boundary for shared team context."""

    TASK = "task"
    SESSION = "session"
    TEAM = "team"


class AgentSpec(BaseModel):
    """One role-bearing native MAS participant."""

    model_config = ConfigDict(frozen=True)

    id: str
    role: str
    description: str = ""
    instructions: str = ""
    prompt_ref: str | None = None
    tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "role")
    @classmethod
    def _non_empty_identifier(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("agent identifiers and roles must not be empty")
        return text

    @field_validator("tools")
    @classmethod
    def _unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_non_empty_strings(value, field_name="tools")


class CollaborationProtocol(BaseModel):
    """Typed collaboration policy for a native MAS team."""

    model_config = ConfigDict(frozen=True)

    mode: CollaborationMode = CollaborationMode.TEAM
    topology: CollaborationTopology = CollaborationTopology.BROADCAST
    aggregation: AggregationStrategy = AggregationStrategy.COLLECT
    arbitration: ArbitrationStrategy = ArbitrationStrategy.COORDINATOR
    coordinator_id: str | None = None
    vote_options: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("vote_options")
    @classmethod
    def _unique_vote_options(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_non_empty_strings(value, field_name="vote_options")

    @field_validator("coordinator_id")
    @classmethod
    def _coordinator_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("coordinator_id must not be empty")
        return text

    @model_validator(mode="after")
    def _validate_protocol_consistency(self) -> Self:
        if self.mode is CollaborationMode.DEBATE:
            debate_rounds(self)

        if self.mode is CollaborationMode.VOTE:
            if self.topology is not CollaborationTopology.BROADCAST:
                raise ValueError("vote mode requires topology=broadcast")
            if self.aggregation is not AggregationStrategy.VOTE:
                raise ValueError("vote mode requires aggregation=vote")
            if self.arbitration is not ArbitrationStrategy.MAJORITY:
                raise ValueError("vote mode requires arbitration=majority")
        elif self.aggregation is AggregationStrategy.VOTE:
            raise ValueError("vote aggregation requires mode=vote")

        if self.aggregation is AggregationStrategy.COORDINATOR_SUMMARY:
            if self.topology is not CollaborationTopology.COORDINATOR:
                raise ValueError(
                    "coordinator_summary aggregation requires topology=coordinator"
                )
            if self.coordinator_id is None:
                raise ValueError(
                    "coordinator_summary aggregation requires protocol.coordinator_id"
                )

        if self.mode is CollaborationMode.DELEGATION:
            if self.topology is not CollaborationTopology.COORDINATOR:
                raise ValueError("delegation mode requires topology=coordinator")
            if self.coordinator_id is None:
                raise ValueError("delegation mode requires protocol.coordinator_id")

        if (
            self.topology is CollaborationTopology.COORDINATOR
            and self.coordinator_id is None
        ):
            raise ValueError("coordinator topology requires protocol.coordinator_id")

        return self


def debate_rounds(protocol: CollaborationProtocol) -> int:
    """Return the validated round count for a debate protocol."""

    value = protocol.metadata.get(
        _DEBATE_ROUNDS_METADATA_KEY,
        DEFAULT_DEBATE_ROUNDS,
    )
    if type(value) is not int:
        raise ValueError("debate metadata.rounds must be a positive integer")
    if value < 1:
        raise ValueError("debate metadata.rounds must be >= 1")
    return value


class SharedWorkspace(BaseModel):
    """Lightweight shared-context marker available to a team.

    Collaboration data flows through the message-passing kernel seam (peer
    transcripts), not through artifact/memory references on the workspace, so
    those fields were removed (S102 execute-or-remove). The workspace remains a
    scoped identity/metadata marker.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    scope: WorkspaceScope = WorkspaceScope.TASK
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("workspace id must not be empty")
        return text


class TeamSpec(BaseModel):
    """First-class native MAS team contract."""

    model_config = ConfigDict(frozen=True)

    id: str
    members: tuple[AgentSpec, ...]
    protocol: CollaborationProtocol = Field(default_factory=CollaborationProtocol)
    workspace: SharedWorkspace | None = None
    tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _team_id_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("team id must not be empty")
        return text

    @field_validator("tools")
    @classmethod
    def _unique_team_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_non_empty_strings(value, field_name="team tools")

    @model_validator(mode="after")
    def _validate_team(self) -> Self:
        if not self.members:
            raise ValueError("team must include at least one member")
        member_ids = [member.id for member in self.members]
        duplicates = sorted({item for item in member_ids if member_ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate team member id(s): {duplicates}")
        if (
            self.protocol.coordinator_id is not None
            and self.protocol.coordinator_id not in member_ids
        ):
            raise ValueError(
                f"coordinator_id {self.protocol.coordinator_id!r} is not a team member"
            )
        if (
            self.protocol.topology is CollaborationTopology.COORDINATOR
            and self.protocol.coordinator_id is None
        ):
            raise ValueError(
                "coordinator topology requires protocol.coordinator_id naming a member"
            )
        if (
            self.protocol.mode in (CollaborationMode.DEBATE, CollaborationMode.VOTE)
            and len(self.members) < 2
        ):
            raise ValueError(
                f"{self.protocol.mode.value} mode requires at least 2 members"
            )
        if self.protocol.mode is CollaborationMode.DELEGATION:
            if self.protocol.coordinator_id is None:
                raise ValueError(
                    "delegation mode requires protocol.coordinator_id naming the "
                    "delegator"
                )
            if len(self.members) < 2:
                raise ValueError(
                    "delegation mode requires at least 2 members (delegator + "
                    ">=1 delegate)"
                )
        if self.tools:
            allowed = set(self.tools)
            for member in self.members:
                unknown = sorted(set(member.tools).difference(allowed))
                if unknown:
                    raise ValueError(
                        f"member {member.id!r} declares tool(s) outside team tools: {unknown}"
                    )
        return self

    def declared_tools(self) -> tuple[str, ...]:
        """Return team and member tools without duplicates, preserving order."""

        tools: list[str] = []
        for tool in self.tools:
            if tool not in tools:
                tools.append(tool)
        for member in self.members:
            for tool in member.tools:
                if tool not in tools:
                    tools.append(tool)
        return tuple(tools)


def _unique_non_empty_strings(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in values:
        text = item.strip()
        if not text:
            raise ValueError(f"{field_name} must not contain empty values")
        if text in normalized:
            raise ValueError(f"{field_name} must not contain duplicates: {text!r}")
        normalized.append(text)
    return tuple(normalized)
