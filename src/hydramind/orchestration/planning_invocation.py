"""Model planner invocation and JSON repair helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from hydramind.harness import InvocationResult, Message, MessageRole, ModelHint
from hydramind.harness.provider import ModelProvider
from hydramind.orchestration import planning_diagnostics as _diagnostics
from hydramind.orchestration.planning_payloads import (
    strict_json_object as _strict_json_object,
)

RepairPromptBuilder = Callable[[str, str, str], str]


class PlannerJsonInvoker:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        system: str,
        role: str,
        model_hint: ModelHint,
        max_tokens: int | None,
        max_json_repairs: int,
        max_invoke_retries: int,
        retry_backoff_seconds: float,
    ) -> None:
        self._provider = provider
        self._system = system
        self._role = role
        self._model_hint = model_hint
        self._max_tokens = max_tokens
        self._max_json_repairs = max_json_repairs
        self._max_invoke_retries = max_invoke_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def invoke_planner_json(
        self,
        prompt: str,
        *,
        repair_prompt: RepairPromptBuilder,
        diagnostics: dict[str, Any],
        phase: str,
    ) -> dict[str, Any]:
        result = await self.invoke_provider(
            prompt,
            diagnostics=diagnostics,
            phase=phase,
        )
        try:
            return _strict_json_object(result.content)
        except ValueError as first_error:
            if self._max_json_repairs <= 0:
                raise
            return await self.repair_planner_json(
                prompt,
                result.content,
                reason=str(first_error),
                repair_prompt=repair_prompt,
                diagnostics=diagnostics,
                phase="json_repair",
            )

    async def repair_planner_json(
        self,
        prompt: str,
        invalid_response: str,
        *,
        reason: str,
        repair_prompt: RepairPromptBuilder,
        diagnostics: dict[str, Any],
        phase: str,
    ) -> dict[str, Any]:
        if self._max_json_repairs <= 0:
            raise ValueError(reason)
        diagnostics["repair_count"] = int(diagnostics["repair_count"]) + 1
        repair = await self.invoke_provider(
            repair_prompt(prompt, invalid_response, reason),
            diagnostics=diagnostics,
            phase=phase,
        )
        try:
            return _strict_json_object(repair.content)
        except ValueError as repair_error:
            raise ValueError(
                f"{reason}; planner JSON repair failed: {repair_error}"
            ) from repair_error

    async def invoke_provider(
        self,
        prompt: str,
        *,
        diagnostics: dict[str, Any],
        phase: str,
    ) -> InvocationResult:
        attempts = self._max_invoke_retries + 1
        for attempt in range(attempts):
            diagnostics["invoke_attempts"] = int(diagnostics["invoke_attempts"]) + 1
            _diagnostics.append_diagnostic_phase(diagnostics, phase)
            try:
                return await self._provider.complete(
                    [Message(role=MessageRole.USER, content=prompt)],
                    system=self._system,
                    role=self._role,
                    model_hint=self._model_hint,
                    max_tokens=self._max_tokens,
                )
            except Exception:
                if attempt >= attempts - 1:
                    diagnostics["status"] = "failed"
                    raise
                diagnostics["retry_count"] = int(diagnostics["retry_count"]) + 1
                if self._retry_backoff_seconds:
                    await asyncio.sleep(self._retry_backoff_seconds)
        raise RuntimeError("unreachable planner retry state")
