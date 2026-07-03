from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_acceptance_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "p0_acceptance.py"
    spec = importlib.util.spec_from_file_location("p0_acceptance", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_acceptance_uses_enqueued_session_id(tmp_path: Path, capsys) -> None:
    module = _load_acceptance_module()
    fake_hydramind = tmp_path / "fake_hydramind.py"
    calls_log = tmp_path / "calls.jsonl"
    fake_hydramind.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"log_path = {str(calls_log)!r}",
                "with open(log_path, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(sys.argv[1:]) + '\\n')",
                "import os",
                "args = sys.argv[1:]",
                "if args[:1] == ['run'] and '--mock-fixture' in args:",
                "    root = args[args.index('--artifact-root') + 1]",
                "    os.makedirs(root, exist_ok=True)",
                "    open(os.path.join(root, 'brief.md'), 'w').write('# brief')",
                "    print(json.dumps({'session_id': 'sess-team', 'status': 'completed'}))",
                "elif args[:1] == ['run'] and '--enqueue-only' in args:",
                "    print(json.dumps({'session_id': 'sess-from-enqueue', 'status': 'queued'}))",
                "elif args[:1] == ['run']:",
                "    print(json.dumps({'session_id': 'sess-direct', 'status': 'completed'}))",
                "elif args[:2] == ['doctor', 'tools']:",
                "    print(json.dumps({'stats': {'total_tools': 9}, 'executions': []}))",
                "elif args[:2] == ['worker', 'once']:",
                "    sid = args[args.index('--session-id') + 1]",
                "    print(json.dumps({'status': 'completed', 'session_id': sid}))",
                "else:",
                "    print(json.dumps({'error': args}))",
                "    raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )
    fake_hydramind.chmod(0o755)

    rc = module.main(
        [
            "--root",
            str(tmp_path),
            "--mode",
            "local",
            "--hydramind",
            str(fake_hydramind),
            "--env-file",
            str(tmp_path / ".env"),
            "--store-path",
            str(tmp_path / "acceptance.sqlite"),
            "--native-team-artifact-root",
            str(tmp_path / "native-team"),
            "--quiet",
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "pass: short_video worker once" in captured.out
    assert "pass: native-team mock e2e" in captured.out
    assert "pass: native-team artifact present" in captured.out

    calls = [json.loads(line) for line in calls_log.read_text(encoding="utf-8").splitlines()]
    worker_call = next(call for call in calls if call[:2] == ["worker", "once"])
    assert worker_call[worker_call.index("--session-id") + 1] == "sess-from-enqueue"


def test_full_acceptance_runs_live_steps_and_gate(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test uses a fake hydramind/gate-ops and never intends a real provider
    # call. Clear ambient provider credentials so the in-process Class-4
    # live-agent acceptance step deterministically reports not-run (a real key
    # leaking from another test must not trigger a live network call here).
    for key in ("DEEPSEEK_API_KEY", "KIMI_API_KEY", "GLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    module = _load_acceptance_module()
    fake_hydramind = tmp_path / "fake_hydramind.py"
    fake_gate_ops = tmp_path / "fake_gate_ops.py"
    hydramind_calls = tmp_path / "hydramind_calls.jsonl"
    gate_calls = tmp_path / "gate_calls.jsonl"
    fake_hydramind.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"log_path = {str(hydramind_calls)!r}",
                "with open(log_path, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(sys.argv[1:]) + '\\n')",
                "import os",
                "args = sys.argv[1:]",
                "if args[:1] == ['run'] and '--mock-fixture' in args:",
                "    root = args[args.index('--artifact-root') + 1]",
                "    os.makedirs(root, exist_ok=True)",
                "    open(os.path.join(root, 'brief.md'), 'w').write('# brief')",
                "    print(json.dumps({'session_id': 'sess-team', 'status': 'completed'}))",
                "elif args[:1] == ['run'] and '--enqueue-only' in args:",
                "    print(json.dumps({'session_id': 'sess-full', 'status': 'queued'}))",
                "elif args[:1] == ['run']:",
                "    print(json.dumps({'session_id': 'sess-direct', 'status': 'completed'}))",
                "elif args[:2] == ['worker', 'once']:",
                "    print(json.dumps({'status': 'completed', 'session_id': 'sess-full'}))",
                "elif args[:2] == ['doctor', 'env']:",
                "    print(json.dumps({'ok': True, 'profiles': {}}))",
                "elif args[:2] == ['doctor', 'providers']:",
                "    print(json.dumps({'providers': [{'role': 'planner', 'ok': True}]}))",
                "elif args[:2] == ['doctor', 'tools']:",
                "    print(json.dumps({'stats': {'total_tools': 9}, 'missing_env': []}))",
                "else:",
                "    print(json.dumps({'error': args}))",
                "    raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )
    fake_gate_ops.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"log_path = {str(gate_calls)!r}",
                "with open(log_path, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(sys.argv[1:]) + '\\n')",
                "print(json.dumps({'verdict': 'pass'}))",
            ]
        ),
        encoding="utf-8",
    )
    fake_hydramind.chmod(0o755)
    fake_gate_ops.chmod(0o755)

    rc = module.main(
        [
            "--root",
            str(tmp_path),
            "--mode",
            "full",
            "--hydramind",
            str(fake_hydramind),
            "--python",
            sys.executable,
            "--gate-ops",
            str(fake_gate_ops),
            "--env-file",
            str(tmp_path / ".env"),
            "--native-team-artifact-root",
            str(tmp_path / "native-team"),
            "--quiet",
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "pass: env preflight" in captured.out
    assert "pass: provider live smoke" in captured.out
    assert "pass: live tool smoke" in captured.out
    assert "pass: S7 checkpoint gate" in captured.out
    # Class 4/5 live acceptance reports not-proven without credentials (never a
    # live call, never replay-as-live, and not silently counted as pass).
    assert "not-proven: live-agent acceptance (Class 4)" in captured.out
    assert "not-proven: live-MAS acceptance (Class 5)" in captured.out

    calls = [
        json.loads(line) for line in hydramind_calls.read_text(encoding="utf-8").splitlines()
    ]
    assert any(call[:2] == ["doctor", "env"] for call in calls)
    provider_call = next(call for call in calls if call[:2] == ["doctor", "providers"])
    assert provider_call[provider_call.index("--prompt") + 1] == "Reply with OK."
    assert provider_call[provider_call.index("--max-tokens") + 1] == "16"
    assert provider_call[provider_call.index("--timeout-seconds") + 1] == "30.0"
    assert any(call[:2] == ["doctor", "tools"] and "--live-tools" in call for call in calls)
    gate_call = json.loads(gate_calls.read_text(encoding="utf-8").strip())
    assert gate_call == [
        "--root",
        ".",
        "check",
        "--id",
        "PLAN-20260517-001",
        "--checkpoint",
        "S7-production-runtime",
    ]


def test_full_acceptance_skips_live_steps_when_env_preflight_fails(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep this hermetic even when the developer shell has live provider keys;
    # the env preflight failure path must not accidentally call a real model.
    for key in ("DEEPSEEK_API_KEY", "KIMI_API_KEY", "GLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    module = _load_acceptance_module()
    fake_hydramind = tmp_path / "fake_hydramind.py"
    fake_gate_ops = tmp_path / "fake_gate_ops.py"
    hydramind_calls = tmp_path / "hydramind_calls.jsonl"
    fake_hydramind.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"log_path = {str(hydramind_calls)!r}",
                "with open(log_path, 'a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(sys.argv[1:]) + '\\n')",
                "import os",
                "args = sys.argv[1:]",
                "if args[:1] == ['run'] and '--mock-fixture' in args:",
                "    root = args[args.index('--artifact-root') + 1]",
                "    os.makedirs(root, exist_ok=True)",
                "    open(os.path.join(root, 'brief.md'), 'w').write('# brief')",
                "    print(json.dumps({'session_id': 'sess-team', 'status': 'completed'}))",
                "elif args[:1] == ['run'] and '--enqueue-only' in args:",
                "    print(json.dumps({'session_id': 'sess-full', 'status': 'queued'}))",
                "elif args[:1] == ['run']:",
                "    print(json.dumps({'session_id': 'sess-direct', 'status': 'completed'}))",
                "elif args[:2] == ['worker', 'once']:",
                "    print(json.dumps({'status': 'completed', 'session_id': 'sess-full'}))",
                "elif args[:2] == ['doctor', 'env']:",
                "    print(json.dumps({'ok': False, 'missing_template': ['BRAVE_SEARCH_API_KEY=']}))",
                "    raise SystemExit(1)",
                "elif args[:2] == ['doctor', 'tools'] and '--live-tools' not in args:",
                "    print(json.dumps({'stats': {'total_tools': 9}, 'missing_env': []}))",
                "else:",
                "    print(json.dumps({'unexpected': args}))",
                "    raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )
    fake_gate_ops.write_text("#!/usr/bin/env python3\nraise SystemExit(2)\n", encoding="utf-8")
    fake_hydramind.chmod(0o755)
    fake_gate_ops.chmod(0o755)

    rc = module.main(
        [
            "--root",
            str(tmp_path),
            "--mode",
            "full",
            "--hydramind",
            str(fake_hydramind),
            "--python",
            sys.executable,
            "--gate-ops",
            str(fake_gate_ops),
            "--env-file",
            str(tmp_path / ".env"),
            "--native-team-artifact-root",
            str(tmp_path / "native-team"),
            "--quiet",
        ]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "fail(1): env preflight" in captured.out
    assert "skip live provider/tool smoke and S7 checkpoint gate" in captured.out
    assert "not-proven: live-agent acceptance (Class 4)" in captured.out
    assert "not-proven: live-MAS acceptance (Class 5)" in captured.out

    calls = [
        json.loads(line) for line in hydramind_calls.read_text(encoding="utf-8").splitlines()
    ]
    assert any(call[:2] == ["doctor", "env"] for call in calls)
    assert not any(call[:2] == ["doctor", "providers"] for call in calls)
    assert not any(call[:2] == ["doctor", "tools"] and "--live-tools" in call for call in calls)


def test_extract_session_id_rejects_failed_or_invalid_results() -> None:
    module = _load_acceptance_module()

    assert (
        module._extract_session_id(
            [
                module.StepResult(
                    name="short_video sqlite enqueue",
                    returncode=1,
                    stdout='{"session_id":"sess-failed"}',
                    stderr="",
                )
            ],
            "short_video sqlite enqueue",
        )
        is None
    )
    assert (
        module._extract_session_id(
            [
                module.StepResult(
                    name="short_video sqlite enqueue",
                    returncode=0,
                    stdout="not-json",
                    stderr="",
                )
            ],
            "short_video sqlite enqueue",
        )
        is None
    )


def test_resolve_executable_prefers_explicit_and_existing_default(tmp_path: Path) -> None:
    module = _load_acceptance_module()
    explicit = "/opt/hydramind"
    assert module._resolve_executable(tmp_path, explicit, ".venv/bin/hydramind") == explicit

    local_executable = tmp_path / ".venv" / "bin" / "hydramind"
    local_executable.parent.mkdir(parents=True)
    local_executable.write_text("", encoding="utf-8")
    assert module._resolve_executable(tmp_path, None, ".venv/bin/hydramind") == local_executable
    assert module._resolve_executable(tmp_path, None, ".venv/bin/missing") == "missing"


def test_live_acceptance_env_loads_env_file_without_overriding_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_acceptance_module()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=from-file\nKIMI_API_KEY='from-file-kimi'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-parent")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    env = module._live_acceptance_env(tmp_path, ".env")

    assert env["DEEPSEEK_API_KEY"] == "from-parent"
    assert env["KIMI_API_KEY"] == "from-file-kimi"


def test_run_steps_times_out_hung_child(tmp_path: Path) -> None:
    module = _load_acceptance_module()

    results = module._run_steps(
        [
            module.Step(
                "hung child",
                [sys.executable, "-c", "import time; time.sleep(5)"],
                timeout_seconds=0.01,
            )
        ],
        cwd=tmp_path,
        quiet=True,
    )

    assert results[0].returncode == 124
    assert "Timed out after 0.01 seconds" in results[0].stderr
