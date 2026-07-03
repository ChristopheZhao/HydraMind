"""Deterministic verifier runners for task contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hydramind.control import (
    AgentReport,
    FeedbackRecord,
    RuntimeSession,
    VerifierResult,
)
from hydramind.harness import Message, MessageRole, ModelHint, ModelProvider
from hydramind.orchestration.builtin_prompts import (
    SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM,
    SEMANTIC_VERIFIER_SYSTEM,
    render_semantic_verifier_json_repair_prompt,
    render_semantic_verifier_prompt,
)
from hydramind.orchestration.verification_content import (
    IMAGE_REF_RE as _IMAGE_REF_RE,
)
from hydramind.orchestration.verification_content import (
    artifact_feedback as _artifact_feedback,
)
from hydramind.orchestration.verification_content import (
    check_local_assets as _check_local_assets,
)
from hydramind.orchestration.verification_content import (
    contract_from_config as _contract_from_config,
)
from hydramind.orchestration.verification_content import (
    extract_image_targets as _extract_image_targets,
)
from hydramind.orchestration.verification_content import (
    locate_existing_artifact as _locate_existing_artifact,
)
from hydramind.orchestration.verification_content import (
    verify_expected_artifacts as _verify_expected_artifacts,
)
from hydramind.orchestration.verification_semantic import (
    has_failed_deterministic_content_check as _has_failed_deterministic_content_check,
)
from hydramind.orchestration.verification_semantic import (
    semantic_degraded_report as _semantic_degraded_report,
)
from hydramind.orchestration.verification_semantic import (
    semantic_json_payload as _semantic_json_payload,
)
from hydramind.orchestration.verification_semantic import (
    semantic_results_from_payload as _semantic_results_from_payload,
)
from hydramind.orchestration.verification_semantic import (
    truncate_semantic_response as _truncate_semantic_response,
)


class VerifierRunner(Protocol):
    """Produces typed verifier evidence for an agent report."""

    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, Any],
        report: AgentReport,
    ) -> AgentReport: ...


@dataclass(frozen=True)
class TaskContractVerifierRunner:
    """Run deterministic checks declared by ``TaskContract``."""

    artifact_root: Path

    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, Any],
        report: AgentReport,
    ) -> AgentReport:
        del session
        contract = _contract_from_config(node_config)
        if contract is None:
            return report
        results: list[VerifierResult] = []
        feedback: list[FeedbackRecord] = []
        if contract.expected_artifacts:
            result = _verify_expected_artifacts(
                self.artifact_root,
                contract,
            )
            results.append(result)
            if not result.passed:
                feedback.append(_artifact_feedback(report.node_key, result))
        if not results and not feedback:
            return report
        return report.model_copy(
            update={
                "verifier_results": (*report.verifier_results, *results),
                "feedback": (*report.feedback, *feedback),
            }
        )


@dataclass(frozen=True)
class ArtifactContainmentVerifierRunner:
    """Boundary safety verifier: reject local image-asset paths that escape
    ``artifact_root`` (ADR-0008 artifact-root containment).

    This is the surviving safety carve-out of the deleted (S97) rule-based
    content-quality verifier. It runs ONLY the deterministic
    containment check — it does NOT enforce any quality threshold (length,
    sections, reference-url/image counts). It scans the produced artifact for
    local (non-URL) markdown image references and emits a typed
    ``artifact.local_assets_contained`` result that fails when any referenced
    local asset path resolves outside ``artifact_root`` (invalid) or is
    missing. ``GoalArtifactQualityContract.local_asset_refs_under_artifact_root``
    gates whether the containment check applies.
    """

    artifact_root: Path

    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, Any],
        report: AgentReport,
    ) -> AgentReport:
        del session
        contract = _contract_from_config(node_config)
        if contract is None or contract.quality_contract is None:
            return report
        quality = contract.quality_contract
        if not quality.local_asset_refs_under_artifact_root:
            return report
        artifact_ref, artifact_path = _locate_existing_artifact(
            self.artifact_root, contract.expected_artifacts
        )
        if artifact_path is None:
            return report
        text = artifact_path.read_text(encoding="utf-8", errors="replace")
        if not _IMAGE_REF_RE.search(text):
            return report
        image_targets = _extract_image_targets(text)
        if not image_targets:
            return report
        new_feedback: list[FeedbackRecord] = []
        result = _check_local_assets(
            image_targets,
            self.artifact_root,
            artifact_ref,
            new_feedback,
            report.node_key,
        )
        return report.model_copy(
            update={
                "verifier_results": (*report.verifier_results, result),
                "feedback": (*report.feedback, *new_feedback),
            }
        )


# Composite runs each runner in declared order, threading the evolving report.
@dataclass(frozen=True)
class CompositeVerifierRunner:
    """Run verifier runners in declared order, threading the report through each."""

    runners: tuple[VerifierRunner, ...]

    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, Any],
        report: AgentReport,
    ) -> AgentReport:
        current = report
        for runner in self.runners:
            current = await runner.verify(
                session=session, node_config=node_config, report=current
            )
        return current


@dataclass(frozen=True)
class SemanticArtifactVerifierRunner:
    """LLM-as-judge rubric runner that delegates to ``ModelProvider``.

    Opt-in: the runner is a no-op unless the task's ``quality_contract`` has
    an enabled ``semantic_rubric`` with at least one check, the expected
    artifact exists, and no deterministic content check has already failed
    (deterministic-first contract). On any harness error the runner emits a
    typed degraded result and never propagates the exception.
    """

    provider: ModelProvider
    artifact_root: Path
    role: str = "verifier"
    model_hint: ModelHint = ModelHint.BALANCED
    max_tokens: int | None = 1024
    json_repair_max_tokens: int | None = 512
    max_json_repairs: int = 1
    max_chars_per_artifact: int = 24_000

    async def verify(
        self,
        *,
        session: RuntimeSession,
        node_config: dict[str, Any],
        report: AgentReport,
    ) -> AgentReport:
        del session
        contract = _contract_from_config(node_config)
        if contract is None or contract.quality_contract is None:
            return report
        rubric = contract.quality_contract.semantic_rubric
        if rubric is None or not rubric.enabled or not rubric.checks:
            return report
        artifact_ref, artifact_path = _locate_existing_artifact(
            self.artifact_root, contract.expected_artifacts
        )
        if artifact_path is None:
            return report
        if _has_failed_deterministic_content_check(report):
            skipped_result = VerifierResult(
                name="artifact.semantic_rubric",
                passed=False,
                score=None,
                detail={
                    "skipped": True,
                    "reason": "deterministic_failed",
                    "artifact": artifact_ref or "",
                },
            )
            return report.model_copy(
                update={
                    "verifier_results": (
                        *report.verifier_results,
                        skipped_result,
                    ),
                }
            )
        text = artifact_path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > self.max_chars_per_artifact
        artifact_text = (
            text[: self.max_chars_per_artifact] + "\n… [truncated]"
            if truncated
            else text
        )
        check_payloads = [
            {
                "name": check.name,
                "description": check.description,
                "min_score": check.min_score,
            }
            for check in rubric.checks
        ]
        prompt = render_semantic_verifier_prompt(
            artifact_ref=artifact_ref or "",
            artifact_text=artifact_text,
            checks=check_payloads,
        )
        try:
            invocation = await self.provider.complete(
                [Message(role=MessageRole.USER, content=prompt)],
                system=SEMANTIC_VERIFIER_SYSTEM,
                role=self.role,
                model_hint=self.model_hint,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:  # graceful degradation — never propagate
            return _semantic_degraded_report(report, exc)
        try:
            payload = _semantic_json_payload(invocation.content)
        except ValueError as first_error:
            if self.max_json_repairs <= 0:
                return _semantic_degraded_report(report, first_error)
            try:
                payload = await self._repair_semantic_json_payload(
                    invalid_response=invocation.content,
                    validation_error=str(first_error),
                    checks=check_payloads,
                )
            except Exception as exc:
                return _semantic_degraded_report(report, exc)
        new_results, new_feedback = _semantic_results_from_payload(
            payload=payload,
            checks=rubric.checks,
            node_key=report.node_key,
            artifact_ref=artifact_ref or "",
        )
        return report.model_copy(
            update={
                "verifier_results": (*report.verifier_results, *new_results),
                "feedback": (*report.feedback, *new_feedback),
            }
        )

    async def _repair_semantic_json_payload(
        self,
        *,
        invalid_response: str,
        validation_error: str,
        checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = render_semantic_verifier_json_repair_prompt(
            invalid_response=_truncate_semantic_response(invalid_response),
            validation_error=validation_error,
            checks=checks,
        )
        repair = await self.provider.complete(
            [Message(role=MessageRole.USER, content=prompt)],
            system=SEMANTIC_VERIFIER_JSON_REPAIR_SYSTEM,
            role=self.role,
            model_hint=self.model_hint,
            max_tokens=self.json_repair_max_tokens,
        )
        try:
            return _semantic_json_payload(repair.content)
        except ValueError as repair_error:
            raise ValueError(
                f"{validation_error}; semantic JSON repair failed: {repair_error}"
            ) from repair_error
