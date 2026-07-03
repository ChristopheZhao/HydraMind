"""Trace event schema and artifact observer tests."""

from __future__ import annotations

import json

import pytest

from hydramind.observability import (
    Emitter,
    JsonlObserver,
    ListObserver,
    ObservationEvent,
    ObservationEventKind,
    redact_value,
    redacted_tool_result_preview,
)


def test_observation_event_carries_trace_correlation() -> None:
    event = ObservationEvent(
        kind=ObservationEventKind.MODEL_INVOKE_COMPLETED,
        session_id="sess-1",
        node_key="plan",
        trace_id="trace-1",
        execution_id="exec-1",
        source="orchestration",
        detail={"tool_call_count": 1},
    )

    assert event.event_id.startswith("evt-")
    assert event.trace_id == "trace-1"
    assert event.execution_id == "exec-1"
    assert event.source == "orchestration"


def test_redaction_masks_secret_like_keys_and_urls() -> None:
    payload = {
        "api_key": "secret",
        "nested": {"access_token": "token", "url": "https://example.com/a"},
        "items": [{"password": "p"}],
    }

    assert redact_value(payload) == {
        "api_key": "<redacted>",
        "nested": {"access_token": "<redacted>", "url": "<redacted-url>"},
        "items": [{"password": "<redacted>"}],
    }


def test_tool_result_preview_redacts_json_payloads() -> None:
    content = json.dumps(
        {
            "tool": "image.generate",
            "success": True,
            "result": {
                "image_url": "https://example.com/image.png?signature=secret",
                "payload": "A" * 120,
            },
            "metadata": {"authorization": "Bearer live-secret", "provider": "doubao"},
        }
    )

    preview = redacted_tool_result_preview(content)

    assert preview["content_json"] is True
    assert preview["content_redacted"] is True
    assert preview["content_keys"] == ["metadata", "result", "success", "tool"]
    assert "live-secret" not in preview["content_preview"]
    assert "signature=secret" not in preview["content_preview"]
    assert "A" * 96 not in preview["content_preview"]
    assert "<redacted-url>" in preview["content_preview"]
    assert "<redacted-payload>" in preview["content_preview"]


def test_tool_result_preview_redacts_non_json_text() -> None:
    content = (
        "download https://example.com/file?token=secret "
        "with bearer Bearer abc.def "
        f"and payload {'B' * 120}"
    )

    preview = redacted_tool_result_preview(content)

    assert preview["content_json"] is False
    assert preview["content_redacted"] is True
    assert "token=secret" not in preview["content_preview"]
    assert "abc.def" not in preview["content_preview"]
    assert "B" * 96 not in preview["content_preview"]
    assert "<redacted-url>" in preview["content_preview"]


@pytest.mark.asyncio
async def test_jsonl_observer_writes_trace_artifact(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    list_observer = ListObserver()
    emitter = Emitter([JsonlObserver(path), list_observer])

    await emitter.emit(
        ObservationEvent(
            kind=ObservationEventKind.TOOL_CALL_STARTED,
            session_id="sess-1",
            node_key="plan",
            trace_id="trace-1",
            execution_id="exec-1",
            detail={"tool_name": "artifact.write_json"},
        )
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["kind"] == "tool_call_started"
    assert loaded["trace_id"] == "trace-1"
    assert list_observer.kinds() == ["tool_call_started"]
