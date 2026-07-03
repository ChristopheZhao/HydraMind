"""short_video reference workflow CLI smoke."""

from __future__ import annotations

import json

from hydramind import cli


def test_short_video_workflow_runs_from_cli_with_mock_backend(capsys) -> None:
    rc = cli.main(
        [
            "run",
            "examples/short_video/workflow.yaml",
            "--provider",
            "mock",
            "--input",
            "topic=Python",
            "--env-file",
            "/tmp/hydramind-missing-env",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "completed"
    assert payload["workflow"] == "short_video_demo"
    assert payload["summary_output"]
