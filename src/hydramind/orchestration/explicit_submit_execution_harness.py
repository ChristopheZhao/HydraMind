"""Explicit-submit ``ExecutionHarness`` implementation for harness swap proof.

The swap-proof variant differs from the default harness only in two harness-level
control knobs — loop granularity (one tool action per turn, not batch tool-drain)
and termination (explicit ``{"done": true, "submit": ...}`` instead of stopping on
an empty tool-call turn). It is named after those壳层 variables, NOT after the
agent's reasoning paradigm: a single-action act/observe loop reads as "ReAct-style"
but ReAct is an agent/scaffold-level pattern any harness can host (config, not a
harness identity), so naming the壳 after it would mis-attribute a portable
invariant to the replaceable seam. See ADR-0012 rename note (2026-06-26).
"""

from __future__ import annotations

import json

from hydramind.control.models import AgentReport
from hydramind.harness.base import InvocationResult, Message, MessageRole, ToolSpec
from hydramind.orchestration.agent_context import AgentPromptContextBuilder
from hydramind.orchestration.agent_execution import AgentExecutionRuntime
from hydramind.orchestration.agent_invocation import (
    ReportBuilder,
    ToolProvider,
    default_report_builder,
)
from hydramind.orchestration.agent_tools import AgentToolLoop, subagent_tool_origin
from hydramind.orchestration.collaboration import (
    CollaborationExecutionRequest,
    CollaborationExecutor,
)
from hydramind.orchestration.execution_harness import (
    ExecutionEpisodeOutcome,
    ExecutionEpisodeRequest,
    FailureCategory,
    ModelInvocationEvidence,
    ProposedStateTransition,
    ProposedTransitionKind,
    RecoverySignal,
    RecoverySignalKind,
    ToolCallEvidence,
)
from hydramind.orchestration.planning_contracts import (
    NodeExecutionMode,
    resolve_node_execution_mode,
)
from hydramind.orchestration.verification import VerifierRunner


class ExplicitSubmitExecutionHarness:
    """Alternative harness: one tool action per turn plus explicit submit/done.

    Differs from the default harness in two harness-level control knobs only:
    (1) loop granularity — a single tool action per turn (act/observe), not a
    batch tool-drain; (2) termination — the loop does NOT stop when a model
    returns no tool calls. A direct/subagent episode terminates only when model
    content carries ``{"done": true, "submit": ...}``; otherwise the harness asks
    the model for another turn until its budget is exhausted. Team mode is
    delegated to the fixed native MAS scheduler and treats protocol completion as
    the explicit termination boundary. The single-action loop reads as
    "ReAct-style", but that is an agent/scaffold-level pattern (config any harness
    can host) — the harness is named after its壳层 variables, not the paradigm.
    """

    name = "ExplicitSubmitExecutionHarness"

    def __init__(
        self,
        *,
        execution: AgentExecutionRuntime,
        context_builder: AgentPromptContextBuilder,
        tool_provider: ToolProvider | None,
        tool_loop: AgentToolLoop,
        collaboration: CollaborationExecutor,
        report_builder: ReportBuilder | None,
        verifier_runner: VerifierRunner | None,
        max_tool_rounds: int,
    ) -> None:
        self._execution = execution
        self._context_builder = context_builder
        self._tools = tool_provider
        self._tool_loop = tool_loop
        self._collaboration = collaboration
        self._report_builder = report_builder or default_report_builder
        self._verifier_runner = verifier_runner
        self._max_tool_rounds = max_tool_rounds

    async def run_episode(
        self, request: ExecutionEpisodeRequest
    ) -> ExecutionEpisodeOutcome:
        mode = resolve_node_execution_mode(request.node_config)
        if mode is NodeExecutionMode.TEAM:
            return await self._run_team(request)
        return await self._run_submit_loop(request, mode=mode)

    async def _run_team(
        self, request: ExecutionEpisodeRequest
    ) -> ExecutionEpisodeOutcome:
        system, user = await self._context_builder.compose_messages(
            request.session,
            request.node_key,
            request.agent_role,
        )
        tools = self._tools.tools_for(request.node_key) if self._tools else []
        invocation = await self._collaboration.invoke_team(
            CollaborationExecutionRequest(
                session_id=request.session.id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                trace_id=request.trace_id,
                messages=[Message(role=MessageRole.USER, content=user)],
                system=system,
                tools=tools or None,
                agent_role=request.agent_role,
                node_config=request.node_config,
            ),
            # Swap reaches each MAS member: this harness drives every member turn
            # through the explicit-submit strategy, not the default batch drain (M1).
            member_strategy=self._collaboration.explicit_submit_member_strategy,
        )
        report = await self._build_report(
            request=request,
            invocation=invocation,
        )
        failure = _classify_report_failure(report)
        return ExecutionEpisodeOutcome(
            report=report,
            failure=failure,
            final_result=_final_result(report),
            model_invocations=(
                _model_invocation_evidence(invocation, round_no=0),
            ),
            verifier_evidence=tuple(report.verifier_results),
            trace_event_refs=(f"{request.trace_id}:team:{self.name}",),
            proposed_transitions=_proposed_transitions(report, failure),
            recovery_signals=_recovery_signals(failure),
        )

    async def _run_submit_loop(
        self,
        request: ExecutionEpisodeRequest,
        *,
        mode: NodeExecutionMode,
    ) -> ExecutionEpisodeOutcome:
        system, user = await self._context_builder.compose_messages(
            request.session,
            request.node_key,
            request.agent_role,
        )
        tools = self._tools.tools_for(request.node_key) if self._tools else []
        messages = [Message(role=MessageRole.USER, content=user)]
        max_turns = request.policy.constraints.max_turns or self._max_tool_rounds
        current = await self._invoke_first(
            request=request,
            mode=mode,
            messages=messages,
            system=system,
            tools=tools or None,
        )
        model_invocations = [_model_invocation_evidence(current, round_no=0)]
        tool_evidence: list[ToolCallEvidence] = []
        successful_results_by_fingerprint: dict[str, tuple[str, str]] = {}
        tool_origin = (
            subagent_tool_origin(current, request.agent_role)
            if mode is NodeExecutionMode.SUBAGENT
            else None
        )
        for turn_no in range(1, max_turns + 1):
            submitted = await self._submitted_outcome(
                request=request,
                current=current,
                model_invocations=tuple(model_invocations),
                tool_evidence=tuple(tool_evidence),
            )
            if submitted is not None:
                return submitted
            if not current.tool_calls:
                messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=current.content,
                        reasoning_content=current.reasoning_content,
                    )
                )
                current = await self._execution.invoke_model(
                    session_id=request.session.id,
                    node_key=request.node_key,
                    execution_id=request.execution_id,
                    trace_id=request.trace_id,
                    messages=messages,
                    system=system,
                    tools=tools or None,
                    role=request.agent_role,
                    round_no=turn_no,
                )
                model_invocations.append(
                    _model_invocation_evidence(current, round_no=turn_no)
                )
                continue
            if not tools:
                raise RuntimeError("explicit-submit tool action requires configured tools")
            action = (current.tool_calls[0],)
            messages.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=current.content,
                    tool_calls=action,
                    reasoning_content=current.reasoning_content,
                )
            )
            tool_results = await self._tool_loop.execute_tool_round(
                session_id=request.session.id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                trace_id=request.trace_id,
                tool_calls=action,
                round_no=turn_no,
                tools=tools,
                agent_role=request.agent_role,
                tool_context=self._tool_loop.tool_context_for(
                    request.node_key,
                    request.agent_role,
                ),
                origin=tool_origin if turn_no == 1 else None,
                successful_results_by_fingerprint=successful_results_by_fingerprint,
            )
            tool_evidence.extend(
                ToolCallEvidence(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    round_no=turn_no,
                    is_error=tool_results[index].is_error,
                )
                for index, call in enumerate(action)
            )
            messages.append(
                Message(role=MessageRole.TOOL, tool_results=tuple(tool_results))
            )
            current = await self._execution.invoke_model(
                session_id=request.session.id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                trace_id=request.trace_id,
                messages=messages,
                system=system,
                tools=tools,
                role=request.agent_role,
                round_no=turn_no,
            )
            model_invocations.append(
                _model_invocation_evidence(current, round_no=turn_no)
            )
        submitted = await self._submitted_outcome(
            request=request,
            current=current,
            model_invocations=tuple(model_invocations),
            tool_evidence=tuple(tool_evidence),
        )
        if submitted is not None:
            return submitted
        return _failure_outcome(
            request,
            failure=FailureCategory.TIMEOUT,
            detail=(
                f"explicit-submit harness exhausted max_turns={max_turns} "
                "before submit/done"
            ),
            model_invocations=tuple(model_invocations),
            tool_evidence=tuple(tool_evidence),
        )

    async def _invoke_first(
        self,
        *,
        request: ExecutionEpisodeRequest,
        mode: NodeExecutionMode,
        messages: list[Message],
        system: str,
        tools: list[ToolSpec] | None,
    ) -> InvocationResult:
        if mode is NodeExecutionMode.SUBAGENT:
            return await self._execution.invoke_subagent(
                session_id=request.session.id,
                node_key=request.node_key,
                execution_id=request.execution_id,
                trace_id=request.trace_id,
                messages=messages,
                system=system,
                tools=tools,
                role=request.agent_role,
            )
        return await self._execution.invoke_model(
            session_id=request.session.id,
            node_key=request.node_key,
            execution_id=request.execution_id,
            trace_id=request.trace_id,
            messages=messages,
            system=system,
            tools=tools,
            role=request.agent_role,
            round_no=0,
        )

    async def _submitted_outcome(
        self,
        *,
        request: ExecutionEpisodeRequest,
        current: InvocationResult,
        model_invocations: tuple[ModelInvocationEvidence, ...],
        tool_evidence: tuple[ToolCallEvidence, ...],
    ) -> ExecutionEpisodeOutcome | None:
        final_content = _explicit_submit(current.content)
        if final_content is None:
            return None
        final = current.model_copy(update={"content": final_content, "tool_calls": ()})
        report = await self._build_report(request=request, invocation=final)
        failure = _classify_report_failure(report)
        return ExecutionEpisodeOutcome(
            report=report,
            failure=failure,
            final_result=_final_result(report),
            model_invocations=model_invocations,
            tool_evidence=tool_evidence,
            verifier_evidence=tuple(report.verifier_results),
            trace_event_refs=(f"{request.trace_id}:submit:{self.name}",),
            proposed_transitions=_proposed_transitions(report, failure),
            recovery_signals=_recovery_signals(failure),
        )

    async def _build_report(
        self,
        *,
        request: ExecutionEpisodeRequest,
        invocation: InvocationResult,
    ) -> AgentReport:
        built = self._report_builder(request.node_key, request.agent_role, invocation)
        report = built if isinstance(built, AgentReport) else await built
        report = report.model_copy(
            update={
                "execution_id": request.execution_id,
                "trace_id": request.trace_id,
                "lease_token": (
                    request.resume.lease_token
                    if request.resume is not None
                    and request.resume.lease_token is not None
                    else request.lease_token
                ),
                "harness_id": self.name,
            }
        )
        if self._verifier_runner is not None:
            report = await self._verifier_runner.verify(
                session=request.session,
                node_config=request.node_config,
                report=report,
            )
            report = report.model_copy(update={"harness_id": self.name})
        return report


def _explicit_submit(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("done") is not True:
        return None
    if "submit" not in payload:
        return None
    submitted = payload["submit"]
    if isinstance(submitted, str):
        return submitted
    return json.dumps(submitted, ensure_ascii=False, sort_keys=True)


def _model_invocation_evidence(
    invocation: InvocationResult,
    *,
    round_no: int,
) -> ModelInvocationEvidence:
    return ModelInvocationEvidence(
        model_id=invocation.model_id,
        round_no=round_no,
        input_tokens=invocation.usage.input_tokens,
        output_tokens=invocation.usage.output_tokens,
        stop_reason=invocation.stop_reason.value,
    )


def _classify_report_failure(report: AgentReport) -> FailureCategory:
    if any(not result.passed for result in report.verifier_results):
        return FailureCategory.VERIFICATION_FAILED
    return FailureCategory.NONE


def _final_result(report: AgentReport) -> str | None:
    text = report.output.get("text")
    return text if isinstance(text, str) else None


def _proposed_transitions(
    report: AgentReport,
    failure: FailureCategory,
) -> tuple[ProposedStateTransition, ...]:
    kind = (
        ProposedTransitionKind.COMPLETE
        if failure is FailureCategory.NONE
        else ProposedTransitionKind.FAIL
    )
    return (
        ProposedStateTransition(
            node_key=report.node_key,
            kind=kind,
            reason="" if failure is FailureCategory.NONE else failure.value,
        ),
    )


def _recovery_signals(failure: FailureCategory) -> tuple[RecoverySignal, ...]:
    if failure is FailureCategory.NONE:
        return ()
    return (
        RecoverySignal(
            kind=RecoverySignalKind.NON_RETRYABLE,
            failure=failure,
            detail=failure.value,
        ),
    )


def _failure_outcome(
    request: ExecutionEpisodeRequest,
    *,
    failure: FailureCategory,
    detail: str,
    model_invocations: tuple[ModelInvocationEvidence, ...],
    tool_evidence: tuple[ToolCallEvidence, ...],
) -> ExecutionEpisodeOutcome:
    report = AgentReport(
        node_key=request.node_key,
        agent_id=request.agent_role,
        execution_id=request.execution_id,
        trace_id=request.trace_id,
        lease_token=request.lease_token,
        harness_id=ExplicitSubmitExecutionHarness.name,
        error=detail,
    )
    return ExecutionEpisodeOutcome(
        report=report,
        failure=failure,
        model_invocations=model_invocations,
        tool_evidence=tool_evidence,
        trace_event_refs=(f"{request.trace_id}:submit:{ExplicitSubmitExecutionHarness.name}",),
        proposed_transitions=_proposed_transitions(report, failure),
        recovery_signals=_recovery_signals(failure),
    )


__all__ = ["ExplicitSubmitExecutionHarness"]
