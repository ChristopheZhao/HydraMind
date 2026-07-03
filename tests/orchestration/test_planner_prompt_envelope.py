"""S95 / DEV-27: the planner prompt advertises ONLY the executed envelope.

The advertised collaboration protocol values must derive from the single
``hydramind.mas.capability.EXECUTED_*`` source of truth so the prompt and the
runtime can never drift (a frozen value advertised as available would invite the
planner to emit a TeamSpec the runtime fails closed on).
"""

from __future__ import annotations

from hydramind.mas.capability import (
    EXECUTED_AGGREGATIONS,
    EXECUTED_ARBITRATIONS,
    EXECUTED_MODE_TOPOLOGY_PAIRS,
    EXECUTED_MODES,
    EXECUTED_TOPOLOGIES,
)
from hydramind.orchestration.builtin_prompts import planner


def _advertised_protocol() -> dict[str, str]:
    return planner._INITIAL_PLAN_RESPONSE_SHAPE["tasks"][0]["team"]["protocol"]


def test_planner_prompt_advertises_exactly_the_executed_envelope() -> None:
    protocol = _advertised_protocol()
    assert set(protocol["mode"].split("|")) == {m.value for m in EXECUTED_MODES}
    assert set(protocol["topology"].split("|")) == {t.value for t in EXECUTED_TOPOLOGIES}
    assert set(protocol["aggregation"].split("|")) == {
        a.value for a in EXECUTED_AGGREGATIONS
    }
    assert set(protocol["arbitration"].split("|")) == {
        a.value for a in EXECUTED_ARBITRATIONS
    }


def test_planner_prompt_advertises_all_modes_after_s100() -> None:
    protocol = _advertised_protocol()
    # S100 executed the last frozen values; the planner now advertises the FULL
    # collaboration surface (execute-or-remove COMPLETE) — derived from EXECUTED_*.
    for value in ("team", "delegation", "debate", "vote"):
        assert value in protocol["mode"]
    assert "vote" in protocol["aggregation"]
    assert "majority" in protocol["arbitration"]
    # Topology values promoted in S94/S95 remain advertised.
    assert "coordinator" in protocol["topology"]
    assert "pipeline" in protocol["topology"]
    assert "coordinator_summary" in protocol["aggregation"]


def test_planner_system_prompt_names_protocol_combination_rules() -> None:
    system = planner.PLANNER_SYSTEM
    assert (
        "vote mode requires broadcast topology, vote aggregation, and majority "
        "arbitration"
    ) in system
    assert "vote aggregation is only valid for vote mode" in system
    assert "coordinator_summary requires coordinator topology" in system
    assert "delegation requires coordinator topology" in system


def test_planner_system_prompt_names_supported_mode_topology_pairs() -> None:
    expected = {
        f"{mode.value}/{topology.value}"
        for mode, topology in EXECUTED_MODE_TOPOLOGY_PAIRS
    }
    marker = "Supported mode/topology pairs: "
    supported = planner.PLANNER_SYSTEM.split(marker, 1)[1].split(".", 1)[0]

    assert set(supported.split("|")) == expected
