"""Tool registry, ToolSpec exposure, and ToolCall execution tests."""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error

import pytest

from hydramind.harness import ToolCall
from hydramind.tools import (
    ExecutionEnvironment,
    ToolContext,
    ToolExecutionMetadata,
    ToolExecutionResult,
    ToolPolicy,
    ToolRegistry,
    build_default_tool_registry,
    external_tools,
    process_tools,
    tool_utils,
)
from hydramind.tools import (
    builtin as builtin_tools,
)


@pytest.mark.asyncio
async def test_default_tools_expose_specs_and_dry_run_search(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    specs = registry.specs_for()
    assert {spec.name for spec in specs} == {
        "artifact.exists",
        "artifact.list",
        "artifact.read_json",
        "artifact.read_text",
        "artifact.write_json",
        "artifact.write_text",
        "image.generate",
        "process.run",
        "search.web",
        "time.now",
    }
    assert registry.stats()["enabled_tools"] == 10
    assert registry.env_requirements() == {
        "image.generate": ("DOUBAO_API_KEY",),
        "search.web": ("BRAVE_SEARCH_API_KEY",),
    }
    health = await registry.health_check()
    assert health["unhealthy_tools"] == 0
    health_by_name = {item["name"]: item for item in health["tools"]}
    assert health_by_name["search.web"]["required_env"] == ("BRAVE_SEARCH_API_KEY",)

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-search",
                name="search.web",
                arguments={"query": "HydraMind MAS", "count": 3},
            ),
        )
    )

    assert len(results) == 1
    assert not results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["success"] is True
    assert payload["result"]["mode"] == "dry_run"
    assert payload["metadata"]["provider"] == "brave"


@pytest.mark.asyncio
async def test_dry_run_image_generation_uses_seedream_defaults(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image",
                name="image.generate",
                arguments={"prompt": "blue square"},
            ),
        )
    )

    assert not results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["result"]["model"] == "doubao-seedream-5-0-260128"
    assert payload["result"]["size"] == "2K"
    assert payload["metadata"] == {"provider": "doubao", "live": False}


@pytest.mark.asyncio
async def test_artifact_tool_writes_under_context_root(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-write",
                name="artifact.write_json",
                arguments={"path": "scene/manifest.json", "data": {"ok": True}},
            ),
        )
    )

    assert not results[0].is_error
    assert (tmp_path / "scene" / "manifest.json").exists()
    assert json.loads((tmp_path / "scene" / "manifest.json").read_text()) == {"ok": True}

    read_results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-read",
                name="artifact.read_json",
                arguments={"path": "scene/manifest.json"},
            ),
        )
    )

    assert not read_results[0].is_error
    payload = json.loads(read_results[0].content)
    assert payload["result"]["data"] == {"ok": True}


def test_execution_environment_derives_node_scoped_tool_context(tmp_path) -> None:
    environment = ExecutionEnvironment(
        artifact_root=tmp_path,
        dry_run=True,
        env={"A": "B"},
        approved_tools=("artifact.write_text",),
        allowed_env_keys=("A",),
        network_access=True,
        allowed_network_hosts=("api.search.brave.com",),
        tool_timeout_seconds=12.5,
        allowed_process_commands=(sys.executable,),
        allowed_process_argv_prefixes=((sys.executable, "-c"),),
        metadata={"run": "goal"},
    )

    context = environment.to_tool_context(node_key="write", role="writer")

    assert context.artifact_root == tmp_path
    assert context.dry_run is True
    assert context.env["A"] == "B"
    assert context.node_key == "write"
    assert context.role == "writer"
    assert context.approved_tools == ("artifact.write_text",)
    assert context.allowed_env_keys == ("A",)
    assert context.network_access is True
    assert context.allowed_network_hosts == ("api.search.brave.com",)
    assert context.tool_timeout_seconds == 12.5
    assert context.allowed_process_commands == (sys.executable,)
    assert context.allowed_process_argv_prefixes == ((sys.executable, "-c"),)
    assert context.metadata == {"run": "goal"}


def test_tool_execution_metadata_is_redaction_safe_and_stable(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=False)
    )
    context = registry.context_for_node("write", "writer")
    call = ToolCall(
        id="call-write",
        name="artifact.write_text",
        arguments={
            "path": "notes/out.txt",
            "content": "ok",
            "api_key": "secret-value",
        },
    )

    first = registry.tool_execution_metadata(call, context=context)
    second = registry.tool_execution_metadata(call, context=context)
    different = registry.tool_execution_metadata(
        call.model_copy(
            update={
                "arguments": {
                    "path": "notes/other.txt",
                    "content": "ok",
                    "api_key": "secret-value",
                }
            }
        ),
        context=context,
    )

    assert isinstance(first, ToolExecutionMetadata)
    assert first.registered is True
    assert first.risk_class == "write_artifact"
    assert first.side_effect_class == "artifact_write"
    assert first.idempotency_scope == "artifact_root"
    assert first.effect_fingerprint.startswith("sha256:")
    assert first.effect_fingerprint == second.effect_fingerprint
    assert first.effect_fingerprint != different.effect_fingerprint
    # The redaction-safe detail mapping carries the same typed values and never
    # leaks the secret argument.
    detail = first.as_detail()
    assert detail["registered"] is True
    assert detail["effect_fingerprint"] == first.effect_fingerprint
    assert "secret-value" not in json.dumps(detail)
    assert "secret-value" not in json.dumps(first.effect_fingerprint)


def test_process_run_metadata_reports_approval_requirement(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    metadata = registry.tool_execution_metadata(
        ToolCall(
            id="call-process",
            name="process.run",
            arguments={"argv": [sys.executable, "-c", "print('ok')"]},
        )
    )

    assert metadata.risk_class == "destructive"
    assert metadata.side_effect_class == "destructive_local"
    assert metadata.requires_approval is True
    assert metadata.approval_present is False


@pytest.mark.asyncio
async def test_text_artifact_tools_execute_through_function_call_contract(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-text",
                name="artifact.write_text",
                arguments={"path": "notes/summary.txt", "content": "ready"},
            ),
            ToolCall(
                id="call-read-text",
                name="artifact.read_text",
                arguments={"path": "notes/summary.txt"},
            ),
            ToolCall(
                id="call-exists",
                name="artifact.exists",
                arguments={"path": "notes/summary.txt"},
            ),
            ToolCall(
                id="call-list",
                name="artifact.list",
                arguments={"path": ".", "recursive": True, "max_items": 10},
            ),
            ToolCall(id="call-time", name="time.now", arguments={}),
        )
    )

    assert [item.is_error for item in results] == [False, False, False, False, False]
    assert (tmp_path / "notes" / "summary.txt").read_text() == "ready"
    read_payload = json.loads(results[1].content)
    assert read_payload["result"]["content"] == "ready"
    assert read_payload["result"]["bytes"] == 5
    exists_payload = json.loads(results[2].content)
    assert exists_payload["result"]["exists"] is True
    assert exists_payload["result"]["kind"] == "file"
    list_payload = json.loads(results[3].content)
    assert list_payload["result"]["items"] == [
        {"path": "notes", "kind": "directory", "bytes": None},
        {"path": "notes/summary.txt", "kind": "file", "bytes": 5},
    ]
    time_payload = json.loads(results[4].content)
    assert time_payload["result"]["timezone"] == "UTC"
    assert time_payload["result"]["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_artifact_tools_reject_path_traversal(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-bad-write",
                name="artifact.write_text",
                arguments={"path": "../escape.txt", "content": "no"},
            ),
            ToolCall(
                id="call-bad-list",
                name="artifact.list",
                arguments={"path": "../"},
            ),
        )
    )

    assert [item.is_error for item in results] == [True, True]
    assert all("artifact_root" in json.loads(item.content)["error"] for item in results)


@pytest.mark.asyncio
async def test_artifact_tools_reject_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-read-link",
                name="artifact.read_text",
                arguments={"path": "link.txt"},
            ),
            ToolCall(
                id="call-write-link",
                name="artifact.write_text",
                arguments={"path": "link.txt", "content": "overwritten"},
            ),
            ToolCall(
                id="call-exists-link",
                name="artifact.exists",
                arguments={"path": "link.txt"},
            ),
            ToolCall(
                id="call-list-link",
                name="artifact.list",
                arguments={"path": ".", "recursive": False},
            ),
        )
    )

    assert [item.is_error for item in results] == [True, True, True, True]
    assert outside.read_text(encoding="utf-8") == "secret"
    errors = [json.loads(item.content)["error"] for item in results]
    assert all("symlink" in error for error in errors)


@pytest.mark.asyncio
async def test_unknown_tool_returns_structured_error(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (ToolCall(id="missing", name="missing.tool", arguments={}),)
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["success"] is False
    assert "not registered" in payload["error"]


@pytest.mark.asyncio
async def test_tool_policy_denies_before_handler_runs(tmp_path) -> None:
    calls: list[str] = []
    registry = ToolRegistry(
        default_context=ToolContext(artifact_root=tmp_path, node_key="blocked")
    )

    async def handler(args, context):
        calls.append("called")
        return ToolExecutionResult.ok({"should_not": "run"})

    registry.register_function(
        name="artifact.restricted_write",
        description="restricted writer",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        policy=ToolPolicy(allowed_nodes=("allowed",)),
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-policy",
                name="artifact.restricted_write",
                arguments={},
            ),
        )
    )

    assert calls == []
    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["metadata"]["policy_denied"] is True
    assert "not allowed for node" in payload["error"]


@pytest.mark.asyncio
async def test_tool_timeout_returns_structured_error(tmp_path) -> None:
    calls: list[str] = []
    registry = ToolRegistry(
        default_context=ToolContext(
            artifact_root=tmp_path,
            tool_timeout_seconds=0.01,
        )
    )

    async def handler(args, context):
        del args, context
        calls.append("started")
        await asyncio.sleep(5)
        calls.append("finished")
        return ToolExecutionResult.ok({"too": "late"})

    registry.register_function(
        name="slow.tool",
        description="slow async tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )

    results = await registry.run_tool_calls(
        (ToolCall(id="call-slow", name="slow.tool", arguments={}),)
    )

    assert calls == ["started"]
    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["error"] == "tool 'slow.tool' timed out"
    assert payload["metadata"]["error_type"] == "TimeoutError"
    assert payload["metadata"]["timeout_seconds"] == 0.01


@pytest.mark.asyncio
async def test_tool_handler_receives_scoped_env(tmp_path) -> None:
    seen: dict[str, str] = {}
    registry = ToolRegistry(
        default_context=ToolContext(
            artifact_root=tmp_path,
            env={
                "SECRET_TOKEN": "hidden",
                "PUBLIC_CONFIG": "visible",
                "REQUIRED_KEY": "required",
            },
            allowed_env_keys=("PUBLIC_CONFIG",),
        )
    )

    async def handler(args, context):
        del args
        seen.update(context.env)
        return ToolExecutionResult.ok({"env_keys": sorted(context.env)})

    registry.register_function(
        name="env.scoped",
        description="env scoped tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        required_env=("REQUIRED_KEY",),
    )

    results = await registry.run_tool_calls(
        (ToolCall(id="call-env", name="env.scoped", arguments={}),)
    )

    assert not results[0].is_error
    assert seen == {
        "PUBLIC_CONFIG": "visible",
        "REQUIRED_KEY": "required",
    }
    payload = json.loads(results[0].content)
    assert payload["result"]["env_keys"] == ["PUBLIC_CONFIG", "REQUIRED_KEY"]


@pytest.mark.asyncio
async def test_tool_handler_hides_env_without_declaration(tmp_path) -> None:
    seen: dict[str, str] = {}
    registry = ToolRegistry(
        default_context=ToolContext(
            artifact_root=tmp_path,
            env={"SECRET_TOKEN": "hidden"},
        )
    )

    async def handler(args, context):
        del args
        seen.update(context.env)
        return ToolExecutionResult.ok({"env_keys": sorted(context.env)})

    registry.register_function(
        name="env.hidden",
        description="env hidden tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )

    results = await registry.run_tool_calls(
        (ToolCall(id="call-env-hidden", name="env.hidden", arguments={}),)
    )

    assert not results[0].is_error
    assert seen == {}
    payload = json.loads(results[0].content)
    assert payload["result"]["env_keys"] == []


@pytest.mark.asyncio
async def test_process_run_is_default_denied(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(artifact_root=tmp_path, dry_run=True)
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-denied",
                name="process.run",
                arguments={"argv": [sys.executable, "-c", "print('no')"]},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["metadata"]["policy_denied"] is True
    assert "requires approval" in payload["error"]


@pytest.mark.asyncio
async def test_process_run_requires_allowlisted_command_after_approval(
    tmp_path,
) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-denied-command",
                name="process.run",
                arguments={"argv": [sys.executable, "-c", "print('no')"]},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "not allowed" in payload["error"]
    assert payload["metadata"]["policy_denied"] is True
    assert payload["metadata"]["sandbox_policy"] == "command_allowlist"
    assert payload["metadata"]["command"] == sys.executable


@pytest.mark.asyncio
async def test_process_run_rejects_argv_prefix_mismatch_after_command_allowlist(
    tmp_path,
) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
            allowed_process_commands=(sys.executable,),
            allowed_process_argv_prefixes=((sys.executable, "-m", "pytest"),),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-denied-prefix",
                name="process.run",
                arguments={"argv": [sys.executable, "-c", "print('no')"]},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "argv prefix policy" in payload["error"]
    assert payload["metadata"]["policy_denied"] is True
    assert payload["metadata"]["sandbox_policy"] == "argv_prefix"
    assert payload["metadata"]["command"] == sys.executable
    assert payload["metadata"]["allowed_prefix_count"] == 1
    assert payload["metadata"]["argv_length"] == 3


@pytest.mark.asyncio
async def test_process_run_executes_allowlisted_command_under_artifact_root(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
            allowed_process_commands=(sys.executable,),
            allowed_process_argv_prefixes=((sys.executable, "-c"),),
            tool_timeout_seconds=5.0,
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process",
                name="process.run",
                arguments={
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('out.txt').write_text('ok'); print('done')",
                    ],
                },
            ),
        )
    )

    assert not results[0].is_error
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"
    payload = json.loads(results[0].content)
    assert payload["result"]["exit_code"] == 0
    assert payload["result"]["stdout"] == "done\n"
    assert payload["metadata"]["command"] == sys.executable


@pytest.mark.asyncio
async def test_process_run_handles_process_lookup_race(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FinishedDuringTimeoutProcess:
        returncode: int | None = None

        def __init__(self) -> None:
            self.communicate_calls = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.sleep(1)
            self.returncode = 0
            return b"done\n", b""

        def kill(self) -> None:
            self.returncode = 0
            raise ProcessLookupError

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return FinishedDuringTimeoutProcess()

    monkeypatch.setattr(
        process_tools.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
            allowed_process_commands=(sys.executable,),
            tool_timeout_seconds=0.01,
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-race",
                name="process.run",
                arguments={"argv": [sys.executable, "-c", "print('done')"]},
            ),
        )
    )

    assert not results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["result"]["exit_code"] == 0
    assert payload["result"]["stdout"] == "done\n"


@pytest.mark.asyncio
async def test_process_run_rejects_cwd_escape(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
            allowed_process_commands=(sys.executable,),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-cwd",
                name="process.run",
                arguments={
                    "argv": [sys.executable, "-c", "print('no')"],
                    "cwd": "..",
                },
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "artifact_root" in payload["error"]


@pytest.mark.asyncio
async def test_process_run_timeout_kills_process(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            approved_tools=("process.run",),
            allowed_process_commands=(sys.executable,),
            tool_timeout_seconds=0.1,
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-process-timeout",
                name="process.run",
                arguments={
                    "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
                },
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["metadata"]["error_type"] == "TimeoutError"
    assert payload["metadata"]["timeout_seconds"] == 0.1
    assert "timed out" in payload["error"]


@pytest.mark.asyncio
async def test_live_search_requires_api_key(tmp_path) -> None:
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={},
            network_access=True,
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-search",
                name="search.web",
                arguments={"query": "HydraMind MAS"},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["success"] is False
    assert "BRAVE_SEARCH_API_KEY" in payload["error"]


@pytest.mark.asyncio
async def test_live_external_tool_requires_network_access(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_get_json(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        del url, headers, timeout_seconds, max_bytes
        calls.append("called")
        return {}

    monkeypatch.setattr(external_tools, "http_get_json", fake_get_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={"BRAVE_SEARCH_API_KEY": "brave-test"},
            network_access=False,
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-search-network-denied",
                name="search.web",
                arguments={"query": "HydraMind MAS"},
            ),
        )
    )

    assert calls == []
    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["metadata"]["policy_denied"] is True
    assert "requires network access" in payload["error"]


@pytest.mark.asyncio
async def test_live_external_tool_respects_host_allowlist(
    tmp_path, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        del url, headers, payload, timeout_seconds, max_bytes
        calls.append("called")
        return {}

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={
                "DOUBAO_API_KEY": "doubao-test",
                "DOUBAO_IMAGE_API_URL": "https://blocked.example.test/images",
            },
            network_access=True,
            allowed_network_hosts=("ark.cn-beijing.volces.com",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-host-denied",
                name="image.generate",
                arguments={"prompt": "blue square"},
            ),
        )
    )

    assert calls == []
    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["metadata"]["policy_denied"] is True
    assert "blocked.example.test" in payload["error"]


@pytest.mark.asyncio
async def test_live_search_uses_brave_response_shape(tmp_path, monkeypatch) -> None:
    def fake_get_json(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        del max_bytes
        assert "api.search.brave.com" in url
        assert headers["X-Subscription-Token"] == "brave-test"
        assert timeout_seconds == 2.5
        return {
            "web": {
                "results": [
                    {
                        "title": "HydraMind",
                        "url": "https://example.test/hydramind",
                        "description": "Framework result",
                    },
                    {
                        "title": "Other",
                        "url": "https://example.test/other",
                        "description": "Second result",
                    },
                ]
            }
        }

    monkeypatch.setattr(external_tools, "http_get_json", fake_get_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={
                "BRAVE_SEARCH_API_KEY": "brave-test",
                "BRAVE_SEARCH_TIMEOUT_SECONDS": "2.5",
            },
            network_access=True,
            allowed_network_hosts=("api.search.brave.com",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-search-live",
                name="search.web",
                arguments={"query": "HydraMind MAS", "count": 1},
            ),
        )
    )

    assert not results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["result"]["mode"] == "live"
    assert payload["result"]["items"] == [
        {
            "title": "HydraMind",
            "url": "https://example.test/hydramind",
            "description": "Framework result",
        }
    ]
    assert payload["metadata"] == {"provider": "brave", "live": True}


@pytest.mark.asyncio
async def test_live_search_returns_structured_http_failure(tmp_path, monkeypatch) -> None:
    def failing_get_json(
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(external_tools, "http_get_json", failing_get_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={"BRAVE_SEARCH_API_KEY": "brave-test"},
            network_access=True,
            allowed_network_hosts=("api.search.brave.com",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-search-live-fail",
                name="search.web",
                arguments={"query": "HydraMind MAS"},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "search.web live request failed" in payload["error"]
    assert payload["metadata"]["provider"] == "brave"
    assert payload["metadata"]["live"] is True
    assert payload["metadata"]["error_type"] == "URLError"
    assert payload["metadata"]["timeout_seconds"] == 30.0


@pytest.mark.asyncio
async def test_live_image_generation_posts_to_doubao_endpoint(tmp_path, monkeypatch) -> None:
    def fake_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        del max_bytes
        assert url == "https://ark.example.test/images/generations"
        assert headers["Authorization"] == "Bearer doubao-test"
        assert timeout_seconds == 7.0
        assert payload == {
            "model": "doubao-image-test",
            "prompt": "blue square",
            "size": "2K",
            "response_format": "url",
        }
        return {"data": [{"url": "https://example.test/image.png"}]}

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={
                "DOUBAO_API_KEY": "doubao-test",
                "DOUBAO_IMAGE_MODEL": "doubao-image-test",
                "DOUBAO_IMAGE_API_URL": "https://ark.example.test/images/generations",
                "DOUBAO_IMAGE_TIMEOUT_SECONDS": "7",
            },
            network_access=True,
            allowed_network_hosts=("ark.example.test",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-live",
                name="image.generate",
                arguments={"prompt": "blue square"},
            ),
        )
    )

    assert not results[0].is_error
    payload = json.loads(results[0].content)
    assert payload["result"] == {"data": [{"url": "https://example.test/image.png"}]}
    assert payload["metadata"] == {"provider": "doubao", "live": True}


@pytest.mark.asyncio
async def test_live_image_generation_returns_structured_http_failure(
    tmp_path, monkeypatch
) -> None:
    def failing_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(external_tools, "http_post_json", failing_post_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={"DOUBAO_API_KEY": "doubao-test"},
            network_access=True,
            allowed_network_hosts=("ark.cn-beijing.volces.com",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-live-fail",
                name="image.generate",
                arguments={"prompt": "blue square"},
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "image.generate live request failed" in payload["error"]
    assert payload["metadata"]["provider"] == "doubao"
    assert payload["metadata"]["live"] is True
    assert payload["metadata"]["error_type"] == "HTTPError"
    assert payload["metadata"]["status_code"] == 429
    assert payload["metadata"]["timeout_seconds"] == 120.0


@pytest.mark.asyncio
async def test_live_image_generation_save_to_downloads_bytes(
    tmp_path, monkeypatch
) -> None:
    def fake_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        return {
            "data": [
                {"url": "https://cdn.example.test/blue.png"},
            ]
        }

    download_calls: list[tuple[str, float]] = []

    def fake_get_bytes(
        url: str, timeout_seconds: float, max_bytes: int = 0
    ) -> bytes:
        del max_bytes
        download_calls.append((url, timeout_seconds))
        return b"\x89PNG\r\n\x1a\nFAKE-PNG-BYTES"

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)
    monkeypatch.setattr(external_tools, "http_get_bytes", fake_get_bytes)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={
                "DOUBAO_API_KEY": "doubao-test",
                "DOUBAO_IMAGE_API_URL": "https://ark.example.test/images/generations",
            },
            network_access=True,
            allowed_network_hosts=("ark.example.test", "cdn.example.test"),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-save-to",
                name="image.generate",
                arguments={
                    "prompt": "blue square",
                    "save_to": "assets/blue.png",
                },
            ),
        )
    )

    assert not results[0].is_error, results[0].content
    payload = json.loads(results[0].content)
    assert payload["result"]["saved_path"] == "assets/blue.png"
    assert payload["result"]["saved_bytes"] == len(b"\x89PNG\r\n\x1a\nFAKE-PNG-BYTES")
    assert payload["result"]["image_url"] == "https://cdn.example.test/blue.png"
    saved_file = tmp_path / "assets" / "blue.png"
    assert saved_file.read_bytes() == b"\x89PNG\r\n\x1a\nFAKE-PNG-BYTES"
    assert download_calls == [("https://cdn.example.test/blue.png", 120.0)]


@pytest.mark.asyncio
async def test_live_image_generation_save_to_rejects_host_outside_allowlist(
    tmp_path, monkeypatch
) -> None:
    def fake_post_json(
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
        max_bytes: int = 0,
    ) -> dict[str, object]:
        return {"data": [{"url": "https://disallowed-cdn.example.test/x.png"}]}

    download_calls: list[str] = []

    def fake_get_bytes(
        url: str, timeout_seconds: float, max_bytes: int = 0
    ) -> bytes:
        del max_bytes
        download_calls.append(url)
        return b"should-not-be-called"

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)
    monkeypatch.setattr(external_tools, "http_get_bytes", fake_get_bytes)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={
                "DOUBAO_API_KEY": "doubao-test",
                "DOUBAO_IMAGE_API_URL": "https://ark.example.test/images/generations",
            },
            network_access=True,
            allowed_network_hosts=("ark.example.test",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-save-host-blocked",
                name="image.generate",
                arguments={
                    "prompt": "blue square",
                    "save_to": "assets/blue.png",
                },
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "disallowed-cdn.example.test" in payload["error"]
    assert payload["metadata"]["policy_denied"] is True
    assert not (tmp_path / "assets" / "blue.png").exists()
    assert download_calls == []


@pytest.mark.asyncio
async def test_live_image_generation_save_to_rejects_escape_path(
    tmp_path, monkeypatch
) -> None:
    def fake_post_json(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("must not POST when save_to is invalid")

    monkeypatch.setattr(external_tools, "http_post_json", fake_post_json)
    registry = build_default_tool_registry(
        context=ToolContext(
            artifact_root=tmp_path,
            dry_run=False,
            env={"DOUBAO_API_KEY": "doubao-test"},
            network_access=True,
            allowed_network_hosts=("ark.cn-beijing.volces.com",),
        )
    )

    results = await registry.run_tool_calls(
        (
            ToolCall(
                id="call-image-save-escape",
                name="image.generate",
                arguments={
                    "prompt": "blue square",
                    "save_to": "../escape.png",
                },
            ),
        )
    )

    assert results[0].is_error
    payload = json.loads(results[0].content)
    assert "save_to is invalid" in payload["error"]


def test_default_external_tool_hosts_includes_doubao_asset_cdn() -> None:
    hosts = builtin_tools.default_external_tool_hosts({})
    assert "ark-acg-cn-beijing.tos-cn-beijing.volces.com" in hosts
    # Operator override should be honored.
    custom = builtin_tools.default_external_tool_hosts(
        {"DOUBAO_IMAGE_ASSET_HOST": "my.cdn.example"}
    )
    assert "my.cdn.example" in custom


# ---------------------------------------------------------------------------
# HTTP response size-guard tests (DoS / memory-exhaustion vector)
# ---------------------------------------------------------------------------


class _CappedResponse:
    """Response fake exposing a ``getheader`` accessor plus a capped ``read``.

    ``read(amt)`` honours the requested byte count like a real
    ``HTTPResponse`` so that an oversized body is still detected via the
    ``max_bytes + 1`` probe even when ``Content-Length`` lies or is absent.
    """

    def __init__(self, payload: bytes, *, content_length: int | None = None) -> None:
        self._payload = payload
        self._content_length = content_length

    def getheader(self, name: str, default: object = None) -> object:
        if name.lower() == "content-length" and self._content_length is not None:
            return str(self._content_length)
        return default

    def read(self, amt: int = -1) -> bytes:
        if amt < 0:
            return self._payload
        return self._payload[:amt]

    def __enter__(self) -> _CappedResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_http_get_json_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        # Tiny body, but the advertised Content-Length is huge.
        return _CappedResponse(b"{}", content_length=10_000)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="Content-Length"):
        tool_utils.http_get_json(
            "https://example.invalid/api", {}, 5.0, 1024
        )


def test_http_get_json_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = b"x" * 5000

    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        # No Content-Length advertised, but the streamed body exceeds the cap.
        return _CappedResponse(big, content_length=None)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="body exceeds cap"):
        tool_utils.http_get_json(
            "https://example.invalid/api", {}, 5.0, 1024
        )


def test_http_post_json_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        return _CappedResponse(b"{}", content_length=999_999)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="Content-Length"):
        tool_utils.http_post_json(
            "https://example.invalid/api", {}, {"q": "x"}, 5.0, 2048
        )


def test_http_post_json_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = json.dumps({"k": "y" * 5000}).encode("utf-8")

    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        return _CappedResponse(big, content_length=None)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="body exceeds cap"):
        tool_utils.http_post_json(
            "https://example.invalid/api", {}, {"q": "x"}, 5.0, 1024
        )


def test_http_get_bytes_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        return _CappedResponse(b"\x89PNG", content_length=64_000_000)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="Content-Length"):
        tool_utils.http_get_bytes(
            "https://example.invalid/image.png", 5.0, 4096
        )


def test_http_get_bytes_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = b"\x00" * 8192

    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        return _CappedResponse(big, content_length=None)

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="body exceeds cap"):
        tool_utils.http_get_bytes(
            "https://example.invalid/image.png", 5.0, 1024
        )


def test_http_get_bytes_allows_payload_within_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x89PNG\r\n"

    def _fake_urlopen(request: object, timeout: float = 0.0) -> _CappedResponse:
        return _CappedResponse(payload, content_length=len(payload))

    monkeypatch.setattr(tool_utils.urllib.request, "urlopen", _fake_urlopen)

    result = tool_utils.http_get_bytes(
        "https://example.invalid/image.png", 5.0, 1024
    )
    assert result == payload


def test_tool_context_default_http_response_cap_is_50mib() -> None:
    assert ToolContext().max_http_response_bytes == 50 * 1024 * 1024
