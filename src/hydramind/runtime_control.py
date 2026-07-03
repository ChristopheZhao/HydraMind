"""Runtime-edge assembly for control-plane dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hydramind.control import ControlPlane, SessionService, SessionStore
from hydramind.gating import GateRegistry, VerifierFeedbackEvaluator
from hydramind.observability import Emitter
from hydramind.runtime_support import create_session_store, load_gate_registry


@dataclass(frozen=True)
class ControlRuntime:
    """Effective control dependencies for one runtime bundle."""

    store: SessionStore
    service: SessionService
    control: ControlPlane


def build_goal_control_runtime(
    *,
    session_store: SessionStore | None,
    session_store_kind: str,
    store_path: str | Path | None,
    emitter: Emitter | None,
) -> ControlRuntime:
    """Assemble the goal-runtime control plane with default verifier feedback gate."""

    store = session_store or create_session_store(session_store_kind, store_path)
    service = SessionService(store, emitter=emitter)
    gate_registry = GateRegistry([VerifierFeedbackEvaluator()])
    control = ControlPlane(service, gate_fn=gate_registry.to_gate_fn())
    return ControlRuntime(store=store, service=service, control=control)


def build_workflow_control_runtime(
    *,
    workflow_path: str | Path,
    session_store: SessionStore | None,
    session_store_kind: str,
    store_path: str | Path | None,
    emitter: Emitter | None,
) -> ControlRuntime:
    """Assemble the workflow-runtime control plane and optional workflow gates."""

    path = Path(workflow_path)
    store = session_store or create_session_store(session_store_kind, store_path)
    service = SessionService(store, emitter=emitter)
    gate_registry = load_gate_registry(path.with_name("gates.py"))
    control = ControlPlane(
        service,
        gate_fn=gate_registry.to_gate_fn() if gate_registry is not None else None,
    )
    return ControlRuntime(store=store, service=service, control=control)
