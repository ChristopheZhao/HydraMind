#!/usr/bin/env python3
"""Regenerate the offline mock fixture for the native_team example.

The fixture is an INPUT-KEYED record/replay corpus (``MockProvider.save_fixture``
/ ``from_fixture``): a JSON map from ``invocation_fingerprint`` to a recorded
:class:`InvocationResult`. It deterministically drives the team members offline
so ``hydramind run examples/native_team/workflow.yaml --provider mock
--mock-fixture examples/native_team/mock_fixture.json`` produces a verified
``brief.md`` with no network/live calls.

How it works: this script runs the SAME workflow once with a scripted +
record-mode :class:`MockProvider`. The scripted FIFO turns are the team members'
authored outputs (researcher -> analyst -> writer; the writer emits an
``artifact.write_text`` tool call, then its final content). Recording captures
each turn keyed by its input fingerprint, so on replay the exact same inputs
return the exact same outputs regardless of order. Re-run this script whenever
the workflow or member prompts change:

    .venv/bin/python examples/native_team/generate_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from hydramind.harness import StopReason, ToolCall
from hydramind.runtime import run_workflow_file
from hydramind.testing import MockProvider, ScriptedTurn

EXAMPLE_DIR = Path(__file__).resolve().parent
WORKFLOW = EXAMPLE_DIR / "workflow.yaml"
FIXTURE = EXAMPLE_DIR / "mock_fixture.json"

BRIEF_PATH = "brief.md"
BRIEF_CONTENT = """# One-Page Brief: Native-Team MAS

## Summary
A native-team multi-agent pipeline (researcher -> analyst -> writer) produced
this brief offline. Each member read its predecessor's output via the kernel
scheduler; the writer persisted the deliverable through artifact.write_text.

## Key Insights
1. Topology is interpreted policy: PIPELINE makes member N read member N-1.
2. The artifact is produced by the TEAM (the writer's tool call), not the test.
3. Determinism comes from an input-keyed MockProvider record/replay fixture.

## Recommendation
Use native teams for medium-complexity tasks that need verified file artifacts
delivered reproducibly.
"""


def _scripted_turns() -> list[ScriptedTurn]:
    """The team members' authored outputs, in PIPELINE turn order.

    Order matches the scheduled turns: researcher (1 send), analyst (1 send),
    writer (1 send that emits the artifact.write_text tool call).

    The writer's deliverable is the FILE artifact ``brief.md`` written by its
    tool call — that is what the verifier checks and what DoD-2 requires. After
    the tool result is threaded back, the writer's final post-tool send is NOT
    scripted: it deterministically falls back to the mock echo. That post-tool
    reply is path-dependent (the tool result embeds the absolute artifact path),
    so scripting it would couple the fixture to a specific artifact_root. By
    leaving it to echo, the FIXTURE — and the produced ``brief.md`` — stay
    byte-identical across any artifact_root, which is the reproducibility half
    of DoD-2. The artifact-producing tool-call turn is keyed only on the team
    transcript (path-independent), so it replays deterministically everywhere.
    """

    return [
        ScriptedTurn(
            content=(
                "Findings: native teams run as scheduled agent turns; PIPELINE "
                "threads each member's output to the next; artifacts are produced "
                "by member tool calls."
            ),
        ),
        ScriptedTurn(
            content=(
                "Insights: (1) topology is interpreted policy, (2) the team "
                "produces the artifact, (3) determinism via record/replay."
            ),
        ),
        # Writer: emit the artifact.write_text tool call that produces brief.md.
        ScriptedTurn(
            content="Writing the brief to brief.md.",
            tool_calls=(
                ToolCall(
                    id="call-write-brief",
                    name="artifact.write_text",
                    arguments={"path": BRIEF_PATH, "content": BRIEF_CONTENT},
                ),
            ),
            stop_reason=StopReason.TOOL_USE,
        ),
    ]


async def _record() -> None:
    provider = MockProvider(scripted=_scripted_turns(), record=True)
    with tempfile.TemporaryDirectory() as tmp:
        session = await run_workflow_file(
            WORKFLOW,
            provider=provider,
            env_file=None,
            artifact_root=tmp,
        )
        if session.status.value != "completed":
            raise SystemExit(f"record run did not complete: {session.status.value}")
        produced = Path(tmp) / BRIEF_PATH
        if not produced.exists():
            raise SystemExit("record run did not produce brief.md")
    provider.save_fixture(FIXTURE)
    # Re-serialize through json for a stable, diff-friendly fixture.
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    FIXTURE.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(payload)} fingerprinted responses to {FIXTURE}")


if __name__ == "__main__":
    asyncio.run(_record())
