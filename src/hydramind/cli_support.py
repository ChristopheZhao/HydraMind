"""Shared helpers for HydraMind CLI command modules."""

from __future__ import annotations

import os
from typing import Any

from hydramind.control import RuntimeSession


def split_values(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        out.extend(part.strip() for part in item.split(",") if part.strip())
    return out


def env_present(key: str) -> bool:
    value = os.environ.get(key)
    return isinstance(value, str) and bool(value.strip())


def tool_execution_ledger(session: RuntimeSession) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node in session.nodes.values():
        for attempt in node.attempts:
            for tool in attempt.tool_executions:
                records.append(
                    {
                        "node_key": node.key,
                        "execution_id": attempt.id,
                        "trace_id": tool.trace_id,
                        "tool_call_id": tool.tool_call_id,
                        "tool_name": tool.tool_name,
                        "round": tool.round_no,
                        "status": tool.status.value,
                        "is_error": tool.is_error,
                        "content_length": tool.content_length,
                        "result_preview": tool.result_preview,
                        "metadata": tool.metadata,
                    }
                )
    return records


def required_tool_evidence(
    tool_name: str,
    ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    records = [item for item in ledger if item["tool_name"] == tool_name]
    statuses = [str(item["status"]) for item in records]
    return {
        "tool": tool_name,
        "started": bool(records),
        "succeeded": any(
            item["status"] == "succeeded" and item["is_error"] is not True
            for item in records
        ),
        "statuses": statuses,
    }
