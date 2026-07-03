"""Provider/model routing for OpenAI-compatible harness backends.

HydraMind keeps vendor selection below the orchestration layer. The
orchestrator supplies a logical role (or node role); this router resolves it to
a provider profile and model using environment-driven policy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RouteRole(StrEnum):
    DEFAULT = "default"
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    COMPACTOR = "compactor"


class ProviderProfile(BaseModel):
    """One OpenAI-compatible provider endpoint."""

    model_config = ConfigDict(frozen=True)

    name: str
    api_key_env: str
    base_url: str
    default_model: str
    timeout_seconds: float = 120.0
    max_context_tokens: int = 128_000
    metadata: dict[str, Any] = Field(default_factory=dict)

    def api_key_from(self, env: Mapping[str, str] | None = None) -> str | None:
        source = os.environ if env is None else env
        value = source.get(self.api_key_env)
        return value.strip() if isinstance(value, str) and value.strip() else None


class RoleRoute(BaseModel):
    """Logical role -> provider/model route."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str | None = None
    thinking: bool = False
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ResolvedRoute(BaseModel):
    """A route with provider defaults applied."""

    model_config = ConfigDict(frozen=True)

    role: RouteRole
    provider: ProviderProfile
    model: str
    api_key: str | None
    thinking: bool = False
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ModelRouter:
    """Resolve a logical agent role into provider/model configuration."""

    def __init__(
        self,
        *,
        providers: Mapping[str, ProviderProfile],
        routes: Mapping[str | RouteRole, RoleRoute],
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._providers = {k.lower(): v for k, v in providers.items()}
        self._routes = {self._normalize_role(k).value: v for k, v in routes.items()}
        self._env = os.environ if env is None else env
        if RouteRole.DEFAULT.value not in self._routes:
            raise ValueError("ModelRouter requires a default route")

    @property
    def providers(self) -> dict[str, ProviderProfile]:
        return dict(self._providers)

    @property
    def routes(self) -> dict[str, RoleRoute]:
        return dict(self._routes)

    def resolve(self, role: str | RouteRole | None = None) -> ResolvedRoute:
        normalized = self._normalize_role(role)
        route = self._routes.get(normalized.value)
        if route is None:
            route = self._routes[RouteRole.DEFAULT.value]
            normalized = RouteRole.DEFAULT
        provider = self._providers.get(route.provider.lower())
        if provider is None:
            raise ValueError(f"unknown provider {route.provider!r} for role {normalized.value!r}")
        model = route.model or provider.default_model
        return ResolvedRoute(
            role=normalized,
            provider=provider,
            model=model,
            api_key=provider.api_key_from(self._env),
            thinking=route.thinking,
            reasoning_effort=route.reasoning_effort,
            temperature=_normalize_temperature(
                provider=provider.name,
                model=model,
                temperature=route.temperature,
            ),
            max_tokens=route.max_tokens,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ModelRouter:
        source = os.environ if env is None else env
        providers = {
            "deepseek": ProviderProfile(
                name="deepseek",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=_env(source, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                default_model=_env(source, "DEEPSEEK_MODEL", "deepseek-v4-pro"),
                timeout_seconds=_env_float(source, "DEEPSEEK_TIMEOUT_SECONDS", 120.0),
                max_context_tokens=_env_int(source, "DEEPSEEK_CONTEXT_TOKENS", 1_000_000),
            ),
            "kimi": ProviderProfile(
                name="kimi",
                api_key_env="KIMI_API_KEY",
                base_url=_env(source, "KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
                default_model=_env(source, "KIMI_MODEL", "kimi-k2.6"),
                timeout_seconds=_env_float(source, "KIMI_TIMEOUT_SECONDS", 120.0),
                max_context_tokens=_env_int(source, "KIMI_CONTEXT_TOKENS", 262_000),
            ),
            "glm": ProviderProfile(
                name="glm",
                api_key_env="GLM_API_KEY",
                base_url=_env(source, "GLM_BASE_URL", "https://api.z.ai/api/paas/v4"),
                default_model=_env(source, "GLM_MODEL", "glm-5.1"),
                timeout_seconds=_env_float(source, "GLM_TIMEOUT_SECONDS", 120.0),
                max_context_tokens=_env_int(source, "GLM_CONTEXT_TOKENS", 200_000),
            ),
        }
        default_provider = _env(source, "HYDRAMIND_DEFAULT_PROVIDER", "deepseek")
        default_model = _env_optional(source, "HYDRAMIND_DEFAULT_MODEL")
        routes: dict[str | RouteRole, RoleRoute] = {
            RouteRole.DEFAULT: RoleRoute(
                provider=default_provider,
                model=default_model,
                thinking=_env_bool(source, "HYDRAMIND_DEFAULT_THINKING", False),
                reasoning_effort=_env_optional(source, "HYDRAMIND_DEFAULT_REASONING_EFFORT"),
            ),
            RouteRole.ORCHESTRATOR: _role_route(
                source,
                "ORCHESTRATOR",
                default_provider="deepseek",
                default_model=providers["deepseek"].default_model,
            ),
            RouteRole.PLANNER: _role_route(
                source,
                "PLANNER",
                default_provider="kimi",
                default_model=providers["kimi"].default_model,
            ),
            RouteRole.EXECUTOR: _role_route(
                source,
                "EXECUTOR",
                default_provider="glm",
                default_model=providers["glm"].default_model,
            ),
            RouteRole.REVIEWER: _role_route(
                source,
                "REVIEWER",
                default_provider="deepseek",
                default_model=providers["deepseek"].default_model,
            ),
            RouteRole.COMPACTOR: _role_route(
                source,
                "COMPACTOR",
                default_provider="kimi",
                default_model=providers["kimi"].default_model,
            ),
        }
        return cls(providers=providers, routes=routes, env=source)

    @staticmethod
    def _normalize_role(role: str | RouteRole | None) -> RouteRole:
        if isinstance(role, RouteRole):
            return role
        raw = (role or "").strip().lower().replace("-", "_")
        if not raw:
            return RouteRole.DEFAULT
        if raw in {"default"}:
            return RouteRole.DEFAULT
        if "orchestr" in raw:
            return RouteRole.ORCHESTRATOR
        if raw in {"plan", "planner"} or "plan" in raw or "concept" in raw:
            return RouteRole.PLANNER
        if raw in {"execute", "executor", "act", "worker"} or any(
            part in raw for part in ("write", "script", "scene", "audio", "video", "voice")
        ):
            return RouteRole.EXECUTOR
        if raw in {"review", "reviewer", "gate", "quality", "verifier"} or any(
            part in raw for part in ("review", "gate", "quality", "check", "verif")
        ):
            return RouteRole.REVIEWER
        if raw in {"compact", "compactor", "memory_compactor"}:
            return RouteRole.COMPACTOR
        return RouteRole.DEFAULT


def _role_route(
    env: Mapping[str, str],
    role_name: str,
    *,
    default_provider: str,
    default_model: str,
    default_thinking: bool = False,
    default_reasoning_effort: str | None = None,
) -> RoleRoute:
    prefix = f"HYDRAMIND_{role_name}"
    return RoleRoute(
        provider=_env(env, f"{prefix}_PROVIDER", default_provider),
        model=_env(env, f"{prefix}_MODEL", default_model),
        thinking=_env_bool(env, f"{prefix}_THINKING", default_thinking),
        reasoning_effort=_env_optional(env, f"{prefix}_REASONING_EFFORT")
        or default_reasoning_effort,
        temperature=_env_float_optional(env, f"{prefix}_TEMPERATURE"),
        max_tokens=_env_int_optional(env, f"{prefix}_MAX_TOKENS"),
    )


def _env(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = env.get(key)
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _env_optional(env, key)
    if value is None:
        return default
    return float(value)


def _env_float_optional(env: Mapping[str, str], key: str) -> float | None:
    value = _env_optional(env, key)
    return float(value) if value is not None else None


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _env_optional(env, key)
    if value is None:
        return default
    return int(value)


def _env_int_optional(env: Mapping[str, str], key: str) -> int | None:
    value = _env_optional(env, key)
    return int(value) if value is not None else None


def _normalize_temperature(
    *,
    provider: str,
    model: str,
    temperature: float | None,
) -> float | None:
    if (
        temperature is not None
        and provider.lower() == "kimi"
        and model.lower().startswith("kimi-k2")
    ):
        return 1.0
    return temperature
