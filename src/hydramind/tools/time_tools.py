"""Built-in time tool handler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hydramind.tools.base import ToolContext, ToolExecutionResult


async def time_now(args: dict[str, Any], context: ToolContext) -> ToolExecutionResult:
    del context
    if args:
        return ToolExecutionResult.fail("time.now does not accept arguments")
    now = datetime.now(UTC).replace(microsecond=0)
    return ToolExecutionResult.ok(
        {"timestamp": now.isoformat().replace("+00:00", "Z"), "timezone": "UTC"},
        metadata={"live": True},
    )
