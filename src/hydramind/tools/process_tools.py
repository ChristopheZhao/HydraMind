"""Built-in controlled process execution tool handler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hydramind.tools.base import ToolContext, ToolExecutionResult
from hydramind.tools.tool_utils import (
    bounded_float,
    bounded_int,
    decode_limited,
    safe_artifact_path,
)

PROCESS_TIMEOUT_CLEANUP_SECONDS = 2.0


async def run_process(args: dict[str, Any], context: ToolContext) -> ToolExecutionResult:
    raw_argv = args.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv:
        return ToolExecutionResult.fail("argv must be a non-empty string array")
    argv = [str(item) for item in raw_argv]
    if any(not item for item in argv):
        return ToolExecutionResult.fail("argv entries must not be empty")
    command = argv[0]
    if command not in context.allowed_process_commands:
        return ToolExecutionResult.fail(
            f"process command {command!r} is not allowed",
            metadata={
                "policy_denied": True,
                "sandbox_policy": "command_allowlist",
                "command": command,
            },
        )
    if denial := _argv_prefix_denial(argv, context):
        return denial
    raw_cwd = str(args.get("cwd") or ".").strip() or "."
    try:
        cwd = safe_artifact_path(context.artifact_root, raw_cwd)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    if raw_cwd == ".":
        cwd.mkdir(parents=True, exist_ok=True)
    if not cwd.exists():
        return ToolExecutionResult.fail(f"process cwd {raw_cwd!r} does not exist")
    if not cwd.is_dir():
        return ToolExecutionResult.fail(f"process cwd {raw_cwd!r} is not a directory")
    timeout_seconds = _process_timeout(args.get("timeout_seconds"), context)
    max_output_bytes = bounded_int(
        args.get("max_output_bytes"),
        default=8192,
        minimum=1,
        maximum=65536,
    )
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=dict(context.env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    communicate_task = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate_task),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        killed_for_timeout = False
        if process.returncode is None:
            try:
                process.kill()
                killed_for_timeout = True
            except ProcessLookupError:
                pass
        stdout, stderr, cleanup_timed_out = await _communicate_after_process_timeout(
            communicate_task
        )
        if not killed_for_timeout and not cleanup_timed_out:
            return _process_run_success(
                argv,
                cwd,
                process.returncode,
                stdout,
                stderr,
                max_output_bytes,
                command=command,
                timeout_seconds=timeout_seconds,
            )
        metadata: dict[str, Any] = {
            "command": command,
            "error_type": "TimeoutError",
            "timeout_seconds": timeout_seconds,
        }
        if cleanup_timed_out:
            metadata["cleanup_timeout_seconds"] = PROCESS_TIMEOUT_CLEANUP_SECONDS
        return ToolExecutionResult.fail(
            f"process command {command!r} timed out",
            metadata=metadata,
        )
    return _process_run_success(
        argv,
        cwd,
        process.returncode,
        stdout,
        stderr,
        max_output_bytes,
        command=command,
        timeout_seconds=timeout_seconds,
    )


async def _communicate_after_process_timeout(
    communicate_task: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes, bool]:
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate_task),
            timeout=PROCESS_TIMEOUT_CLEANUP_SECONDS,
        )
    except TimeoutError:
        communicate_task.cancel()
        return b"", b"", True
    return stdout, stderr, False


def _process_run_success(
    argv: list[str],
    cwd: Path,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    max_output_bytes: int,
    *,
    command: str,
    timeout_seconds: float,
) -> ToolExecutionResult:
    return ToolExecutionResult.ok(
        {
            "argv": argv,
            "cwd": str(cwd),
            "exit_code": returncode,
            "stdout": decode_limited(stdout, max_output_bytes),
            "stderr": decode_limited(stderr, max_output_bytes),
            "stdout_truncated": len(stdout) > max_output_bytes,
            "stderr_truncated": len(stderr) > max_output_bytes,
        },
        metadata={"command": command, "timeout_seconds": timeout_seconds},
    )


def _argv_prefix_denial(
    argv: list[str],
    context: ToolContext,
) -> ToolExecutionResult | None:
    prefixes = context.allowed_process_argv_prefixes
    if not prefixes:
        return None
    command = argv[0]
    for prefix in prefixes:
        if not prefix or prefix[0] != command:
            continue
        if len(argv) >= len(prefix) and tuple(argv[: len(prefix)]) == prefix:
            return None
    return ToolExecutionResult.fail(
        f"process argv for command {command!r} is not allowed by argv prefix policy",
        metadata={
            "policy_denied": True,
            "sandbox_policy": "argv_prefix",
            "command": command,
            "allowed_prefix_count": len(prefixes),
            "argv_length": len(argv),
        },
    )


def _process_timeout(value: Any, context: ToolContext) -> float:
    default = context.tool_timeout_seconds or 30.0
    return bounded_float(value, default=default, minimum=0.1, maximum=600.0)
