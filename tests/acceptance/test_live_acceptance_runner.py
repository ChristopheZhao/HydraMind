"""S7b live-acceptance runner unit tests — NO network, NO credentials.

PLAN-20260618-001 §7 S7b: the live runner exists in code, but its EXECUTION is
credential-gated and the user's step. These tests prove:

1. WITHOUT credentials the runner reports not-run / not-proven and makes NO live
   call (no backend constructed, no network);
2. the typed report shape/labeling is correct when a (fake, in-process) invoker
   stands in for a live model — the report carries the LIVE class but is built
   from an injected fake, NOT a real provider, and we never claim it as proof of
   live quality here;
3. a replay/mock run can never be labeled live (the offline guard rejects it).

NO test here performs a live API call.
"""

from __future__ import annotations

import pytest

from hydramind.governance import (
    AcceptanceClass,
    AcceptanceFailureCategory,
    AcceptanceLabelError,
    AcceptanceReport,
    ExecutionHarnessRef,
    ModelProviderRef,
    TaskRef,
    has_provider_credentials,
    require_offline_class,
    run_live_acceptance,
)
from hydramind.governance.live_acceptance import (
    PROVIDER_CREDENTIAL_KEYS,
    LiveAcceptanceTask,
)


def _task(
    acceptance_class: AcceptanceClass = AcceptanceClass.LIVE_AGENT,
) -> LiveAcceptanceTask:
    return LiveAcceptanceTask(
        task_id="fixed-task-1",
        task_description="reply with OK",
        prompt="Reply with OK.",
        tools_environment="live providers + web/artifact tools",
        evaluator_profile="nonempty-response",
        acceptance_class=acceptance_class,
    )


def _empty_env() -> dict[str, str]:
    return {key: "" for key in PROVIDER_CREDENTIAL_KEYS}


def test_has_provider_credentials_false_when_absent() -> None:
    assert has_provider_credentials(_empty_env()) is False
    assert has_provider_credentials({}) is False


def test_has_provider_credentials_true_when_present() -> None:
    assert has_provider_credentials({"DEEPSEEK_API_KEY": "sk-xxx"}) is True


@pytest.mark.asyncio
async def test_without_credentials_reports_not_run_and_makes_no_call() -> None:
    outcome = await run_live_acceptance(
        _task(),
        provider="deepseek",
        harness_name="HydraMindExecutionHarness",
        env=_empty_env(),
        # No ``invoker`` — must hit the credential gate, never construct a backend.
    )
    assert outcome.ran is False
    assert outcome.report is None
    assert "no credentials" in outcome.reason
    assert "live_agent" in outcome.reason


@pytest.mark.asyncio
async def test_live_mas_without_credentials_also_not_run() -> None:
    outcome = await run_live_acceptance(
        _task(AcceptanceClass.LIVE_MAS),
        provider="glm",
        harness_name="HydraMindExecutionHarness",
        env=_empty_env(),
    )
    assert outcome.ran is False
    assert outcome.report is None
    assert "live_mas" in outcome.reason


@pytest.mark.asyncio
async def test_fake_invoker_produces_correctly_typed_live_report() -> None:
    async def _fake_invoker(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        # Stands in for a live model WITHOUT a network call. This is a test
        # double; the report is typed correctly but is NOT proof of live quality.
        return ("deepseek-chat", "OK", 5, 1)

    outcome = await run_live_acceptance(
        _task(),
        provider="deepseek",
        harness_name="HydraMindExecutionHarness",
        harness_id="default",
        invoker=_fake_invoker,
    )
    assert outcome.ran is True
    report = outcome.report
    assert isinstance(report, AcceptanceReport)
    assert report.acceptance_class is AcceptanceClass.LIVE_AGENT
    assert report.success is True
    assert report.failure_category is AcceptanceFailureCategory.NONE
    assert report.model_provider.provider == "deepseek"
    assert report.model_provider.model_id == "deepseek-chat"
    assert report.execution_harness.name == "HydraMindExecutionHarness"
    assert report.cost is not None and report.cost.total_tokens == 6
    assert report.latency_ms is not None
    # Report is reportable by task+provider+harness+evaluator.
    payload = report.as_payload()
    assert payload["task"]["id"] == "fixed-task-1"
    assert payload["acceptance_class"] == "live_agent"


@pytest.mark.asyncio
async def test_recorded_provider_is_derived_from_route_not_caller_label() -> None:
    # M3 (PLAN-20260621-001): the recorded provider must match the route that
    # produced model_id, not a static caller label. With env routing role->glm and
    # a model_id of glm-5.1, recording provider="deepseek" (the old bug) is wrong.
    async def _glm_invoker(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        return ("glm-5.1", "OK", 5, 1)

    outcome = await run_live_acceptance(
        _task(),
        provider="deepseek",  # coarse/incorrect caller label
        harness_name="HydraMindExecutionHarness",
        invoker=_glm_invoker,
        env={"HYDRAMIND_DEFAULT_PROVIDER": "glm", "HYDRAMIND_DEFAULT_MODEL": "glm-5.1"},
    )
    assert outcome.report is not None
    # Provider is corrected to the route's provider and is consistent with model_id.
    assert outcome.report.model_provider.provider == "glm"
    assert outcome.report.model_provider.model_id == "glm-5.1"


@pytest.mark.asyncio
async def test_invoker_failure_is_surfaced_as_typed_model_error() -> None:
    async def _boom(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        raise RuntimeError("provider 500")

    outcome = await run_live_acceptance(
        _task(),
        provider="kimi",
        harness_name="HydraMindExecutionHarness",
        invoker=_boom,
    )
    assert outcome.ran is True
    assert outcome.report is not None
    assert outcome.report.success is False
    assert outcome.report.failure_category is AcceptanceFailureCategory.MODEL_ERROR
    assert "provider 500" in (outcome.report.notes or "")


@pytest.mark.asyncio
async def test_empty_live_response_is_failure_not_success() -> None:
    async def _empty(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        return ("kimi-k2", "   ", 3, 0)

    outcome = await run_live_acceptance(
        _task(),
        provider="kimi",
        harness_name="HydraMindExecutionHarness",
        invoker=_empty,
    )
    assert outcome.report is not None
    assert outcome.report.success is False
    assert outcome.report.failure_category is AcceptanceFailureCategory.MODEL_ERROR


def test_live_task_rejects_offline_acceptance_class() -> None:
    with pytest.raises(ValueError):
        LiveAcceptanceTask(
            task_id="t",
            task_description="d",
            prompt="p",
            tools_environment="e",
            evaluator_profile="ev",
            acceptance_class=AcceptanceClass.REPLAY,  # not a live class
        )


@pytest.mark.asyncio
async def test_live_mas_with_fake_team_invoker_reports_multi_member_evidence() -> None:
    # S7b GOAL 2: a LIVE_MAS run with an injected fake TEAM invoker produces an
    # AcceptanceReport labeled LIVE_MAS whose content/usage are MULTI-MEMBER
    # derived (aggregated from >1 collaborating member). No network, no creds.
    async def _fake_team_invoker(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        # Stands in for a real native-team collaboration: aggregated content from
        # two members + summed per-member usage. Test double, not live proof.
        aggregated = "[drafter] draft\n[reviewer] reviewed"
        return ("team", aggregated, 9, 4)

    outcome = await run_live_acceptance(
        _task(AcceptanceClass.LIVE_MAS),
        provider="glm",
        harness_name="HydraMindExecutionHarness",
        invoker=_fake_team_invoker,
    )
    assert outcome.ran is True
    report = outcome.report
    assert isinstance(report, AcceptanceReport)
    assert report.acceptance_class is AcceptanceClass.LIVE_MAS
    assert report.success is True
    assert report.failure_category is AcceptanceFailureCategory.NONE
    # Multi-member evidence: both members appear in the aggregated content, and
    # usage is the SUM of both members' per-turn usage.
    assert report.notes is None
    assert report.cost is not None
    assert report.cost.input_tokens == 9
    assert report.cost.output_tokens == 4
    assert report.cost.total_tokens == 13
    payload = report.as_payload()
    assert payload["acceptance_class"] == "live_mas"


@pytest.mark.asyncio
async def test_default_team_invoker_drives_native_team_offline_via_mockbackend() -> None:
    # S7b GOAL 2 wiring proof: the team-invoker shape runs through the EXISTING
    # collaboration machinery (CollaborationExecutor -> NativeTeamExecutor) end to
    # end. We drive it with hydramind.testing's MockProvider (replay) ONLY to prove
    # the wiring; the resulting report MUST NOT be labeled live for a replay run.
    from hydramind.governance.live_acceptance import _aggregate_team_result
    from hydramind.harness import Message, MessageRole, ModelHint
    from hydramind.mas import AgentSpec, TeamSpec
    from hydramind.observability import ObservationEventKind
    from hydramind.orchestration.collaboration import (
        CollaborationExecutionRequest,
        CollaborationExecutor,
    )
    from hydramind.orchestration.execution_harness import ProviderExecutionHarnessRuntime
    from hydramind.orchestration.subagent_spawn import SubagentSpawner
    from hydramind.testing import MockProvider
    from hydramind.tools import ToolContext

    backend = MockProvider()

    async def _emit_trace(
        kind: ObservationEventKind,
        *,
        session_id: str,
        node_key: str,
        execution_id: str,
        trace_id: str,
        detail: dict | None = None,
        level: str = "info",
        actor: str | None = None,
    ) -> None:
        return None

    async def _execute_tool_round(**_: object) -> list[object]:
        raise RuntimeError("no tools expected")

    executor = CollaborationExecutor(
        subagent_spawner=SubagentSpawner.from_runtime(ProviderExecutionHarnessRuntime(backend)),
        model_hint=ModelHint.BALANCED,
        max_tool_rounds=1,
        emit_trace=_emit_trace,
        execute_tool_round=_execute_tool_round,
        tool_context_for=lambda node_key, role: ToolContext(node_key=node_key, role=role),
    )
    team = TeamSpec(
        id="live-mas-acceptance-team",
        members=(
            AgentSpec(id="drafter", role="executor"),
            AgentSpec(id="reviewer", role="reviewer"),
        ),
        protocol={"topology": "pipeline"},
    )
    request = CollaborationExecutionRequest(
        session_id="live-mas-acceptance",
        node_key="collaborate",
        execution_id="exec",
        trace_id="trace",
        messages=[Message(role=MessageRole.USER, content="Reply with OK.")],
        system="reply with OK",
        tools=None,
        agent_role="executor",
        node_config={"mas_team": team.model_dump(mode="json")},
    )

    result = await executor.invoke_team(request)
    model_id, content, in_tok, out_tok = _aggregate_team_result(result)

    # Two members genuinely ran through the collaboration machinery.
    assert "[drafter]" in content and "[reviewer]" in content
    assert in_tok >= 0 and out_tok >= 0

    # A replay/mock-driven run can NEVER be labeled live. The acceptance report
    # for this deterministic run is REPLAY; the offline guard rejects relabeling
    # it as LIVE_MAS (the §7 replay-cannot-be-live guarantee, preserved).
    replay_report = AcceptanceReport(
        task=TaskRef(id="t", description="d"),
        model_provider=ModelProviderRef(provider="offline", model_id=model_id),
        execution_harness=ExecutionHarnessRef(name="replay-fixture"),
        tools_environment="replay",
        evaluator_profile="replay-regression",
        acceptance_class=AcceptanceClass.REPLAY,
        success=True,
    )
    assert require_offline_class(replay_report) is replay_report
    mislabeled = replay_report.model_copy(
        update={"acceptance_class": AcceptanceClass.LIVE_MAS}
    )
    with pytest.raises(AcceptanceLabelError):
        require_offline_class(mislabeled)


def test_offline_guard_rejects_live_label_on_replay_report() -> None:
    # A replay run must never be labeled live (the §7 core negative case).
    replay_report = AcceptanceReport(
        task=TaskRef(id="t", description="d"),
        model_provider=ModelProviderRef(provider="offline", model_id="replay"),
        execution_harness=ExecutionHarnessRef(name="replay-fixture"),
        tools_environment="replay",
        evaluator_profile="replay-regression",
        acceptance_class=AcceptanceClass.REPLAY,
        success=True,
    )
    # Correctly labeled replay passes the offline guard.
    assert require_offline_class(replay_report) is replay_report

    # If someone tried to relabel that SAME deterministic run as live, the guard
    # rejects it.
    mislabeled = replay_report.model_copy(
        update={"acceptance_class": AcceptanceClass.LIVE_AGENT}
    )
    with pytest.raises(AcceptanceLabelError):
        require_offline_class(mislabeled)
