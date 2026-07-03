"""Live-agent + live-MAS acceptance runner (S7b).

Given live provider/tool credentials + a fixed task + fixed harness + fixed
evaluator, this runs the task and emits an :class:`AcceptanceReport` with
``acceptance_class`` ``LIVE_AGENT`` (single agent) or ``LIVE_MAS`` (native
team), reporting model/provider, harness, evaluator, success, cost, latency,
failure category, and recovery behavior.

CREDENTIAL GATE (hard rule, §7): without provider credentials this runner does
**not** make a live call. It returns a ``LiveAcceptanceOutcome`` with
``ran=False`` and a human-readable "not run / not proven (no credentials)"
reason. It NEVER fabricates an :class:`AcceptanceReport` with a live class for a
run that did not actually drive a live model — a replay/mock run can never be
reported as live acceptance.

This module is import-safe with no credentials and is unit-tested WITHOUT
network access (see ``tests/acceptance/test_live_acceptance_runner.py``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from hydramind.governance.acceptance import (
    AcceptanceClass,
    AcceptanceCost,
    AcceptanceFailureCategory,
    AcceptanceReport,
    ExecutionHarnessRef,
    ModelProviderRef,
    RecoveryBehavior,
    TaskRef,
)

# Provider credential keys (mirrors scripts/p0_acceptance.py / cli_doctor env).
PROVIDER_CREDENTIAL_KEYS: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "GLM_API_KEY",
)


@dataclass(frozen=True)
class LiveAcceptanceTask:
    """The fixed inputs an acceptance run is reported against."""

    task_id: str
    task_description: str
    prompt: str
    tools_environment: str
    evaluator_profile: str
    acceptance_class: AcceptanceClass = AcceptanceClass.LIVE_AGENT
    max_tokens: int = 256
    role: str = "executor"

    def __post_init__(self) -> None:
        if not self.acceptance_class.is_live:
            raise ValueError(
                "LiveAcceptanceTask.acceptance_class must be a live class "
                "(live_agent/live_mas); offline classes use the negative suite"
            )


@dataclass(frozen=True)
class LiveAcceptanceOutcome:
    """Result of a (possibly skipped) live acceptance attempt.

    ``ran`` is ``True`` only when a live model actually drove the task. When
    ``ran`` is ``False``, ``report`` is ``None`` and ``reason`` explains why
    (the standard case being "no credentials"). Callers must treat
    ``ran=False`` as "not proven", never as success.
    """

    ran: bool
    reason: str
    report: AcceptanceReport | None = None


def has_provider_credentials(
    env: Mapping[str, str] | None = None,
    *,
    keys: tuple[str, ...] = PROVIDER_CREDENTIAL_KEYS,
) -> bool:
    """True if at least one provider credential is present and non-empty."""

    source = os.environ if env is None else env
    return any(
        isinstance(source.get(key), str) and bool(str(source.get(key)).strip())
        for key in keys
    )


# A minimal model-invocation seam so unit tests can inject a fake WITHOUT a
# network call. Returns (model_id, content, input_tokens, output_tokens).
ModelInvoker = Callable[[LiveAcceptanceTask], Awaitable[tuple[str, str, int, int]]]


async def run_live_acceptance(
    task: LiveAcceptanceTask,
    *,
    provider: str,
    harness_name: str,
    harness_id: str | None = None,
    env: Mapping[str, str] | None = None,
    invoker: ModelInvoker | None = None,
    require_success_nonempty: bool = True,
    monotonic: Callable[[], float] = time.monotonic,
) -> LiveAcceptanceOutcome:
    """Run a live-agent/live-MAS acceptance task, or report not-run.

    Without provider credentials (and without an explicit ``invoker`` test
    seam), this returns ``LiveAcceptanceOutcome(ran=False, ...)`` and makes no
    live call. With credentials it drives the task and emits a typed
    :class:`AcceptanceReport` carrying the live class. The ``invoker`` argument
    exists ONLY so unit tests can verify report shape/labeling without a
    network; when ``invoker`` is supplied the credential gate is bypassed and
    the run is treated as live for typing purposes.
    """

    if invoker is None and not has_provider_credentials(env):
        return LiveAcceptanceOutcome(
            ran=False,
            reason=(
                "not run / not proven (no credentials): set one of "
                f"{', '.join(PROVIDER_CREDENTIAL_KEYS)} to run live "
                f"{task.acceptance_class.value} acceptance"
            ),
            report=None,
        )

    active_invoker = (
        invoker
        if invoker is not None
        else _default_invoker_for_class(task.acceptance_class, provider, env)
    )

    started = monotonic()
    failure = AcceptanceFailureCategory.NONE
    recovery = RecoveryBehavior.NO_FAILURE
    cost: AcceptanceCost | None = None
    success = False
    notes: str | None = None
    model_id = "unknown"
    try:
        model_id, content, input_tokens, output_tokens = await active_invoker(task)
        cost = AcceptanceCost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        success = bool(content.strip()) if require_success_nonempty else True
        if not success:
            failure = AcceptanceFailureCategory.MODEL_ERROR
            recovery = RecoveryBehavior.NOT_RECOVERED
            notes = "live model returned empty content"
    except Exception as exc:  # surfaced as a typed failure, not swallowed.
        failure = AcceptanceFailureCategory.MODEL_ERROR
        recovery = RecoveryBehavior.NOT_RECOVERED
        notes = f"live call raised {type(exc).__name__}: {exc}"
    latency_ms = (monotonic() - started) * 1000.0

    report = AcceptanceReport(
        task=TaskRef(id=task.task_id, description=task.task_description),
        model_provider=ModelProviderRef(
            provider=_resolve_provider_label(provider, role=task.role, env=env),
            model_id=model_id,
        ),
        execution_harness=ExecutionHarnessRef(name=harness_name, harness_id=harness_id),
        tools_environment=task.tools_environment,
        evaluator_profile=task.evaluator_profile,
        acceptance_class=task.acceptance_class,
        success=success,
        failure_category=failure,
        recovery_behavior=recovery,
        cost=cost,
        latency_ms=latency_ms,
        notes=notes,
    )
    return LiveAcceptanceOutcome(ran=True, reason="live acceptance run completed", report=report)


def _resolve_provider_label(
    fallback: str,
    *,
    role: str,
    env: Mapping[str, str] | None,
) -> str:
    """Truthful provider label = the role's resolved route provider.

    The caller passes a coarse provider label, but the actual model is chosen by
    the env role-route, so a static label can contradict the returned model_id
    (e.g. ``provider="deepseek"`` recorded against ``model_id="glm-5.1"``). Derive
    the provider from the SAME route that produced the model so the recorded
    identity is internally consistent. Falls back to ``fallback`` when no
    env/route is available (e.g. the unit-test invoker seam passes ``env=None``).
    """

    if not env:
        return fallback
    try:
        from hydramind.harness.routing import ModelRouter

        return ModelRouter.from_env(env).resolve(role).provider.name
    except Exception:
        return fallback


def _default_invoker_for_class(
    acceptance_class: AcceptanceClass,
    provider: str,
    env: Mapping[str, str] | None,
) -> ModelInvoker:
    """Select the default invoker by acceptance class (only reached live).

    LIVE_MAS drives a REAL native team (multiple members collaborating via the
    in-process collaboration machinery), so a credentialed live-MAS run produces
    genuine multi-agent evidence and is never a single-agent run mislabeled as
    live-MAS. LIVE_AGENT drives a single provider completion. The class-keyed
    selection is only consulted when no explicit ``invoker`` test seam is passed.
    """

    if acceptance_class is AcceptanceClass.LIVE_MAS:
        return _default_team_invoker(provider, env)
    return _default_provider_invoker(provider, env)


def _default_provider_invoker(
    provider: str,
    env: Mapping[str, str] | None,
) -> ModelInvoker:
    """Build a real single-agent provider invoker (only reached with creds).

    Imported lazily so the module stays import-safe and so unit tests that use
    the ``invoker`` seam never construct a real provider.
    """

    async def _invoke(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        from hydramind.harness import Message, MessageRole
        from hydramind.harness.factory import create_model_provider_from_env

        model_provider = create_model_provider_from_env(env)
        try:
            result = await model_provider.complete(
                messages=[Message(role=MessageRole.USER, content=task.prompt)],
                role=task.role,
                max_tokens=task.max_tokens,
            )
        finally:
            close = getattr(model_provider, "close", None)
            if callable(close):
                await close()
        return (
            result.model_id or "unknown",
            result.content,
            result.usage.input_tokens,
            result.usage.output_tokens,
        )

    return _invoke


def _default_team_invoker(
    provider: str,
    env: Mapping[str, str] | None,
) -> ModelInvoker:
    """Build a real NATIVE-TEAM invoker for LIVE_MAS (only reached with creds).

    Constructs a small native team (2 members in a PIPELINE topology) and runs
    it through the EXISTING in-process collaboration machinery
    (``CollaborationExecutor`` -> ``NativeTeamExecutor``, the same path the
    offline native-team example/tests drive) with the REAL provider producing
    each member's turn. The
    aggregated multi-member content + summed per-member token usage are returned
    so the report carries genuine multi-agent evidence.

    All collaboration imports are lazy so the module stays import-safe with no
    credentials; unit tests use the ``invoker`` seam and never reach this path.
    """

    async def _invoke(task: LiveAcceptanceTask) -> tuple[str, str, int, int]:
        from typing import Any

        from hydramind.harness import ModelHint
        from hydramind.harness.base import ToolCall, ToolResultBlock, ToolSpec
        from hydramind.harness.factory import create_model_provider_from_env
        from hydramind.mas import (
            AgentSpec,
            CollaborationProtocol,
            CollaborationTopology,
            TeamSpec,
        )
        from hydramind.observability import ObservationEventKind
        from hydramind.orchestration.collaboration import (
            CollaborationExecutionRequest,
            CollaborationExecutor,
        )
        from hydramind.orchestration.execution_harness import (
            ProviderExecutionHarnessRuntime,
        )
        from hydramind.orchestration.subagent_spawn import SubagentSpawner
        from hydramind.tools import ToolContext

        model_provider = create_model_provider_from_env(env)

        async def _emit_trace(
            kind: ObservationEventKind,
            *,
            session_id: str,
            node_key: str,
            execution_id: str,
            trace_id: str,
            detail: dict[str, Any] | None = None,
            level: str = "info",
            actor: str | None = None,
        ) -> None:
            # Acceptance run is self-contained: no observer wiring needed to
            # produce the report; the real multi-member turns still run.
            return None

        async def _execute_tool_round(
            *,
            session_id: str,
            node_key: str,
            execution_id: str,
            trace_id: str,
            tool_calls: tuple[ToolCall, ...],
            round_no: int,
            tools: list[ToolSpec],
            agent_role: str,
            tool_context: ToolContext,
            origin: dict[str, Any] | None,
            successful_results_by_fingerprint: dict[str, tuple[str, str]],
        ) -> list[ToolResultBlock]:
            # The acceptance team task is tool-free; the collaboration machinery
            # only reaches here when a member emits tool calls (it does not here).
            raise RuntimeError("live-MAS acceptance team is not expected to call tools")

        executor = CollaborationExecutor(
            subagent_spawner=SubagentSpawner.from_runtime(
                ProviderExecutionHarnessRuntime(model_provider)
            ),
            model_hint=ModelHint.BALANCED,
            max_tool_rounds=1,
            emit_trace=_emit_trace,
            execute_tool_round=_execute_tool_round,
            tool_context_for=lambda node_key, role: ToolContext(
                node_key=node_key, role=role
            ),
        )
        team = TeamSpec(
            id="live-mas-acceptance-team",
            members=(
                AgentSpec(id="drafter", role=task.role),
                AgentSpec(id="reviewer", role="reviewer"),
            ),
            protocol=CollaborationProtocol(
                topology=CollaborationTopology.PIPELINE
            ),
        )
        request = CollaborationExecutionRequest(
            session_id="live-mas-acceptance",
            node_key="collaborate",
            execution_id="exec",
            trace_id="trace",
            messages=[
                _user_message(task.prompt),
            ],
            system=task.task_description,
            tools=None,
            agent_role=task.role,
            node_config={"mas_team": team.model_dump(mode="json")},
        )
        try:
            result = await executor.invoke_team(request)
        finally:
            close = getattr(model_provider, "close", None)
            if callable(close):
                await close()

        return _aggregate_team_result(result)

    return _invoke


def _user_message(prompt: str):  # type: ignore[no-untyped-def]
    from hydramind.harness import Message, MessageRole

    return Message(role=MessageRole.USER, content=prompt)


def _aggregate_team_result(result: object) -> tuple[str, str, int, int]:
    """Reduce a team InvocationResult to (model_id, content, in_tok, out_tok).

    Content is derived from ALL member turns (joined, attributed) so the report
    reflects multi-agent collaboration, not a single member. Usage is the sum of
    every member's per-turn usage (the team-level Usage is intentionally zeroed).
    """

    raw = getattr(result, "raw", None) or {}
    team = raw.get("mas_team", {}) if isinstance(raw, dict) else {}
    member_results = team.get("results", []) if isinstance(team, dict) else []

    parts: list[str] = []
    input_tokens = 0
    output_tokens = 0
    model_id = getattr(result, "model_id", None) or "team"
    for member in member_results:
        if not isinstance(member, dict):
            continue
        agent_id = str(member.get("agent_id", "?"))
        content = str(member.get("content", ""))
        if content.strip():
            parts.append(f"[{agent_id}] {content}")
        usage = member.get("usage", {})
        if isinstance(usage, dict):
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
        member_model = member.get("model_id")
        if isinstance(member_model, str) and member_model and model_id == "team":
            model_id = member_model

    aggregated = "\n".join(parts) if parts else str(getattr(result, "content", ""))
    return (model_id, aggregated, input_tokens, output_tokens)


__all__ = [
    "PROVIDER_CREDENTIAL_KEYS",
    "LiveAcceptanceOutcome",
    "LiveAcceptanceTask",
    "ModelInvoker",
    "has_provider_credentials",
    "run_live_acceptance",
]
