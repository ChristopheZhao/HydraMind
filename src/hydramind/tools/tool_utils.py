"""Shared helpers for built-in tool implementations."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from hydramind.tools.base import ToolContext, ToolExecutionResult

DEFAULT_MAX_HTTP_RESPONSE_BYTES = 50 * 1024 * 1024


def safe_artifact_path(root: Path, raw_path: str) -> Path:
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("artifact path must be relative and stay under artifact_root")
    root_resolved = root.resolve(strict=False)
    reject_symlink_components(root_resolved, rel)
    target = (root_resolved / rel).resolve(strict=False)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            "artifact path must be relative and stay under artifact_root"
        ) from exc
    return target


def reject_symlink_components(root: Path, rel: Path) -> None:
    current = root
    for part in rel.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.is_symlink():
            raise ValueError("artifact path must not contain symlink components")


def write_json_sync(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def artifact_relative_path(root: Path, item: Path) -> str:
    root_resolved = root.resolve(strict=False)
    item_resolved = item.resolve(strict=False)
    try:
        return item_resolved.relative_to(root_resolved).as_posix()
    except ValueError as exc:
        raise ValueError(
            "artifact path must be relative and stay under artifact_root"
        ) from exc


def read_capped(response: Any, max_bytes: int, *, url: str) -> bytes:
    """Read a response body while enforcing a hard size cap."""

    declared: Any = None
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        declared = getheader("Content-Length")
    else:
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "get", None) if headers is not None else None
        if callable(getter):
            declared = getter("Content-Length")
    if declared is not None:
        try:
            declared_len = int(declared)
        except (TypeError, ValueError):
            declared_len = -1
        if declared_len > max_bytes:
            raise RuntimeError(
                f"response from {url} too large: Content-Length {declared_len} "
                f"exceeds cap of {max_bytes} bytes"
            )
    payload = response.read(max_bytes + 1)
    if not isinstance(payload, bytes):
        raise RuntimeError("expected bytes from HTTP response")
    if len(payload) > max_bytes:
        raise RuntimeError(
            f"response from {url} too large: body exceeds cap of {max_bytes} bytes"
        )
    return payload


def http_get_json(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES,
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = read_capped(response, max_bytes, url=url).decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("tool HTTP response must be a JSON object")
    return parsed


def http_post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    max_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = read_capped(response, max_bytes, url=url).decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("tool HTTP response must be a JSON object")
    return parsed


def http_get_bytes(
    url: str,
    timeout_seconds: float,
    max_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES,
) -> bytes:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return read_capped(response, max_bytes, url=url)


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def decode_limited(raw: bytes, max_bytes: int) -> str:
    return raw[:max_bytes].decode("utf-8", errors="replace")


def http_tool_failure(
    tool_name: str,
    exc: Exception,
    *,
    provider: str,
    timeout_seconds: float,
) -> ToolExecutionResult:
    metadata: dict[str, Any] = {
        "provider": provider,
        "live": True,
        "timeout_seconds": timeout_seconds,
        "error_type": type(exc).__name__,
    }
    if isinstance(exc, urllib.error.HTTPError):
        metadata["status_code"] = exc.code
        reason = f"HTTP {exc.code}"
    elif isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason)
    else:
        reason = str(exc) or type(exc).__name__
    return ToolExecutionResult.fail(
        f"{tool_name} live request failed: {reason}",
        metadata=metadata,
    )


def env(context: ToolContext, key: str) -> str | None:
    value = context.env.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def url_host(url: str) -> str | None:
    return urllib.parse.urlparse(url).hostname


def network_denial(context: ToolContext, tool_name: str, host: str) -> str | None:
    if not context.network_access:
        return f"tool {tool_name!r} requires network access"
    allowed = set(context.allowed_network_hosts)
    if allowed and host not in allowed:
        return f"tool {tool_name!r} cannot access network host {host!r}"
    return None
