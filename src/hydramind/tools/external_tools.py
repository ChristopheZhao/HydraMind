"""Built-in external HTTP-backed tool handlers."""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from hydramind.tools.base import ToolContext, ToolExecutionResult
from hydramind.tools.tool_utils import (
    bounded_float,
    bounded_int,
    env,
    http_get_bytes,
    http_get_json,
    http_post_json,
    http_tool_failure,
    network_denial,
    safe_artifact_path,
    url_host,
)

DEFAULT_DOUBAO_IMAGE_MODEL = "doubao-seedream-5-0-260128"
DEFAULT_DOUBAO_IMAGE_SIZE = "2K"
DEFAULT_BRAVE_SEARCH_HOST = "api.search.brave.com"
DEFAULT_DOUBAO_IMAGE_HOST = "ark.cn-beijing.volces.com"
DEFAULT_DOUBAO_IMAGE_ASSET_HOST = "ark-acg-cn-beijing.tos-cn-beijing.volces.com"


def default_external_tool_hosts(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Return built-in external tool hosts for network allowlist wiring."""

    source = env or {}
    doubao_url = source.get("DOUBAO_IMAGE_API_URL") or (
        "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    )
    asset_host = source.get("DOUBAO_IMAGE_ASSET_HOST") or DEFAULT_DOUBAO_IMAGE_ASSET_HOST
    hosts = {
        DEFAULT_BRAVE_SEARCH_HOST,
        url_host(doubao_url) or DEFAULT_DOUBAO_IMAGE_HOST,
        asset_host,
    }
    return tuple(sorted(hosts))


async def search_web(args: dict[str, Any], context: ToolContext) -> ToolExecutionResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolExecutionResult.fail("query is required")
    count = bounded_int(args.get("count"), default=5, minimum=1, maximum=10)
    api_key = env(context, "BRAVE_SEARCH_API_KEY")
    if context.dry_run:
        return ToolExecutionResult.ok(
            {
                "query": query,
                "items": [],
                "mode": "dry_run",
                "reason": "dry_run",
            },
            metadata={"provider": "brave", "live": False},
        )
    if not api_key:
        return ToolExecutionResult.fail(
            "BRAVE_SEARCH_API_KEY is required for live search.web",
            metadata={"provider": "brave", "live": False},
        )
    timeout_seconds = bounded_float(
        env(context, "BRAVE_SEARCH_TIMEOUT_SECONDS"),
        default=30.0,
        minimum=1.0,
        maximum=300.0,
    )
    params = urllib.parse.urlencode({"q": query, "count": str(count)})
    url = f"https://{DEFAULT_BRAVE_SEARCH_HOST}/res/v1/web/search?{params}"
    denial = network_denial(context, "search.web", DEFAULT_BRAVE_SEARCH_HOST)
    if denial is not None:
        return ToolExecutionResult.fail(
            denial,
            metadata={"provider": "brave", "live": False, "policy_denied": True},
        )
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    try:
        raw = http_get_json(
            url, headers, timeout_seconds, context.max_http_response_bytes
        )
    except Exception as exc:
        return http_tool_failure(
            "search.web",
            exc,
            provider="brave",
            timeout_seconds=timeout_seconds,
        )
    web = raw.get("web") if isinstance(raw, dict) else {}
    raw_results = web.get("results") if isinstance(web, dict) else []
    results = raw_results if isinstance(raw_results, list) else []
    items = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "description": item.get("description"),
        }
        for item in results[:count]
        if isinstance(item, dict)
    ]
    return ToolExecutionResult.ok(
        {"query": query, "items": items, "mode": "live"},
        metadata={"provider": "brave", "live": True},
    )


async def generate_image(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolExecutionResult:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolExecutionResult.fail("prompt is required")
    api_key = env(context, "DOUBAO_API_KEY")
    model = str(
        args.get("model")
        or env(context, "DOUBAO_IMAGE_MODEL")
        or DEFAULT_DOUBAO_IMAGE_MODEL
    )
    size = str(args.get("size") or DEFAULT_DOUBAO_IMAGE_SIZE)
    raw_save_to = args.get("save_to")
    save_to = str(raw_save_to).strip() if isinstance(raw_save_to, str) else ""
    if context.dry_run:
        return ToolExecutionResult.ok(
            {
                "prompt": prompt,
                "model": model,
                "size": size,
                "mode": "dry_run",
                "image_url": None,
                "saved_path": save_to or None,
            },
            metadata={"provider": "doubao", "live": False},
        )
    if not api_key:
        return ToolExecutionResult.fail(
            "DOUBAO_API_KEY is required for live image.generate",
            metadata={"provider": "doubao", "live": False},
        )
    url = env(context, "DOUBAO_IMAGE_API_URL") or (
        "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    )
    host = url_host(url)
    if not host:
        return ToolExecutionResult.fail(
            "DOUBAO_IMAGE_API_URL must include a valid network host",
            metadata={"provider": "doubao", "live": False},
        )
    save_target: Path | None = None
    if save_to:
        try:
            save_target = safe_artifact_path(context.artifact_root, save_to)
        except ValueError as exc:
            return ToolExecutionResult.fail(
                f"save_to is invalid: {exc}",
                metadata={"provider": "doubao", "live": False},
            )
    timeout_seconds = bounded_float(
        env(context, "DOUBAO_IMAGE_TIMEOUT_SECONDS"),
        default=120.0,
        minimum=1.0,
        maximum=600.0,
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
    }
    denial = network_denial(context, "image.generate", host)
    if denial is not None:
        return ToolExecutionResult.fail(
            denial,
            metadata={"provider": "doubao", "live": False, "policy_denied": True},
        )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        raw = http_post_json(
            url, headers, payload, timeout_seconds, context.max_http_response_bytes
        )
    except Exception as exc:
        return http_tool_failure(
            "image.generate",
            exc,
            provider="doubao",
            timeout_seconds=timeout_seconds,
        )
    result_data: dict[str, Any] = dict(raw)
    if save_target is not None:
        image_url = _extract_image_url(raw)
        if not image_url:
            return ToolExecutionResult.fail(
                "image.generate did not return an image URL to download",
                metadata={"provider": "doubao", "live": True, "save_to": save_to},
            )
        asset_host = url_host(image_url)
        if not asset_host:
            return ToolExecutionResult.fail(
                f"image.generate returned malformed URL: {image_url!r}",
                metadata={"provider": "doubao", "live": True},
            )
        asset_denial = network_denial(
            context,
            "image.generate.download",
            asset_host,
        )
        if asset_denial is not None:
            return ToolExecutionResult.fail(
                asset_denial,
                metadata={
                    "provider": "doubao",
                    "live": True,
                    "policy_denied": True,
                    "asset_host": asset_host,
                },
            )
        try:
            image_bytes = http_get_bytes(
                image_url, timeout_seconds, context.max_http_response_bytes
            )
        except Exception as exc:
            return http_tool_failure(
                "image.generate.download",
                exc,
                provider="doubao",
                timeout_seconds=timeout_seconds,
            )
        save_target.parent.mkdir(parents=True, exist_ok=True)
        save_target.write_bytes(image_bytes)
        result_data["saved_path"] = save_to
        result_data["saved_bytes"] = len(image_bytes)
        result_data["image_url"] = image_url
    return ToolExecutionResult.ok(
        result_data, metadata={"provider": "doubao", "live": True}
    )


def _extract_image_url(raw: dict[str, Any]) -> str | None:
    data = raw.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
    image_url = raw.get("image_url")
    if isinstance(image_url, str) and image_url.strip():
        return image_url.strip()
    url_field = raw.get("url")
    if isinstance(url_field, str) and url_field.strip():
        return url_field.strip()
    return None
