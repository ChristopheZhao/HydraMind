"""Typed, canonical MAS protocol outcome contracts.

Vote / coordinator / debate semantics must be typed and canonical, and must not
depend on raw model surface text (see
``docs/architecture/95-execution-harness-correction.md`` — "MAS protocol
outcomes must be typed and canonical. Vote/coordinator/debate semantics must not
depend on raw model surface text").

This module provides:

- :func:`canonicalize_vote` — normalize raw model text to a stable vote key so
  equivalent wording ("yes" / "Yes." / " yes ") maps to ONE outcome.
- :class:`MemberVoteRecord` — a durable/replayable per-voter record carrying the
  raw text, its canonical form, and whether it was a valid (declared) option.
- :class:`VoteOutcome` — a typed, frozen, replayable tally with explicit invalid
  handling and explicit tie representation.
- :class:`CoordinatorOutcome` — a typed coordinator-summary artifact.
- :func:`build_vote_outcome` — assemble a :class:`VoteOutcome` from per-voter
  records under a declared option set and arbitration strategy.

Outcomes are frozen Pydantic models and round-trip through ``.as_payload()`` so
they can be embedded in the collaboration wire payload, persisted, and replayed.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from hydramind.mas.contracts import ArbitrationStrategy

PROTOCOL_OUTCOME_SCHEMA_VERSION = 1
"""Current schema version for typed protocol outcome payloads."""

_LEADING_PUNCTUATION = re.compile(r"^[\s\.\!\?\,\;\:\"'`]+")
_TRAILING_PUNCTUATION = re.compile(r"[\s\.\!\?\,\;\:\"'`]+$")
_INTERNAL_WHITESPACE = re.compile(r"\s+")


def canonicalize_vote(raw: str) -> str:
    """Normalize raw model vote text to a stable canonical key.

    Normalization (in order): strip, lowercase, collapse internal whitespace,
    and strip surrounding punctuation (e.g. a trailing ``.`` / ``!`` or a
    leading quote). Equivalent wording therefore collapses to one key, so
    ``"yes"`` / ``"Yes."`` / ``" yes "`` all canonicalize to ``"yes"``.
    """

    text = raw.strip().lower()
    text = _INTERNAL_WHITESPACE.sub(" ", text)
    text = _LEADING_PUNCTUATION.sub("", text)
    text = _TRAILING_PUNCTUATION.sub("", text)
    return text


class MemberVoteRecord(BaseModel):
    """Durable/replayable per-voter vote record.

    Carries the raw model text, its canonical form, and whether the canonical
    vote matched a declared option (``valid``). When no options are declared
    every non-empty canonical vote is valid (back-compat).
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    raw: str
    canonical: str
    valid: bool

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class VoteOutcome(BaseModel):
    """Typed, frozen, replayable vote tally outcome.

    Only VALID votes are tallied. Invalid/free-form votes are recorded (in both
    ``votes`` and ``invalid``) and surfaced, but never counted in ``tally`` and
    never eligible to win. Ties are represented explicitly via ``tie`` /
    ``tied_options``, while ``winner`` stays deterministic (alpha-first of the
    tied options) for replay stability.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = PROTOCOL_OUTCOME_SCHEMA_VERSION
    arbitration: str
    tally: dict[str, int]
    votes: tuple[MemberVoteRecord, ...]
    invalid: tuple[MemberVoteRecord, ...]
    winner: str | None
    winner_count: int
    tie: bool
    tied_options: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        """Return the collaboration wire payload dict for this outcome."""

        return {
            "schema_version": self.schema_version,
            "strategy": "vote",
            "arbitration": self.arbitration,
            "tally": dict(self.tally),
            "winner": self.winner,
            "winner_count": self.winner_count,
            "tie": self.tie,
            "tied_options": list(self.tied_options),
            "invalid": [record.as_payload() for record in self.invalid],
            "votes": [record.as_payload() for record in self.votes],
        }


class CoordinatorOutcome(BaseModel):
    """Typed coordinator-summary protocol artifact."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = PROTOCOL_OUTCOME_SCHEMA_VERSION
    coordinator_id: str | None
    summary: str

    def as_payload(self) -> dict[str, Any]:
        """Return the collaboration wire payload dict for this outcome."""

        return {
            "schema_version": self.schema_version,
            "strategy": "coordinator_summary",
            "coordinator_id": self.coordinator_id,
            "summary": self.summary,
        }


def build_vote_outcome(
    votes: list[MemberVoteRecord],
    *,
    arbitration: ArbitrationStrategy,
    declared_options: tuple[str, ...] = (),
) -> VoteOutcome:
    """Assemble a :class:`VoteOutcome` from per-voter records.

    ``declared_options`` are canonicalized; when non-empty, only votes whose
    canonical form is a declared option are valid (and tallied). When empty,
    every non-empty canonical vote is valid (back-compat — canonicalization
    alone still collapses equivalent wording).

    Under MAJORITY arbitration the winner is the canonical option with the top
    valid count; a shared top count sets ``tie=True`` with sorted
    ``tied_options`` and a deterministic alpha-first ``winner``. An empty tally
    (all votes invalid, or no votes) yields ``winner=None``/``winner_count=0``.
    """

    tally: dict[str, int] = {}
    invalid: list[MemberVoteRecord] = []
    for record in votes:
        if record.valid:
            tally[record.canonical] = tally.get(record.canonical, 0) + 1
        else:
            invalid.append(record)

    winner: str | None = None
    winner_count = 0
    tie = False
    tied_options: tuple[str, ...] = ()
    if arbitration is ArbitrationStrategy.MAJORITY and tally:
        top_count = max(tally.values())
        top_options = sorted(
            option for option, count in tally.items() if count == top_count
        )
        winner = top_options[0]
        winner_count = top_count
        if len(top_options) > 1:
            tie = True
            tied_options = tuple(top_options)

    return VoteOutcome(
        arbitration=arbitration.value,
        tally=tally,
        votes=tuple(votes),
        invalid=tuple(invalid),
        winner=winner,
        winner_count=winner_count,
        tie=tie,
        tied_options=tied_options,
    )


def member_vote_record(
    *,
    agent_id: str,
    raw: str,
    declared_options: tuple[str, ...] = (),
) -> MemberVoteRecord:
    """Build a :class:`MemberVoteRecord` from raw text + declared options.

    ``declared_options`` are canonicalized before comparison. With no declared
    options, any non-empty canonical vote is valid. With declared options, the
    canonical vote must be one of them, otherwise the record is ``valid=False``
    (invalid/free-form) and will not be tallied nor eligible to win.
    """

    canonical = canonicalize_vote(raw)
    if declared_options:
        allowed = {canonicalize_vote(option) for option in declared_options}
        valid = canonical in allowed
    else:
        valid = bool(canonical)
    return MemberVoteRecord(
        agent_id=agent_id,
        raw=raw,
        canonical=canonical,
        valid=valid,
    )


__all__ = [
    "PROTOCOL_OUTCOME_SCHEMA_VERSION",
    "CoordinatorOutcome",
    "MemberVoteRecord",
    "VoteOutcome",
    "build_vote_outcome",
    "canonicalize_vote",
    "member_vote_record",
]
