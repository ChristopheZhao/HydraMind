"""Tests for the MAS Production Blog scenario static deliverables.

These tests cover the FRAMEWORK-PUBLIC parts of the scenario directory:

- ``quality_contract.json`` parses through ``load_goal_quality_contract`` and
  round-trips through ``GoalArtifactQualityContract.model_dump(mode="json")``.
- ``goal_spec.json`` is a JSON object with all expected scenario keys/types.
- ``evidence_collector.py`` is importable and exposes a ``main()`` entry point.
- The redaction guard inside the collector rejects secrets that might leak via
  a ``ToolExecution.metadata`` payload — fed a deliberately-secret-shaped
  fixture session, the produced JSON files contain no copy of the fake secret
  value, and the collector still completes without raising.

No live provider or tool is invoked; the fixture session is built directly via
pydantic types.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hydramind.control import (
    GoalArtifactQualityContract,
    InMemorySessionStore,
    NodeAttempt,
    NodeState,
    RuntimeSession,
    SessionService,
    ToolExecution,
)
from hydramind.control.states import (
    AttemptStatus,
    NodeStatus,
    SessionStatus,
    ToolExecutionStatus,
)
from hydramind.orchestration import load_goal_quality_contract

SCENARIO_DIR = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "scenarios"
    / "mas-production-blog"
)
CONTRACT_PATH = SCENARIO_DIR / "quality_contract.json"
SPEC_PATH = SCENARIO_DIR / "goal_spec.json"
COLLECTOR_PATH = SCENARIO_DIR / "evidence_collector.py"
RUN_SCENARIO_PATH = SCENARIO_DIR / "run_scenario.sh"


def _load_evidence_collector():
    """Import ``evidence_collector.py`` as a module without polluting sys.path globally."""

    spec = importlib.util.spec_from_file_location(
        "mas_production_blog_evidence_collector",
        COLLECTOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_quality_contract_loads_via_framework_loader() -> None:
    """``load_goal_quality_contract`` accepts the scenario contract file."""

    contract = load_goal_quality_contract(CONTRACT_PATH)

    assert isinstance(contract, GoalArtifactQualityContract)
    assert contract.min_length == 18000
    assert contract.min_reference_urls == 5
    assert contract.min_image_refs == 2
    assert contract.min_local_image_refs == 2
    assert contract.local_asset_refs_under_artifact_root is True
    assert len(contract.required_sections) == 8
    assert contract.required_sections[0] == "## 引言"
    assert contract.required_sections[-1] == "## 参考文献"
    assert contract.semantic_rubric is not None
    assert contract.semantic_rubric.enabled is True
    assert {check.name for check in contract.semantic_rubric.checks} == {
        "technical_depth",
        "source_grounding",
        "non_mechanical_expression",
    }
    for check in contract.semantic_rubric.checks:
        assert check.min_score == pytest.approx(0.6)


def test_quality_contract_round_trips_through_pydantic() -> None:
    """``model_dump(mode='json')`` -> ``model_validate`` is lossless."""

    contract = load_goal_quality_contract(CONTRACT_PATH)
    dumped = contract.model_dump(mode="json")
    revived = GoalArtifactQualityContract.model_validate(dumped)

    assert revived == contract
    assert revived.model_dump(mode="json") == dumped


def test_goal_spec_json_is_well_formed_object() -> None:
    """``goal_spec.json`` parses as a JSON object with the documented shape."""

    payload = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    assert isinstance(payload, dict)
    expected_string_keys = {"objective"}
    expected_list_keys = {
        "available_tools",
        "required_tools",
        "expected_artifacts",
        "constraints",
        "success_criteria",
    }
    assert expected_string_keys.issubset(payload.keys())
    assert expected_list_keys.issubset(payload.keys())
    assert isinstance(payload["objective"], str) and payload["objective"]
    for key in expected_list_keys:
        value = payload[key]
        assert isinstance(value, list), f"{key} must be a JSON array"
        assert value, f"{key} must not be empty"
        assert all(isinstance(item, str) and item for item in value), (
            f"{key} must contain non-empty strings only"
        )
    assert set(payload["required_tools"]).issubset(set(payload["available_tools"]))
    assert payload["expected_artifacts"] == ["blog/mas-production-blog.md"]
    assert "../assets/" in payload["objective"]
    assert "inline URL" in payload["objective"]
    assert "semantic.source_grounding" in "\n".join(payload["success_criteria"])
    assert any("不要只在" in item for item in payload["constraints"])


def test_run_scenario_forwards_trace_and_budget_flags() -> None:
    """The scenario wrapper must produce trace evidence and enough repair budget."""

    script = RUN_SCENARIO_PATH.read_text(encoding="utf-8")

    assert "TRACE_PATH=" in script
    assert 'CLI_ARGS+=("--trace-path" "${TRACE_PATH}")' in script
    assert 'CLI_ARGS+=("--max-tool-rounds" "${MAX_TOOL_ROUNDS}")' in script
    assert 'CLI_ARGS+=("--max-auto-repairs" "${MAX_AUTO_REPAIRS}")' in script
    assert "currently has no `--trace-path` flag" not in script


def test_evidence_collector_module_exposes_main() -> None:
    """``evidence_collector.py`` is importable as a module and exposes ``main``."""

    module = _load_evidence_collector()

    assert hasattr(module, "main"), "evidence_collector must expose a main() entry"
    assert callable(module.main)
    # And the building-block helpers tests rely on:
    for symbol in (
        "collect_evidence",
        "collect_ledger",
        "collect_verifier_results",
        "collect_planner_diagnostics",
        "assert_no_secrets",
    ):
        assert hasattr(module, symbol), f"evidence_collector missing {symbol}"


def _build_fixture_session_with_secret() -> RuntimeSession:
    """Build a RuntimeSession whose tool execution metadata has a fake secret."""

    now = datetime.now(UTC)
    tool = ToolExecution(
        node_key="writer",
        execution_id="exec-fixture",
        trace_id="trace-fixture",
        tool_call_id="call-fixture",
        tool_name="search.web",
        round_no=1,
        status=ToolExecutionStatus.SUCCEEDED,
        # The framework would normally redact arguments/result_preview before
        # they ever reach this point; the collector still drops them entirely.
        arguments={"query": "<redacted>"},
        result_preview={"content_preview": "<redacted>"},
        is_error=False,
        content_length=42,
        started_at=now,
        finished_at=now,
        metadata={
            "api_key": "FAKE-SECRET-VALUE-DO-NOT-LEAK",
            "provider": "brave",
        },
    )
    attempt = NodeAttempt(
        node_key="writer",
        attempt_no=1,
        status=AttemptStatus.SUCCEEDED,
        tool_executions=[tool],
        output={"_verifier_results": [], "_feedback": []},
        started_at=now,
        finished_at=now,
    )
    node = NodeState(
        key="writer",
        status=NodeStatus.COMPLETED,
        attempts=[attempt],
        gates=[],
    )
    return RuntimeSession(
        id="sess-fixture",
        workflow_name="goal_driven_session",
        version=0,
        status=SessionStatus.COMPLETED,
        nodes={"writer": node},
        input_payload={},
        summary_output={},
        metadata={
            "execution_plan": {
                "metadata": {
                    "planner_diagnostics": {"name": "fixture", "tasks": 1},
                    "last_plan_delta_diagnostics": None,
                }
            }
        },
    )


def test_evidence_collector_redacts_tool_metadata_secret(tmp_path: Path) -> None:
    """A secret-shaped tool metadata value must NOT survive evidence collection."""

    module = _load_evidence_collector()
    session = _build_fixture_session_with_secret()
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "evidence"

    manifest = module.collect_evidence(
        session=session,
        artifact_root=artifact_root,
        output_dir=output_dir,
        trace_path=None,
    )

    assert manifest["redaction_check"] == "passed"
    # The ledger must include the tool record but must NOT echo the secret value.
    ledger_payload: list[dict[str, Any]] = json.loads(
        (output_dir / "ledger.json").read_text(encoding="utf-8")
    )
    assert len(ledger_payload) == 1
    entry = ledger_payload[0]
    assert entry["tool_name"] == "search.web"
    assert entry["status"] == "succeeded"
    assert entry["metadata_keys"] == ["api_key", "provider"]
    serialized = (output_dir / "ledger.json").read_text(encoding="utf-8")
    assert "FAKE-SECRET-VALUE-DO-NOT-LEAK" not in serialized

    # Every produced JSON file must also be free of that value.
    for name in ("verifier_results.json", "planner_diagnostics.json", "manifest.json"):
        text = (output_dir / name).read_text(encoding="utf-8")
        assert "FAKE-SECRET-VALUE-DO-NOT-LEAK" not in text, (
            f"secret leaked into {name}"
        )


def test_evidence_collector_guard_raises_on_explicit_leak(tmp_path: Path) -> None:
    """The redaction guard must abort with SystemExit(2) when a secret leaks."""

    module = _load_evidence_collector()
    payload = {"api_key": "PLAINTEXT-NOT-REDACTED"}

    with pytest.raises(SystemExit) as excinfo:
        module.assert_no_secrets("fixture", payload)

    assert excinfo.value.code == 2


def test_evidence_collector_loads_session_from_store(tmp_path: Path) -> None:
    """The collector resolves a session via ``SessionService.get_session``."""

    module = _load_evidence_collector()
    session = _build_fixture_session_with_secret()
    store = InMemorySessionStore()
    service = SessionService(store)
    # SessionService is the only mutation owner; we go through it to seed.
    import asyncio

    async def _seed() -> RuntimeSession:
        await store.put(session)
        loaded = await service.get_session(session.id)
        assert loaded is not None
        return loaded

    loaded = asyncio.run(_seed())
    assert loaded.id == session.id
    assert loaded.status is SessionStatus.COMPLETED
    # The module-level helper accepts any SessionStore Protocol implementation.
    resolved = asyncio.run(module.load_session(store, session.id))
    assert resolved.id == session.id
