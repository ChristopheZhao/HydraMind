"""DoD-2 capstone: the native_team example runs end-to-end and delivers a
VERIFIED FILE ARTIFACT reproducibly OFFLINE.

This drives the SAME example shipped under ``examples/native_team/`` through the
real run path (``runtime.run_workflow_file`` → OrchestratorAgent + ControlPlane +
SessionService), NOT the executor in isolation. The MockProvider is driven by the
committed input-keyed fixture (``mock_fixture.json`` via
``MockProvider.from_fixture``), so the team members — including the writer's
``artifact.write_text`` tool call — run deterministically with no network/live
calls. The artifact is produced by the TEAM (the writer's scripted tool call
drained through the real tool runner), not by the test harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hydramind.control import InMemorySessionStore, SessionStatus
from hydramind.harness.base import StopReason, ToolCall
from hydramind.observability import Emitter, ListObserver, ObservationEventKind
from hydramind.orchestration.agent import ExecutionHarnessDependencies
from hydramind.orchestration.explicit_submit_execution_harness import ExplicitSubmitExecutionHarness
from hydramind.runtime import run_workflow_file
from hydramind.testing import MockProvider, ScriptedTurn

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "native_team"
WORKFLOW = EXAMPLE_DIR / "workflow.yaml"
FIXTURE = EXAMPLE_DIR / "mock_fixture.json"
ARTIFACT_NAME = "brief.md"


async def _run_example(artifact_root: Path):
    """Run the shipped example through the full runtime with the fixture."""
    backend = MockProvider.from_fixture(FIXTURE)
    return await run_workflow_file(
        WORKFLOW,
        provider=backend,
        env_file=None,
        artifact_root=artifact_root,
    )


def _artifact_verifier_passed(session) -> bool:
    """True when the TaskContract artifact-existence verifier passed.

    The verifier evidence is threaded onto the node report and persisted into
    the recorded node output (``_verifier_results``).
    """
    for node in session.nodes.values():
        attempt = node.latest_attempt()
        if attempt is None:
            continue
        for result in attempt.output.get("_verifier_results", ()):
            if result.get("name") == "artifact.exists":
                return bool(result.get("passed"))
    return False


def _submit_factory(deps: ExecutionHarnessDependencies) -> ExplicitSubmitExecutionHarness:
    return ExplicitSubmitExecutionHarness(
        execution=deps.execution,
        context_builder=deps.context_builder,
        tool_provider=deps.tool_provider,
        tool_loop=deps.tool_loop,
        collaboration=deps.collaboration,
        report_builder=deps.report_builder,
        verifier_runner=deps.verifier_runner,
        max_tool_rounds=deps.max_tool_rounds,
    )


def _native_team_swap_workflow(path: Path) -> None:
    path.write_text(
        """
name: native_team_swap
version: "1"
nodes:
  - key: collaborate
    role: coordinator
    tools:
      - artifact.write_text
    config:
      mas_team:
        id: swap-team
        protocol:
          mode: team
          topology: pipeline
          aggregation: collect
        tools:
          - artifact.write_text
        members:
          - id: researcher
            role: researcher
            instructions: Research.
          - id: analyst
            role: analyst
            instructions: Analyze.
          - id: writer
            role: writer
            tools:
              - artifact.write_text
            instructions: Write brief.md.
      task_contract:
        objective: Produce brief.md.
        expected_artifacts:
          - brief.md
""".lstrip(),
        encoding="utf-8",
    )


def _swap_provider() -> MockProvider:
    return MockProvider(
        scripted=[
            ScriptedTurn(content="research facts"),
            ScriptedTurn(content="analysis insights"),
            ScriptedTurn(
                content="writing brief",
                tool_calls=(
                    ToolCall(
                        id="write-brief",
                        name="artifact.write_text",
                        arguments={
                            "path": "brief.md",
                            "content": "# Swap Proof\n\nFixed native-team protocol.\n",
                        },
                    ),
                ),
                stop_reason=StopReason.TOOL_USE,
            ),
            ScriptedTurn(content="writer done"),
        ]
    )


def _trace_harness_ids(observer: ListObserver) -> set[str]:
    return {
        str(event.detail["harness_id"])
        for event in observer.events
        if event.kind is ObservationEventKind.EXECUTION_HARNESS_SELECTED
        and "harness_id" in event.detail
    }


@pytest.mark.asyncio
async def test_native_team_example_delivers_verified_artifact_e2e(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "art"
    session = await _run_example(artifact_root)

    # 1. The session reaches COMPLETED through the orchestrator/control plane.
    assert session.status is SessionStatus.COMPLETED

    # 2. The artifact FILE exists under the tmp artifact_root.
    artifact_path = artifact_root / ARTIFACT_NAME
    assert artifact_path.exists(), "team did not produce the brief.md artifact"
    assert artifact_path.read_text(encoding="utf-8").strip()

    # 3. The artifact verifier passed (TaskContract existence + path safety).
    assert _artifact_verifier_passed(session), (
        "artifact verifier did not pass for the produced brief.md"
    )

    # The artifact is produced by the TEAM (the writer's tool call drained
    # through the real tool runner), evidenced by a drained tool call on the
    # writer member's collaboration result.
    collaboration = session.summary_output["collaboration"]
    writer = next(member for member in collaboration["results"] if member["agent_id"] == "writer")
    assert writer["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_native_team_example_is_reproducible(tmp_path: Path) -> None:
    # Running the same fixture twice yields the IDENTICAL verified artifact —
    # the reproducible half of DoD-2.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    session_a = await _run_example(root_a)
    session_b = await _run_example(root_b)

    assert session_a.status is SessionStatus.COMPLETED
    assert session_b.status is SessionStatus.COMPLETED

    artifact_a = (root_a / ARTIFACT_NAME).read_bytes()
    artifact_b = (root_b / ARTIFACT_NAME).read_bytes()
    assert artifact_a == artifact_b, "replay produced a different artifact (non-deterministic)"
    assert _artifact_verifier_passed(session_a)
    assert _artifact_verifier_passed(session_b)


@pytest.mark.asyncio
async def test_same_native_team_swaps_execution_harness_with_fixed_layers(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow.yaml"
    artifact_root = tmp_path / "artifacts"
    _native_team_swap_workflow(workflow)
    default_observer = ListObserver()
    submit_observer = ListObserver()
    shared_store = InMemorySessionStore()

    default_session = await run_workflow_file(
        workflow,
        provider=_swap_provider(),
        env_file=None,
        artifact_root=artifact_root,
        session_store=shared_store,
        emitter=Emitter([default_observer]),
    )
    submit_session = await run_workflow_file(
        workflow,
        provider=_swap_provider(),
        env_file=None,
        artifact_root=artifact_root,
        session_store=shared_store,
        emitter=Emitter([submit_observer]),
        execution_harness_factory=_submit_factory,
    )

    assert default_session.status is SessionStatus.COMPLETED
    assert submit_session.status is SessionStatus.COMPLETED
    assert _artifact_verifier_passed(default_session)
    assert _artifact_verifier_passed(submit_session)
    assert (artifact_root / ARTIFACT_NAME).exists()

    for session in (default_session, submit_session):
        collaboration = session.summary_output["collaboration"]
        assert collaboration["team_id"] == "swap-team"
        assert collaboration["protocol"]["topology"] == "pipeline"
        assert collaboration["aggregation"] == {"strategy": "collect"}
        assert collaboration["member_count"] == 3
        writer = next(
            item for item in collaboration["results"] if item["agent_id"] == "writer"
        )
        assert writer["tool_call_count"] == 1
        interaction = next(iter(session.durable_interactions.values()))
        assert interaction.team_id == "swap-team"
        assert interaction.topology == "pipeline"
        assert interaction.status.value == "completed"
        assert len(interaction.turns) == 3
        assert len(interaction.messages) == 3

    assert _trace_harness_ids(default_observer) == {"HydraMindExecutionHarness"}
    assert _trace_harness_ids(submit_observer) == {"ExplicitSubmitExecutionHarness"}

    # M1: the swap reaches EVERY MAS member — each member turn is driven by the
    # swapped harness's strategy, not the fixed default drain. This is the proof
    # that the orchestration layer coordinates multiple per-agent harness
    # strategies (concept-map boundary), not just the outer team node.
    default_members = default_session.summary_output["collaboration"]["results"]
    submit_members = submit_session.summary_output["collaboration"]["results"]
    assert {m["member_strategy"] for m in default_members} == {"default-drain"}
    assert {m["member_strategy"] for m in submit_members} == {"explicit-submit"}
