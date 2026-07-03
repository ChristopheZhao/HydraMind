"""First-class kernel entities for native-agent collaboration (ADR-0007).

These contracts make the *agent interaction* the durable, scheduled unit: an
``Interaction`` owns an ordered log of ``Message`` exchanges and ``Turn`` acts.
They are pure, frozen value types with no harness, control, or runtime coupling;
later sprints attach control-owned persistence (S92), a harness interaction
primitive (S93), and scheduling wiring (S94) onto this foundation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hydramind.mas import CollaborationProtocol


class MessageRole(StrEnum):
    """Origin role of a kernel message."""

    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"


class TurnStatus(StrEnum):
    """Lifecycle of a single scheduled act."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class InteractionStatus(StrEnum):
    """Lifecycle of an agent interaction."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _non_empty(value: str, *, field: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


class Message(BaseModel):
    """One unit of communication exchanged within an interaction."""

    model_config = ConfigDict(frozen=True)

    id: str
    interaction_id: str
    turn_index: int = 0
    sender: str
    recipients: tuple[str, ...] = ()
    role: MessageRole = MessageRole.AGENT
    content: str = ""
    in_reply_to: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "interaction_id", "sender")
    @classmethod
    def _ids_non_empty(cls, value: str) -> str:
        return _non_empty(value, field="message field")

    @field_validator("turn_index")
    @classmethod
    def _turn_index_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("turn_index must be non-negative")
        return value


class Turn(BaseModel):
    """One scheduled acting step by a single agent."""

    model_config = ConfigDict(frozen=True)

    index: int
    agent_id: str
    status: TurnStatus = TurnStatus.PENDING
    message_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("index")
    @classmethod
    def _index_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("turn index must be non-negative")
        return value

    @field_validator("agent_id")
    @classmethod
    def _agent_id_non_empty(cls, value: str) -> str:
        return _non_empty(value, field="turn agent_id")


class Interaction(BaseModel):
    """The first-class, durable, scheduled collaboration unit."""

    model_config = ConfigDict(frozen=True)

    id: str
    team_id: str
    member_ids: tuple[str, ...]
    protocol: CollaborationProtocol = Field(default_factory=CollaborationProtocol)
    messages: tuple[Message, ...] = ()
    turns: tuple[Turn, ...] = ()
    status: InteractionStatus = InteractionStatus.PENDING
    workspace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "team_id")
    @classmethod
    def _ids_non_empty(cls, value: str) -> str:
        return _non_empty(value, field="interaction field")

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        if not self.member_ids:
            raise ValueError("interaction must include at least one member")
        members = list(self.member_ids)
        duplicates = sorted({m for m in members if members.count(m) > 1})
        if duplicates:
            raise ValueError(f"duplicate member id(s): {duplicates}")
        member_set = set(members)
        for turn in self.turns:
            if turn.agent_id not in member_set:
                raise ValueError(
                    f"turn agent_id {turn.agent_id!r} is not a declared member"
                )
        if self.protocol.coordinator_id is not None and (
            self.protocol.coordinator_id not in member_set
        ):
            raise ValueError(
                f"coordinator_id {self.protocol.coordinator_id!r} is not a member"
            )
        return self

    def acted_agent_ids(self) -> tuple[str, ...]:
        """Return the agent ids that have already taken a turn, in turn order."""

        return tuple(turn.agent_id for turn in self.turns)
