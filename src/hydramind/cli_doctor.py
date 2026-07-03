"""Doctor command registration and diagnostics for the HydraMind CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hydramind.cli_doctor_goal_scenario import run_doctor_goal_scenario
from hydramind.cli_doctor_tools import (
    doctor_tool_args,
    run_doctor_tools,
)
from hydramind.cli_support import (
    env_present,
    split_values,
)
from hydramind.control import (
    ControlPlane,
    InMemorySessionStore,
    RuntimeSession,
    SessionService,
    WorkflowBlueprint,
    WorkflowNodeSpec,
)
from hydramind.harness import (
    Message,
    MessageRole,
    ModelProvider,
    StopReason,
    ToolCall,
    ToolSpec,
)
from hydramind.observability import (
    Emitter,
    JsonlObserver,
    ListObserver,
    ObservationEvent,
    ObservationEventKind,
)
from hydramind.orchestration import OrchestratorAgent
from hydramind.runtime import create_provider, load_env_file
from hydramind.testing import MockProvider, ScriptedTurn
from hydramind.tools import (
    ToolContext,
    ToolRegistry,
    build_default_tool_registry,
    default_external_tool_hosts,
)

ProviderFactory = Callable[[str], ModelProvider]


def register_doctor_commands(subparsers: Any) -> None:
    doctor = subparsers.add_parser("doctor", help="run provider/tool diagnostics")
    doctor_sub = doctor.add_subparsers(dest="doctor_command", required=True)
    env = doctor_sub.add_parser(
        "env",
        help="check required environment keys without printing values",
    )
    env.add_argument("--env-file", default=".env", help="dotenv file to load")
    env.add_argument(
        "--profile",
        choices=("all", "providers", "tools"),
        default="all",
        help="environment profile to check",
    )
    env.add_argument(
        "--include-missing-template",
        action="store_true",
        help="include empty KEY= template lines for missing variables",
    )
    providers = doctor_sub.add_parser(
        "providers",
        help="smoke test configured LLM providers",
    )
    providers.add_argument("--env-file", default=".env", help="dotenv file to load")
    providers.add_argument("--provider", default="env", help="provider override")
    providers.add_argument(
        "--roles",
        action="append",
        default=[],
        help="role or comma-separated roles to test; default planner",
    )
    providers.add_argument(
        "--prompt",
        default="Reply with OK.",
        help="small smoke prompt",
    )
    providers.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="maximum output tokens for each provider smoke request",
    )
    providers.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="override provider HTTP timeout for diagnostics",
    )
    tools = doctor_sub.add_parser(
        "tools",
        help="inspect and execute registered tools",
    )
    tools.add_argument("--env-file", default=".env", help="dotenv file to load")
    tools.add_argument(
        "--live-tools",
        action="store_true",
        help="allow live tool APIs",
    )
    tools.add_argument(
        "--tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names to execute",
    )
    tools.add_argument(
        "--artifact-root",
        default="artifacts/doctor",
        help="artifact root",
    )
    tool_loop = doctor_sub.add_parser(
        "tool-loop",
        help="smoke test provider-driven tool-call loop with trace evidence",
    )
    tool_loop.add_argument("--env-file", default=".env", help="dotenv file to load")
    tool_loop.add_argument("--provider", default="env", help="provider override")
    tool_loop.add_argument("--tool", default="time.now", help="single tool to expose")
    tool_loop.add_argument(
        "--artifact-root",
        default="artifacts/doctor-tool-loop",
        help="artifact root",
    )
    tool_loop.add_argument("--trace-path", default=None, help="JSONL trace path")
    tool_loop.add_argument(
        "--live-tools",
        action="store_true",
        help="allow live tool APIs",
    )
    tool_loop.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="overall smoke timeout in seconds",
    )
    tool_loop.add_argument(
        "--prompt",
        default=(
            "Call the exposed tool exactly once, wait for its result, then reply "
            "with a compact JSON object."
        ),
        help="instruction sent to the provider",
    )
    goal_scenario = doctor_sub.add_parser(
        "goal-scenario",
        help="run a goal-driven scenario and validate required tool evidence",
    )
    goal_scenario.add_argument(
        "--env-file",
        default=".env",
        help="dotenv file to load",
    )
    goal_scenario.add_argument("--provider", default="env", help="provider override")
    goal_scenario.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "model"),
        help="goal planner implementation",
    )
    goal_scenario.add_argument(
        "--objective",
        default=None,
        help="scenario objective; defaults to a required-tool exercise",
    )
    goal_scenario.add_argument(
        "--tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names available to the goal",
    )
    goal_scenario.add_argument(
        "--required-tool",
        action="append",
        default=[],
        help="tool name or comma-separated tool names that must succeed",
    )
    goal_scenario.add_argument(
        "--artifact-root",
        default="artifacts/doctor-goal-scenario",
        help="artifact root",
    )
    goal_scenario.add_argument(
        "--trace-path",
        default=None,
        help="JSONL trace path",
    )
    goal_scenario.add_argument(
        "--live-tools",
        action="store_true",
        help="allow live tool APIs",
    )
    goal_scenario.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="overall scenario timeout in seconds",
    )


def run_doctor(
    args: argparse.Namespace,
    *,
    provider_factory: ProviderFactory = create_provider,
) -> int:
    if args.doctor_command == "env":
        return _doctor_env(args)
    if args.doctor_command == "providers":
        return asyncio.run(_doctor_providers(args, provider_factory))
    if args.doctor_command == "tools":
        return asyncio.run(run_doctor_tools(args))
    if args.doctor_command == "tool-loop":
        return asyncio.run(_doctor_tool_loop(args, provider_factory))
    if args.doctor_command == "goal-scenario":
        return asyncio.run(run_doctor_goal_scenario(args))
    return 2


def _doctor_env(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    tool_registry = build_default_tool_registry(context=ToolContext(dry_run=True))
    tool_keys = sorted(
        {
            key
            for keys in tool_registry.env_requirements().values()
            for key in keys
        }
    )
    profiles: dict[str, list[str]] = {
        "providers": ["DEEPSEEK_API_KEY", "KIMI_API_KEY", "GLM_API_KEY"],
        "tools": tool_keys,
    }
    selected = (
        profiles if args.profile == "all" else {args.profile: profiles[args.profile]}
    )
    groups: dict[str, dict[str, Any]] = {}
    missing_template: list[str] = []
    for profile, keys in selected.items():
        checks = [{"key": key, "present": env_present(key)} for key in keys]
        missing_template.extend(
            f"{item['key']}=" for item in checks if not item["present"]
        )
        groups[profile] = {
            "ok": all(item["present"] for item in checks),
            "keys": checks,
        }
    payload: dict[str, Any] = {
        "env_file": str(args.env_file),
        "ok": all(group["ok"] for group in groups.values()),
        "profiles": groups,
    }
    if args.include_missing_template:
        payload["missing_template"] = missing_template
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if payload["ok"] else 1


async def _doctor_providers(
    args: argparse.Namespace,
    provider_factory: ProviderFactory,
) -> int:
    load_env_file(args.env_file)
    if args.timeout_seconds is not None:
        for key in (
            "DEEPSEEK_TIMEOUT_SECONDS",
            "KIMI_TIMEOUT_SECONDS",
            "GLM_TIMEOUT_SECONDS",
        ):
            os.environ[key] = str(args.timeout_seconds)
    provider = provider_factory(args.provider)
    roles = split_values(args.roles) or ["planner"]
    results: list[dict[str, Any]] = []
    for role in roles:
        try:
            invocation = provider.complete(
                messages=[Message(role=MessageRole.USER, content=args.prompt)],
                role=role,
                max_tokens=args.max_tokens,
            )
            result = (
                await asyncio.wait_for(invocation, timeout=args.timeout_seconds)
                if args.timeout_seconds is not None
                else await invocation
            )
            results.append(
                {
                    "role": role,
                    "ok": True,
                    "provider": provider.name,
                    "model_id": result.model_id,
                    "stop_reason": result.stop_reason.value,
                    "content_preview": result.content[:80],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "role": role,
                    "ok": False,
                    "provider": provider.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    close = getattr(provider, "close", None)
    if callable(close):
        await close()
    print(json.dumps({"providers": results}, ensure_ascii=False, default=str))
    return 0 if all(item["ok"] for item in results) else 1


async def _doctor_tool_loop(
    args: argparse.Namespace,
    provider_factory: ProviderFactory,
) -> int:
    provider = None
    trace_path = (
        Path(args.trace_path)
        if args.trace_path
        else Path(args.artifact_root) / "tool-loop-trace.jsonl"
    )
    try:
        load_env_file(args.env_file)
        artifact_root = Path(args.artifact_root)
        if trace_path.exists():
            trace_path.unlink()
        registry = build_default_tool_registry(
            context=ToolContext(
                artifact_root=artifact_root,
                dry_run=not bool(args.live_tools),
                network_access=bool(args.live_tools),
                allowed_network_hosts=default_external_tool_hosts(dict(os.environ)),
                tool_timeout_seconds=120.0,
            )
        )
        tool_name = str(args.tool).strip()
        if not tool_name or tool_name not in registry.names():
            payload = {
                "ok": False,
                "reason": "unknown_tool",
                "tool": tool_name,
                "known_tools": registry.names(),
                "trace_path": str(trace_path),
            }
            print(json.dumps(payload, ensure_ascii=False, default=str))
            return 1
        provider = _create_doctor_tool_loop_provider(
            args.provider,
            tool_name,
            provider_factory,
        )
        observer = ListObserver()
        emitter = Emitter([JsonlObserver(trace_path), observer])
        service = SessionService(InMemorySessionStore(), emitter=emitter)
        control = ControlPlane(service)
        agent = OrchestratorAgent(
            provider=provider,
            control=control,
            workflow=WorkflowBlueprint(
                name="doctor_tool_loop",
                nodes=(
                    WorkflowNodeSpec(
                        key="tool_loop",
                        role="executor",
                        description=f"Call {tool_name} and finalize.",
                    ),
                ),
            ),
            tool_provider=_SelectedToolProvider(registry, (tool_name,)),
            tool_runner=registry,
            emitter=emitter,
        )
        run = _run_doctor_tool_loop(
            agent,
            service,
            instruction=str(args.prompt),
            tool_name=tool_name,
        )
        session = (
            await asyncio.wait_for(run, timeout=args.timeout_seconds)
            if args.timeout_seconds is not None
            else await run
        )
        evidence = _tool_loop_evidence(
            events=observer.events,
            tool_name=tool_name,
            session_status=session.status.value,
            trace_path=trace_path,
            provider_name=provider.name,
        )
        print(json.dumps(evidence, ensure_ascii=False, default=str))
        return 0 if evidence["ok"] else 1
    except Exception as exc:
        payload = {
            "ok": False,
            "reason": "exception",
            "provider": getattr(provider, "name", str(args.provider)),
            "tool": str(args.tool),
            "trace_path": str(trace_path),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 1
    finally:
        if provider is not None:
            close = getattr(provider, "close", None)
            if callable(close):
                await close()


class _SelectedToolProvider:
    def __init__(self, registry: ToolRegistry, names: tuple[str, ...]) -> None:
        self._registry = registry
        self._names = names

    def tools_for(self, node_key: str) -> list[ToolSpec]:
        del node_key
        return self._registry.specs_for(self._names)


def _create_doctor_tool_loop_provider(
    provider_name: str,
    tool_name: str,
    provider_factory: ProviderFactory,
) -> ModelProvider:
    normalized = provider_name.lower().replace("-", "_")
    if normalized == "mock":
        return MockProvider(
            scripted=[
                ScriptedTurn(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id=f"doctor-{tool_name}",
                            name=tool_name,
                            arguments=doctor_tool_args(tool_name),
                        ),
                    ),
                    stop_reason=StopReason.TOOL_USE,
                ),
                ScriptedTurn(content='{"doctor_tool_loop": true}'),
            ]
        )
    return provider_factory(provider_name)


async def _run_doctor_tool_loop(
    agent: OrchestratorAgent,
    service: SessionService,
    *,
    instruction: str,
    tool_name: str,
) -> RuntimeSession:
    session = await agent.start_session(
        input_payload={"instruction": instruction, "required_tool": tool_name}
    )
    await agent.run_session(session.id)
    return await service.get_session(session.id)


def _tool_loop_evidence(
    *,
    events: list[ObservationEvent],
    tool_name: str,
    session_status: str,
    trace_path: Path,
    provider_name: str,
) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.kind.value] = event_counts.get(event.kind.value, 0) + 1
    tool_started = [
        event
        for event in events
        if event.kind is ObservationEventKind.TOOL_CALL_STARTED
        and event.detail.get("tool_name") == tool_name
    ]
    tool_completed = [
        event
        for event in events
        if event.kind is ObservationEventKind.TOOL_CALL_COMPLETED
    ]
    model_completed = [
        event
        for event in events
        if event.kind is ObservationEventKind.MODEL_INVOKE_COMPLETED
    ]

    reason = "passed"
    if session_status != "completed":
        reason = "session_not_completed"
    elif not trace_path.exists():
        reason = "trace_missing"
    elif not tool_started:
        reason = "no_tool_call"
    elif not tool_completed:
        reason = "tool_result_missing"
    elif len(model_completed) < 2:
        reason = "final_model_missing"
    return {
        "ok": reason == "passed",
        "reason": reason,
        "provider": provider_name,
        "tool": tool_name,
        "session_status": session_status,
        "trace_path": str(trace_path),
        "event_counts": dict(sorted(event_counts.items())),
        "model_invoke_completed": len(model_completed),
        "tool_call_started": len(tool_started),
        "tool_call_completed": len(tool_completed),
    }
