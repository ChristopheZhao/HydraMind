"""Contract tests for typed, canonical MAS protocol outcomes (S3b).

Proves the HIGH gap fix: vote/coordinator semantics are typed and canonical and
do not depend on raw model surface text.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hydramind.mas import (
    ArbitrationStrategy,
    CollaborationProtocol,
    CoordinatorOutcome,
    MemberVoteRecord,
    VoteOutcome,
    build_vote_outcome,
    canonicalize_vote,
    member_vote_record,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("yes", "yes"),
        ("Yes.", "yes"),
        (" yes ", "yes"),
        ("YES!", "yes"),
        ("  Option   A  ", "option a"),
        ('"approve"', "approve"),
        ("", ""),
    ],
)
def test_canonicalize_vote_collapses_equivalent_wording(
    raw: str, expected: str
) -> None:
    assert canonicalize_vote(raw) == expected


def test_equivalent_wording_yields_one_tally_key_and_one_majority() -> None:
    # "yes" / "Yes." / " yes " are THREE raw strings but ONE canonical vote.
    votes = [
        member_vote_record(agent_id="a", raw="yes"),
        member_vote_record(agent_id="b", raw="Yes."),
        member_vote_record(agent_id="c", raw=" yes "),
        member_vote_record(agent_id="d", raw="no"),
    ]
    outcome = build_vote_outcome(votes, arbitration=ArbitrationStrategy.MAJORITY)

    # ONE tally key for all equivalent wording (not three).
    assert outcome.tally == {"yes": 3, "no": 1}
    assert outcome.winner == "yes"
    assert outcome.winner_count == 3
    assert outcome.tie is False
    assert outcome.invalid == ()


def test_declared_options_make_freeform_invalid_and_unable_to_win() -> None:
    declared = ("yes", "no")
    votes = [
        member_vote_record(agent_id="a", raw="yes", declared_options=declared),
        # Free-form essay: would have been a 1-count majority key under the old
        # raw-text tally; with declared options it is INVALID and never tallied.
        member_vote_record(
            agent_id="b",
            raw="I think we should reconsider entirely",
            declared_options=declared,
        ),
        member_vote_record(
            agent_id="c",
            raw="maybe later",
            declared_options=declared,
        ),
    ]
    outcome = build_vote_outcome(
        votes,
        arbitration=ArbitrationStrategy.MAJORITY,
        declared_options=declared,
    )

    # Free-form text does NOT create a new majority key.
    assert outcome.tally == {"yes": 1}
    assert outcome.winner == "yes"
    assert outcome.winner_count == 1
    # Invalid votes are SURFACED (recorded), never counted.
    assert {record.agent_id for record in outcome.invalid} == {"b", "c"}
    assert all(not record.valid for record in outcome.invalid)
    # All per-voter records are retained for durability/replay.
    assert {record.agent_id for record in outcome.votes} == {"a", "b", "c"}


def test_all_invalid_yields_no_winner() -> None:
    declared = ("yes", "no")
    votes = [
        member_vote_record(agent_id="a", raw="whatever", declared_options=declared),
        member_vote_record(agent_id="b", raw="hmm", declared_options=declared),
    ]
    outcome = build_vote_outcome(
        votes,
        arbitration=ArbitrationStrategy.MAJORITY,
        declared_options=declared,
    )
    assert outcome.tally == {}
    assert outcome.winner is None
    assert outcome.winner_count == 0
    assert len(outcome.invalid) == 2


def test_tie_is_explicit_with_deterministic_winner() -> None:
    votes = [
        member_vote_record(agent_id="a", raw="bbb"),
        member_vote_record(agent_id="b", raw="aaa"),
    ]
    outcome = build_vote_outcome(votes, arbitration=ArbitrationStrategy.MAJORITY)

    assert outcome.tie is True
    assert outcome.tied_options == ("aaa", "bbb")
    # Deterministic (alpha-first) winner for replay stability.
    assert outcome.winner == "aaa"
    assert outcome.winner_count == 1


def test_vote_outcome_is_frozen_and_round_trips_via_as_payload() -> None:
    votes = [
        member_vote_record(agent_id="a", raw="yes"),
        member_vote_record(agent_id="b", raw="no"),
    ]
    outcome = build_vote_outcome(votes, arbitration=ArbitrationStrategy.MAJORITY)

    with pytest.raises(ValidationError):
        outcome.winner = "no"  # type: ignore[misc]

    payload = outcome.as_payload()
    assert payload["strategy"] == "vote"
    assert payload["schema_version"] == outcome.schema_version
    assert payload["tally"] == {"yes": 1, "no": 1}
    assert payload["arbitration"] == ArbitrationStrategy.MAJORITY.value
    # Durable/replayable: per-voter records survive the round-trip.
    rebuilt = [MemberVoteRecord.model_validate(v) for v in payload["votes"]]
    assert rebuilt == list(outcome.votes)


def test_coordinator_outcome_is_frozen_and_round_trips() -> None:
    outcome = CoordinatorOutcome(coordinator_id="lead", summary="final answer")

    with pytest.raises(ValidationError):
        outcome.summary = "changed"  # type: ignore[misc]

    payload = outcome.as_payload()
    assert payload == {
        "schema_version": outcome.schema_version,
        "strategy": "coordinator_summary",
        "coordinator_id": "lead",
        "summary": "final answer",
    }


def test_member_vote_record_back_compat_any_nonempty_is_valid() -> None:
    record = member_vote_record(agent_id="a", raw="Anything Goes.")
    assert isinstance(record, MemberVoteRecord)
    assert record.canonical == "anything goes"
    assert record.valid is True
    # An empty/blank vote is not valid even without declared options.
    blank = member_vote_record(agent_id="b", raw="   ")
    assert blank.valid is False


def test_protocol_vote_options_round_trip_through_model_dump() -> None:
    protocol = CollaborationProtocol(
        mode="vote",
        topology="broadcast",
        aggregation="vote",
        arbitration="majority",
        vote_options=("yes", "no"),
    )
    dumped = protocol.model_dump(mode="json")
    assert dumped["vote_options"] == ["yes", "no"]
    rebuilt = CollaborationProtocol.model_validate(dumped)
    assert rebuilt.vote_options == ("yes", "no")


def test_vote_outcome_typed_outcome_drives_payload_no_raw_text_key() -> None:
    # The canonical tally key is never the raw surface text.
    votes = [member_vote_record(agent_id="a", raw="  Yes! ")]
    outcome: VoteOutcome = build_vote_outcome(
        votes, arbitration=ArbitrationStrategy.MAJORITY
    )
    assert list(outcome.tally) == ["yes"]
    assert "  Yes! " not in outcome.tally
