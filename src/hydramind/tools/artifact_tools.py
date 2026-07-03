"""Built-in artifact read/write/list tool handlers."""

from __future__ import annotations

import json
from typing import Any

from hydramind.tools.base import ToolContext, ToolExecutionResult
from hydramind.tools.tool_utils import (
    artifact_relative_path,
    bounded_int,
    safe_artifact_path,
    write_json_sync,
)


async def write_json_artifact(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return ToolExecutionResult.fail("path is required")
    data = args.get("data")
    if not isinstance(data, dict):
        return ToolExecutionResult.fail("data must be an object")
    try:
        target = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    write_json_sync(target, data)
    return ToolExecutionResult.ok({"path": str(target)}, metadata={"live": True})


async def read_json_artifact(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return ToolExecutionResult.fail("path is required")
    try:
        target = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    if not target.exists():
        return ToolExecutionResult.fail(f"artifact {raw_path!r} does not exist")
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ToolExecutionResult.fail(
            f"artifact {raw_path!r} is not valid JSON: {exc.msg}"
        )
    return ToolExecutionResult.ok(
        {"path": str(target), "data": parsed},
        metadata={"live": True},
    )


async def artifact_exists(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return ToolExecutionResult.fail("path is required")
    try:
        target = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    kind = "missing"
    if target.is_file():
        kind = "file"
    elif target.is_dir():
        kind = "directory"
    return ToolExecutionResult.ok(
        {"path": str(target), "exists": target.exists(), "kind": kind},
        metadata={"live": True},
    )


async def list_artifacts(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or ".").strip() or "."
    recursive = bool(args.get("recursive", False))
    max_items = bounded_int(args.get("max_items"), default=50, minimum=1, maximum=500)
    try:
        base = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    if not base.exists():
        return ToolExecutionResult.fail(f"artifact directory {raw_path!r} does not exist")
    if not base.is_dir():
        return ToolExecutionResult.fail(f"artifact path {raw_path!r} is not a directory")

    iterator = sorted(
        base.rglob("*") if recursive else base.iterdir(),
        key=lambda path: path.as_posix(),
    )
    items: list[dict[str, Any]] = []
    for item in iterator[:max_items]:
        if item.is_symlink():
            return ToolExecutionResult.fail(
                "artifact path must not contain symlink components"
            )
        try:
            relative = artifact_relative_path(context.artifact_root, item)
        except ValueError as exc:
            return ToolExecutionResult.fail(str(exc))
        kind = "directory" if item.is_dir() else "file"
        stat = item.stat() if item.is_file() else None
        items.append(
            {
                "path": relative,
                "kind": kind,
                "bytes": stat.st_size if stat is not None else None,
            }
        )
    return ToolExecutionResult.ok(
        {
            "path": str(base),
            "items": items,
            "recursive": recursive,
            "truncated": len(iterator) > max_items,
        },
        metadata={"live": True},
    )


async def write_text_artifact(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return ToolExecutionResult.fail("path is required")
    content = args.get("content")
    if not isinstance(content, str):
        return ToolExecutionResult.fail("content must be a string")
    try:
        target = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ToolExecutionResult.ok(
        {"path": str(target), "bytes": len(content.encode("utf-8"))},
        metadata={"live": True},
    )


async def read_text_artifact(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return ToolExecutionResult.fail("path is required")
    try:
        target = safe_artifact_path(context.artifact_root, raw_path)
    except ValueError as exc:
        return ToolExecutionResult.fail(str(exc))
    if not target.exists():
        return ToolExecutionResult.fail(f"artifact {raw_path!r} does not exist")
    if not target.is_file():
        return ToolExecutionResult.fail(f"artifact {raw_path!r} is not a file")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolExecutionResult.fail(f"artifact {raw_path!r} is not valid UTF-8 text")
    return ToolExecutionResult.ok(
        {
            "path": str(target),
            "content": content,
            "bytes": len(content.encode("utf-8")),
        },
        metadata={"live": True},
    )
