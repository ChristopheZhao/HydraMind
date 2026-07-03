"""ModelProvider / LLMProvider — the model-access contract (S1, ADR-0010).

A ``ModelProvider`` supplies model calls and nothing else: provider/model
identity, endpoint/transport, role/profile routing, context limits, usage/cost,
and response parsing. It is deliberately separate from the execution harness.

The provider contract MUST NOT expose ``spawn_subagent``, ``compact_context``,
interaction/recovery surfaces, or harness capability declarations. Those are
execution-harness concerns; see the corrected ``ExecutionHarness`` design in
``docs/architecture/95-execution-harness-correction.md`` §F2 / Phase 1.

ModelHint vs role-route precedence (the ONE rule, ADR-0010 / F10)
-----------------------------------------------------------------
Role-route is authoritative for provider + model selection:

    route.model (if the role route pins a concrete model)
      > provider.default_model

``ModelHint`` is a coarse profile *request* (fast / balanced / powerful). It is
honored ONLY where a provider defines a hint->model map AND the route does not
pin a model, e.g. the Claude SDK provider, which has no role routes. A
route-driven provider such as ``OpenAICompatibleProvider`` therefore uses the
route-resolved model regardless of ``ModelHint``. There is no second hidden
selection axis.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hydramind.harness.base import (
    InvocationResult,
    Message,
    ModelHint,
    ToolSpec,
)


class ModelProvider(ABC):
    """Model-access contract. NO harness surface (no subagent/compaction/caps).

    Implementations own provider/model identity, role/profile routing, transport,
    response parsing, and usage/cost. See module docstring for the single
    ModelHint-vs-route precedence rule.
    """

    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
        max_tokens: int | None = None,
    ) -> InvocationResult:
        """Run a single model completion turn.

        ``role`` selects the provider's role route (authoritative for
        provider+model). ``model_hint`` is honored only by providers without role
        routes that define a hint->model map (see module docstring).
        """

    @abstractmethod
    def resolve_model(
        self,
        role: str | None = None,
        model_hint: ModelHint = ModelHint.BALANCED,
    ) -> str:
        """Return the concrete model id this provider would call for ``role``.

        Route-pinned model wins over ``model_hint`` and over the provider default.
        """

    @abstractmethod
    def context_limit(self, role: str | None = None) -> int:
        """Return the max context tokens for the model selected for ``role``."""


# Public alias — both names denote the same model-access contract (ADR-0010).
LLMProvider = ModelProvider
