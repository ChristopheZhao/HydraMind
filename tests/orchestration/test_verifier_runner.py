"""Deterministic verifier runners."""

from __future__ import annotations

import pytest

from hydramind.control import AgentReport, RuntimeSession, TaskContract
from hydramind.control.states import DecisionAction
from hydramind.orchestration import TaskContractVerifierRunner


def _session() -> RuntimeSession:
    return RuntimeSession(workflow_name="verify")


def _node_config(contract: TaskContract) -> dict[str, object]:
    return {"task_contract": contract.model_dump(mode="json")}


@pytest.mark.asyncio
async def test_task_contract_verifier_passes_existing_expected_artifacts(tmp_path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "summary.txt").write_text("ok", encoding="utf-8")
    runner = TaskContractVerifierRunner(tmp_path)
    report = AgentReport(node_key="write", agent_id="writer", output={"done": True})

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(
            TaskContract(expected_artifacts=("reports/summary.txt",))
        ),
        report=report,
    )

    assert verified.output == {"done": True}
    assert verified.verifier_results[0].name == "artifact.exists"
    assert verified.verifier_results[0].passed is True
    assert verified.verifier_results[0].evidence_refs == ("reports/summary.txt",)
    assert verified.feedback == ()


@pytest.mark.asyncio
async def test_task_contract_verifier_feedback_for_missing_artifacts(tmp_path) -> None:
    runner = TaskContractVerifierRunner(tmp_path)
    report = AgentReport(node_key="write", agent_id="writer")

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(
            TaskContract(expected_artifacts=("reports/missing.txt",))
        ),
        report=report,
    )

    result = verified.verifier_results[0]
    assert result.passed is False
    assert result.detail["missing"] == ["reports/missing.txt"]
    assert result.repair_instruction == "Create expected artifact(s): reports/missing.txt"
    feedback = verified.feedback[0]
    assert feedback.source == "verifier.artifact_exists"
    assert feedback.target_node_key == "write"
    assert feedback.suggested_action is DecisionAction.REVISE
    assert "missing expected artifact" in feedback.message


@pytest.mark.asyncio
async def test_task_contract_verifier_rejects_path_escape(tmp_path) -> None:
    runner = TaskContractVerifierRunner(tmp_path)

    verified = await runner.verify(
        session=_session(),
        node_config=_node_config(TaskContract(expected_artifacts=("../escape.txt",))),
        report=AgentReport(node_key="write", agent_id="writer"),
    )

    result = verified.verifier_results[0]
    assert result.passed is False
    assert result.detail["invalid"] == ["../escape.txt"]
    assert verified.feedback[0].evidence_refs == ("../escape.txt",)
