#!/usr/bin/env python3
"""Run HydraMind local acceptance smoke commands without printing secrets.

Evidence classes (see docs/architecture/95-execution-harness-correction.md §9):

- ``--mode local`` runs CONTRACT / PLUMBING / REPLAY checks only. Every step uses
  the in-process deterministic ``MockProvider`` (no live model) or dry-run tool
  diagnostics. This is NOT live-agent or live-MAS acceptance — no model decides,
  so it proves wiring and reproducibility, not agent quality.
- ``--mode full`` additionally runs a LIVE PROVIDER/TOOL smoke (provider
  reachability + live tool calls) and records credential-gated live-agent
  (Class 4) plus live-MAS (Class 5) acceptance attempts. A live attempt reports
  ``not-proven`` when credentials or network are unavailable; mock/replay is
  never reported as live evidence.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    parse_json: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    status_label: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    hydramind = _resolve_executable(root, args.hydramind, ".venv/bin/hydramind")
    python = _resolve_executable(root, args.python, ".venv/bin/python")
    gate_ops = Path(args.gate_ops)
    store_path = Path(args.store_path)

    print(f"HydraMind local acceptance mode: {args.mode}")
    if args.mode == "local":
        print(
            "evidence class: CONTRACT / PLUMBING / REPLAY only "
            "(deterministic MockProvider + dry-run tools; NOT live-agent/live-MAS acceptance)"
        )
    else:
        print(
            "evidence class: local contract/plumbing/replay + LIVE PROVIDER/TOOL smoke "
            "+ credential-gated LIVE_AGENT/LIVE_MAS attempts"
        )
    print(f"root: {root}")
    print(f"env_file: {args.env_file}")
    print("secret policy: commands may print key names and presence only; never secret values")
    _print_evidence_taxonomy(args.mode)

    results: list[StepResult] = []
    live_acceptance_env = _live_acceptance_env(root, args.env_file)
    _print_metadata()

    local_steps = [
        Step(
            "short_video mock quickstart",
            [
                str(hydramind),
                "run",
                "examples/short_video/workflow.yaml",
                "--provider",
                "mock",
                "--input",
                "topic=Python",
            ],
            parse_json=True,
            timeout_seconds=args.step_timeout_seconds,
        ),
        Step(
            "short_video sqlite enqueue",
            [
                str(hydramind),
                "run",
                "examples/short_video/workflow.yaml",
                "--provider",
                "mock",
                "--input",
                "topic=Python",
                "--session-store",
                "sqlite",
                "--store-path",
                str(store_path),
                "--enqueue-only",
            ],
            parse_json=True,
            timeout_seconds=args.step_timeout_seconds,
        ),
        Step(
            "native-team mock e2e",
            [
                str(hydramind),
                "run",
                "examples/native_team/workflow.yaml",
                "--provider",
                "mock",
                "--mock-fixture",
                "examples/native_team/mock_fixture.json",
                "--artifact-root",
                args.native_team_artifact_root,
            ],
            parse_json=True,
            timeout_seconds=args.step_timeout_seconds,
        ),
        Step(
            "dry-run tool registry and function calls",
            [
                str(hydramind),
                "doctor",
                "tools",
                "--env-file",
                args.env_file,
                "--artifact-root",
                args.artifact_root,
                "--tool",
                "search.web,artifact.write_json,artifact.read_json,artifact.write_text,"
                "artifact.read_text,artifact.exists,artifact.list,time.now",
            ],
            parse_json=True,
            timeout_seconds=args.step_timeout_seconds,
        ),
    ]
    _clear_native_team_artifact(args.native_team_artifact_root)
    results.extend(_run_steps(local_steps, cwd=root, quiet=args.quiet))
    native_team_check = _verify_native_team_artifact(
        results,
        artifact_root=args.native_team_artifact_root,
    )
    print(f"\n== {native_team_check.name}")
    if not args.quiet:
        _print_output(native_team_check, parse_json=False)
    results.append(native_team_check)

    session_id = _extract_session_id(results, "short_video sqlite enqueue")
    if session_id:
        results.extend(
            _run_steps(
                [
                    Step(
                        "short_video worker once",
                        [
                            str(hydramind),
                            "worker",
                            "once",
                            "examples/short_video/workflow.yaml",
                            "--provider",
                            "mock",
                            "--session-store",
                            "sqlite",
                            "--store-path",
                            str(store_path),
                            "--session-id",
                            session_id,
                        ],
                        parse_json=True,
                        timeout_seconds=args.step_timeout_seconds,
                    )
                ],
                cwd=root,
                quiet=args.quiet,
            )
        )
    else:
        results.append(
            StepResult(
                name="short_video worker once",
                returncode=2,
                stdout="",
                stderr="Could not parse session_id from enqueue step",
            )
        )

    if args.package_build:
        hatch = _resolve_executable(root, args.hatch, ".venv/bin/hatch")
        results.extend(
            _run_steps(
                [
                    Step(
                        "package wheel and sdist build",
                        [str(hatch), "build"],
                        timeout_seconds=args.step_timeout_seconds,
                    )
                ],
                cwd=root,
                quiet=args.quiet,
            )
        )

    if args.mode == "full":
        env_steps = [
            Step(
                "env preflight",
                [
                    str(hydramind),
                    "doctor",
                    "env",
                    "--env-file",
                    args.env_file,
                    "--include-missing-template",
                ],
                parse_json=True,
                timeout_seconds=args.step_timeout_seconds,
            ),
        ]
        env_results = _run_steps(env_steps, cwd=root, quiet=args.quiet)
        results.extend(env_results)
        if not all(result.ok for result in env_results):
            print("skip live provider/tool smoke and S7 checkpoint gate: env preflight failed")
        else:
            full_steps = [
                Step(
                    "provider live smoke",
                    [
                        str(hydramind),
                        "doctor",
                        "providers",
                        "--env-file",
                        args.env_file,
                        "--roles",
                        "orchestrator,planner,executor",
                        "--prompt",
                        "Reply with OK.",
                        "--max-tokens",
                        str(args.provider_max_tokens),
                        "--timeout-seconds",
                        str(args.provider_timeout_seconds),
                    ],
                    parse_json=True,
                    timeout_seconds=args.provider_step_timeout_seconds,
                ),
                Step(
                    "live tool smoke",
                    [
                        str(hydramind),
                        "doctor",
                        "tools",
                        "--env-file",
                        args.env_file,
                        "--artifact-root",
                        args.live_artifact_root,
                        "--live-tools",
                        "--tool",
                        "search.web,image.generate",
                    ],
                    parse_json=True,
                    timeout_seconds=args.step_timeout_seconds,
                ),
            ]
            if gate_ops.exists():
                full_steps.append(
                    Step(
                        "S7 checkpoint gate",
                        [
                            str(python),
                            str(gate_ops),
                            "--root",
                            ".",
                            "check",
                            "--id",
                            "PLAN-20260517-001",
                            "--checkpoint",
                            "S7-production-runtime",
                        ],
                        parse_json=True,
                        timeout_seconds=args.step_timeout_seconds,
                    )
                )
            else:
                print(f"skip S7 checkpoint gate: missing {gate_ops}")
            results.extend(_run_steps(full_steps, cwd=root, quiet=args.quiet))
        results.append(_run_live_acceptance_step("live_agent", env=live_acceptance_env))
        results.append(_run_live_acceptance_step("live_mas", env=live_acceptance_env))

    _print_summary(results)
    if args.mode == "local":
        print(
            "\nevidence class: contract/plumbing/replay only — "
            "live-agent/live-MAS acceptance NOT proven by this local run"
        )
    else:
        print(
            "\nevidence class: contract/plumbing/replay + attempted live provider/tool smoke — "
            "Class 4/5 live acceptance status is reported per live step above "
            "(pass = proven; not-proven = credentials/network unavailable)"
        )
    return 0 if all(result.ok for result in results) else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run HydraMind local acceptance smoke checks (contract/plumbing/replay; "
            "full mode adds live provider/tool reachability plus credential-gated "
            "Class 4/5 live acceptance attempts) without printing secrets."
        )
    )
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--mode",
        choices=("local", "full"),
        default="local",
        help=(
            "local = contract/plumbing/replay checks (mock/dry-run, no live model); "
            "full = local + live provider/tool reachability smoke and S7 gate "
            "+ Class 4/5 live attempts (not-proven when unavailable)"
        ),
    )
    parser.add_argument("--env-file", default=".env", help="dotenv file to load")
    parser.add_argument(
        "--artifact-root",
        default="/tmp/hydramind-p0-acceptance-tools",
        help="dry-run/local artifact root",
    )
    parser.add_argument(
        "--live-artifact-root",
        default="/tmp/hydramind-p0-acceptance-live-tools",
        help="live tool artifact root",
    )
    parser.add_argument(
        "--native-team-artifact-root",
        default="/tmp/hydramind-p0-acceptance-native-team",
        help="artifact root for the native-team mock e2e check",
    )
    parser.add_argument(
        "--store-path",
        default="/tmp/hydramind-p0-acceptance.sqlite",
        help="SQLite path for worker handoff smoke",
    )
    parser.add_argument(
        "--gate-ops",
        default="/home/zhaojj/.codex/skills/checkpoint-gatekeeper/scripts/gate_ops.py",
        help="checkpoint-gatekeeper gate_ops.py path",
    )
    parser.add_argument("--hydramind", default=None, help="hydramind executable override")
    parser.add_argument("--python", default=None, help="python executable override")
    parser.add_argument("--hatch", default=None, help="hatch executable override")
    parser.add_argument(
        "--package-build",
        action="store_true",
        help="also build wheel and sdist with hatch",
    )
    parser.add_argument(
        "--provider-timeout-seconds",
        type=float,
        default=30.0,
        help="timeout passed to provider live smoke in full mode",
    )
    parser.add_argument(
        "--provider-max-tokens",
        type=int,
        default=16,
        help="max tokens passed to provider live smoke in full mode",
    )
    parser.add_argument(
        "--provider-step-timeout-seconds",
        type=float,
        default=120.0,
        help="subprocess timeout for the provider live smoke step",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=float,
        default=120.0,
        help="subprocess timeout for each non-provider acceptance step",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only step headers and final summary",
    )
    return parser.parse_args(argv)


def _resolve_executable(root: Path, explicit: str | None, default_rel: str) -> Path | str:
    if explicit:
        return explicit
    candidate = root / default_rel
    return candidate if candidate.exists() else default_rel.rsplit("/", 1)[-1]


def _run_live_acceptance_step(
    acceptance_class_value: str,
    *,
    env: dict[str, str] | None = None,
) -> StepResult:
    """Class 4/5 live acceptance via the credential-gated runner.

    Invokes ``hydramind.governance.run_live_acceptance`` for a fixed task. With
    live credentials present it emits the typed report. WITHOUT credentials it
    reports not-run / not-proven in the summary (a missing-credential live step
    is the user's acceptance gap, not a local wiring failure). Replay/mock is
    never reported as live.
    """

    import asyncio

    from hydramind.governance import (
        AcceptanceClass,
        has_provider_credentials,
        run_live_acceptance,
    )
    from hydramind.governance.live_acceptance import LiveAcceptanceTask

    acceptance_class = AcceptanceClass(acceptance_class_value)
    if acceptance_class is AcceptanceClass.LIVE_AGENT:
        class_no = "Class 4"
        label = "live-agent"
        task_id = "p0-live-agent-ok"
        task_description = "reply with OK under the default harness"
        tools_environment = "live providers (no tools)"
    elif acceptance_class is AcceptanceClass.LIVE_MAS:
        class_no = "Class 5"
        label = "live-MAS"
        task_id = "p0-live-mas-ok"
        task_description = "reply with OK through a native team under the default harness"
        tools_environment = "live providers + native team collaboration"
    else:
        raise ValueError(f"expected live acceptance class, got {acceptance_class.value!r}")

    name = f"{label} acceptance ({class_no})"
    print(f"\n== {name}")
    if not has_provider_credentials(env):
        return StepResult(
            name=name,
            returncode=0,
            stdout=(
                "not run / not proven (no credentials) — set DEEPSEEK_API_KEY / "
                f"KIMI_API_KEY / GLM_API_KEY to run {label} acceptance with "
                "harness=HydraMindExecutionHarness/default. Replay is never "
                "reported as live."
            ),
            stderr="",
            status_label="not-proven",
        )
    task = LiveAcceptanceTask(
        task_id=task_id,
        task_description=task_description,
        prompt="Reply with OK.",
        tools_environment=tools_environment,
        evaluator_profile="nonempty-response",
        acceptance_class=acceptance_class,
    )
    outcome = asyncio.run(
        run_live_acceptance(
            task,
            provider="deepseek",
            harness_name="HydraMindExecutionHarness",
            harness_id="default",
            env=env,
        )
    )
    if not outcome.ran or outcome.report is None:
        return StepResult(
            name=name,
            returncode=0,
            stdout=outcome.reason,
            stderr="",
            status_label="not-proven",
        )
    report = outcome.report
    return StepResult(
        name=name,
        returncode=0 if report.success else 1,
        stdout=json.dumps(report.as_payload(), ensure_ascii=False, indent=2, sort_keys=True),
        stderr="" if report.success else f"{label} acceptance failed: {report.notes}",
    )


def _live_acceptance_env(root: Path, env_file: str) -> dict[str, str]:
    env = dict(os.environ)
    path = Path(env_file)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in env:
            env[key] = value
    return env


def _print_evidence_taxonomy(mode: str) -> None:
    """Enumerate the five acceptance evidence classes (95 §9) and where they run.

    Class 1-3 (contract/plumbing/replay) + the §6 negative-acceptance suite run
    LOCALLY (offline, no credentials). Class 4-5 (live-agent/live-MAS) require
    live provider/tool credentials and are NOT run in ``--mode local``.
    """

    runs_locally = "RUN locally (offline)"
    live_only = (
        "ATTEMPT in --mode full; PASS only with live credentials"
        if mode == "full"
        else "NOT run in --mode local (needs live credentials)"
    )
    print("\n== acceptance evidence taxonomy (95 §9)")
    print(f"  Class 1 CONTRACT   : {runs_locally} — schemas/types/invariants")
    print(f"  Class 2 PLUMBING   : {runs_locally} — control/queue/tool/state wiring")
    print(f"  Class 3 REPLAY     : {runs_locally} — deterministic fixture regression")
    print(
        "  negative suite     : "
        f"{runs_locally} — tests/acceptance/ (§6 5 negative cases incl. multi-worker)"
    )
    print(f"  Class 4 LIVE_AGENT : {live_only}")
    print(f"  Class 5 LIVE_MAS   : {live_only}")


def _print_metadata() -> None:
    try:
        version = metadata.version("hydramind")
        scripts = [
            entry
            for entry in metadata.entry_points(group="console_scripts")
            if entry.name == "hydramind"
        ]
    except metadata.PackageNotFoundError:
        print("package metadata: hydramind is not installed in this environment")
        return
    print(f"package metadata: hydramind {version}")
    print(f"console script: {scripts[0].value if scripts else 'missing'}")


def _run_steps(steps: list[Step], *, cwd: Path, quiet: bool) -> list[StepResult]:
    results = []
    for step in steps:
        print(f"\n== {step.name}")
        print("$ " + " ".join(step.command))
        try:
            completed = subprocess.run(
                step.command,
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=step.timeout_seconds,
            )
            result = StepResult(
                name=step.name,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            result = StepResult(
                name=step.name,
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=f"Timed out after {step.timeout_seconds} seconds",
            )
        if not quiet:
            _print_output(result, parse_json=step.parse_json)
        results.append(result)
    return results


def _print_output(result: StepResult, *, parse_json: bool) -> None:
    if result.stdout:
        if parse_json:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(result.stdout.rstrip())
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)


def _clear_native_team_artifact(artifact_root: str) -> None:
    """Remove a stale brief.md so the e2e check proves THIS run produced it."""
    artifact = Path(artifact_root) / "brief.md"
    if artifact.exists():
        artifact.unlink()


def _verify_native_team_artifact(
    results: list[StepResult],
    *,
    artifact_root: str,
) -> StepResult:
    """DoD-2 gate: the native-team e2e run completed AND produced brief.md.

    Asserts the example CLI run reported ``status == completed`` and that the
    artifact file genuinely exists under the artifact root (the writer member's
    ``artifact.write_text`` tool call wrote it offline).
    """
    name = "native-team artifact present"
    run_step = next((r for r in results if r.name == "native-team mock e2e"), None)
    if run_step is None or not run_step.ok:
        return StepResult(
            name=name,
            returncode=2,
            stdout="",
            stderr="native-team mock e2e step did not run or failed",
        )
    try:
        payload = json.loads(run_step.stdout)
    except json.JSONDecodeError:
        return StepResult(
            name=name,
            returncode=2,
            stdout="",
            stderr="could not parse native-team run JSON",
        )
    status = payload.get("status")
    artifact = Path(artifact_root) / "brief.md"
    if status != "completed":
        return StepResult(
            name=name,
            returncode=1,
            stdout="",
            stderr=f"native-team session status was {status!r}, expected 'completed'",
        )
    if not artifact.exists():
        return StepResult(
            name=name,
            returncode=1,
            stdout="",
            stderr=f"native-team artifact missing: {artifact}",
        )
    return StepResult(
        name=name,
        returncode=0,
        stdout=f"verified artifact: {artifact}",
        stderr="",
    )


def _extract_session_id(results: list[StepResult], step_name: str) -> str | None:
    for result in results:
        if result.name != step_name or not result.ok:
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        session_id = payload.get("session_id")
        return session_id if isinstance(session_id, str) else None
    return None


def _print_summary(results: list[StepResult]) -> None:
    print("\n== summary")
    for result in results:
        status = (
            result.status_label
            if result.status_label is not None
            else ("pass" if result.ok else f"fail({result.returncode})")
        )
        print(f"{status}: {result.name}")


if __name__ == "__main__":
    raise SystemExit(main())
