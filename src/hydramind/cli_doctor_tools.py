"""Tool diagnostics helpers for ``hydramind.cli_doctor``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hydramind.cli_support import env_present, split_values
from hydramind.harness import ToolCall
from hydramind.runtime import load_env_file
from hydramind.tools import (
    DEFAULT_DOUBAO_IMAGE_SIZE,
    ToolContext,
    build_default_tool_registry,
    default_external_tool_hosts,
)


async def run_doctor_tools(args: Any) -> int:
    load_env_file(args.env_file)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=Path(args.artifact_root),
            dry_run=not bool(args.live_tools),
            network_access=bool(args.live_tools),
            allowed_network_hosts=default_external_tool_hosts(dict(os.environ)),
            tool_timeout_seconds=120.0,
        )
    )
    tool_names = split_values(args.tool)
    live_missing_env: list[str] = []
    env_requirements: dict[str, tuple[str, ...]] = {}
    if args.live_tools:
        known_tools = set(registry.names())
        env_names = [name for name in tool_names if name in known_tools] or None
        env_requirements = registry.env_requirements(env_names)
        live_missing_env = sorted(
            {
                key
                for keys in env_requirements.values()
                for key in keys
                if not env_present(key)
            }
        )
    executions: list[dict[str, Any]] = []
    for name in tool_names:
        call = ToolCall(
            id=f"doctor-{name}",
            name=name,
            arguments=doctor_tool_args(name),
        )
        result = await registry.run_tool_call(call)
        executions.append(
            {
                "tool": name,
                "ok": not result.is_error,
                "is_error": result.is_error,
                "content": _doctor_result_content(name, result.content),
            }
        )
    payload = {
        "stats": registry.stats(),
        "health": await registry.health_check(),
        "executions": executions,
        "live_tools": bool(args.live_tools),
        "env_requirements": env_requirements,
        "missing_env": live_missing_env,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if all(item["ok"] for item in executions) and not live_missing_env else 1


def missing_live_tool_env(tool_names: tuple[str, ...]) -> list[str]:
    registry = build_default_tool_registry(context=ToolContext(dry_run=True))
    known = set(registry.names())
    env_requirements = registry.env_requirements(
        name for name in tool_names if name in known
    )
    return sorted(
        {
            key
            for keys in env_requirements.values()
            for key in keys
            if not env_present(key)
        }
    )


def doctor_tool_args(name: str) -> dict[str, Any]:
    if name == "search.web":
        return {"query": "HydraMind multi-agent framework", "count": 1}
    if name == "image.generate":
        return {
            "prompt": "A minimal blue square icon",
            "size": DEFAULT_DOUBAO_IMAGE_SIZE,
        }
    if name == "artifact.write_json":
        return {
            "path": "doctor.json",
            "data": {"ok": True, "source": "hydramind doctor"},
        }
    if name == "artifact.read_json":
        return {"path": "doctor.json"}
    if name == "artifact.write_text":
        return {"path": "doctor.txt", "content": "hydramind doctor"}
    if name == "artifact.read_text":
        return {"path": "doctor.txt"}
    if name == "artifact.exists":
        return {"path": "doctor.txt"}
    if name == "artifact.list":
        return {"path": ".", "recursive": False, "max_items": 20}
    if name == "time.now":
        return {}
    return {}


def _doctor_result_content(tool_name: str, content: str) -> str:
    if tool_name != "image.generate":
        return content
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    _redact_media_payload(payload.get("result"))
    return json.dumps(payload, ensure_ascii=False, default=str)


def _redact_media_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"url", "b64_json"} and isinstance(item, str):
                value[key] = "<redacted>"
            else:
                _redact_media_payload(item)
    elif isinstance(value, list):
        for item in value:
            _redact_media_payload(item)


__all__ = [
    "doctor_tool_args",
    "missing_live_tool_env",
    "run_doctor_tools",
]
