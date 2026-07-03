"""Redaction helpers for trace-safe observability payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9._~+/=-]{96,}\b")


def redact_value(value: Any) -> Any:
    """Return a JSON-like value safe enough for local trace artifacts."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower()
            if any(part in lowered for part in _SECRET_KEY_PARTS):
                redacted[text_key] = "<redacted>"
            else:
                redacted[text_key] = redact_value(item)
        return redacted
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return "<redacted-url>"
        if _looks_like_large_payload(value):
            return "<redacted-payload>"
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [redact_value(item) for item in value]
    return value


def compact_text(value: str | None, *, limit: int = 160) -> str:
    if not value:
        return ""
    text = value.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def redact_text(value: str) -> str:
    """Redact sensitive substrings from non-JSON trace previews."""

    redacted = _URL_RE.sub("<redacted-url>", value)
    redacted = _BEARER_RE.sub("Bearer <redacted>", redacted)
    return _LONG_TOKEN_RE.sub(_redact_large_token, redacted)


def redacted_tool_result_preview(
    content: str,
    *,
    limit: int = 160,
) -> dict[str, Any]:
    """Build a trace-safe summary for a ToolResultBlock content string."""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        safe_text = redact_text(content)
        return {
            "content_preview": compact_text(safe_text, limit=limit),
            "content_json": False,
            "content_redacted": safe_text != content,
        }

    redacted = redact_value(parsed)
    serialized = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
    preview: dict[str, Any] = {
        "content_preview": compact_text(serialized, limit=limit),
        "content_json": True,
        "content_redacted": redacted != parsed,
    }
    if isinstance(redacted, Mapping):
        preview["content_keys"] = sorted(str(key) for key in redacted)
    return preview


def _looks_like_large_payload(value: str) -> bool:
    return len(value) >= 96 and _LONG_TOKEN_RE.fullmatch(value) is not None


def _redact_large_token(match: re.Match[str]) -> str:
    text = match.group(0)
    if len(text) >= 96:
        return "<redacted-payload>"
    return text
