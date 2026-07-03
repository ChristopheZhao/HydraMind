"""Run the short_video reference workflow."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hydramind.runtime import run_workflow_file


async def run_short_video_demo(
    *,
    input_payload: dict[str, Any] | None = None,
    provider_name: str = "mock",
) -> dict[str, Any]:
    session = await run_workflow_file(
        Path(__file__).with_name("workflow.yaml"),
        input_payload=input_payload or {"topic": "Python"},
        provider_name=provider_name,
    )
    return {
        "session_id": session.id,
        "status": session.status.value,
        "summary_output": session.summary_output,
    }


def main() -> int:
    result = asyncio.run(run_short_video_demo())
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
