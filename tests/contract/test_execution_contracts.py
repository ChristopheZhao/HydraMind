"""S2a contract tests: typed cross-layer execution contracts (F7/F8).

These prove the hidden cross-layer contracts removed in PLAN-20260618-001 S2a:

* a conformant ``ToolRunner`` satisfies the *typed* protocol with no duck-typing
  (``runtime_checkable`` isinstance + ``AgentToolLoop`` works with no
  ``getattr``/``TypeError`` fallback);
* provider/harness swap does not change reasoning/subagent attribution, because
  those signals are typed ``InvocationResult`` fields, not ``raw`` keys.
"""

from __future__ import annotations

import inspect

import pytest

import hydramind.orchestration.agent_tools as agent_tools_module
from hydramind.control import ControlPlane, InMemorySessionStore, SessionService
from hydramind.harness.base import InvocationResult, StopReason, ToolCall, ToolResultBlock
from hydramind.orchestration.agent_tools import (
    AgentToolLoop,
    ToolRunner,
    _reasoning_content,
    subagent_tool_origin,
)
from hydramind.tools import (
    ToolContext,
    ToolExecutionMetadata,
    ToolRegistry,
    build_default_tool_registry,
)


def test_tool_registry_satisfies_typed_tool_runner_protocol() -> None:
    """The sole production runner is a structural ``ToolRunner`` — no shims."""

    registry = build_default_tool_registry(context=ToolContext(dry_run=True))
    assert isinstance(registry, ToolRunner)
    # The typed protocol declares the three methods the loop actually calls.
    for method in ("run_tool_calls", "context_for_node", "tool_execution_metadata"):
        assert hasattr(ToolRunner, method)
        assert callable(getattr(ToolRunner, method))


class _ConformantRunner:
    """A minimal runner implementing only the typed ``ToolRunner`` contract."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def context_for_node(self, node_key: str, role: str) -> ToolContext:
        return ToolContext(node_key=node_key, role=role, dry_run=True)

    def tool_execution_metadata(
        self,
        call: ToolCall,
        *,
        context: ToolContext,
    ) -> ToolExecutionMetadata:
        return ToolExecutionMetadata(
            tool_name=call.name,
            dry_run=context.dry_run,
            network_access=context.network_access,
            effect_fingerprint=f"sha256:{call.id}",
            registered=True,
            enabled=True,
            risk_class="read_only",
            side_effect_class="none",
            requires_approval=False,
            approval_present=False,
            node_scoped=False,
            role_scoped=False,
            idempotency_scope="read_only",
        )

    async def run_tool_calls(
        self,
        tool_calls: tuple[ToolCall, ...],
        *,
        context: ToolContext | None = None,
    ) -> tuple[ToolResultBlock, ...]:
        self.calls.extend(tool_calls)
        return tuple(
            ToolResultBlock(tool_call_id=call.id, content="ok", is_error=False)
            for call in tool_calls
        )


def test_minimal_conformant_runner_needs_no_duck_typing() -> None:
    runner = _ConformantRunner()
    assert isinstance(runner, ToolRunner)


def test_agent_tool_loop_has_no_getattr_fallback() -> None:
    """The loop must not reintroduce ``getattr``/``hasattr``/``TypeError`` shims."""

    source = inspect.getsource(agent_tools_module)
    assert "getattr(self.tool_runner" not in source
    assert "hasattr(self.tool_runner" not in source
    assert "except TypeError" not in source


@pytest.mark.asyncio
async def test_agent_tool_loop_drives_conformant_runner_via_typed_calls(tmp_path) -> None:
    """``AgentToolLoop`` drives a typed-only runner end to end with no fallback."""

    del tmp_path
    runner = _ConformantRunner()
    plane = ControlPlane(SessionService(InMemorySessionStore()))

    async def _noop_emit(*_args, **_kwargs) -> None:
        return None

    async def _noop_invoke(**_kwargs) -> InvocationResult:
        raise AssertionError("invoke_model should not be reached in this test")

    loop = AgentToolLoop(
        control=plane,
        tool_runner=runner,
        emit_trace=_noop_emit,
        invoke_model=_noop_invoke,
        max_tool_rounds=1,
    )

    context = loop.tool_context_for("node", "writer")
    assert context.node_key == "node"
    assert context.role == "writer"

    call = ToolCall(id="c1", name="probe", arguments={})
    metadata = loop.tool_execution_metadata(call, context=context)
    assert isinstance(metadata, ToolExecutionMetadata)
    assert metadata.effect_fingerprint == "sha256:c1"

    results = await loop.run_tool_calls(
        (call,),
        node_key="node",
        agent_role="writer",
        context=context,
    )
    assert [block.content for block in results] == ["ok"]
    assert runner.calls == [call]


def test_provider_swap_preserves_reasoning_and_subagent_attribution() -> None:
    """Two backends that surface equivalent typed fields yield identical attribution.

    Reasoning continuity and subagent-origin attribution must depend on typed
    ``InvocationResult`` fields, never on ``raw`` debug payload (F7). Backend A
    and backend B carry *different* ``raw`` payloads but equivalent typed fields,
    so downstream helpers must read identically.
    """

    backend_a = InvocationResult(
        content="answer",
        stop_reason=StopReason.END_TURN,
        reasoning_content="step-by-step",
        subagent_id="child-7",
        subagent_summary="did the work",
        raw={"provider": "deepseek", "reasoning_content": "STALE-DEBUG"},
    )
    backend_b = InvocationResult(
        content="answer",
        stop_reason=StopReason.END_TURN,
        reasoning_content="step-by-step",
        subagent_id="child-7",
        subagent_summary="did the work",
        raw={"provider": "kimi"},
    )

    assert _reasoning_content(backend_a) == _reasoning_content(backend_b) == "step-by-step"
    assert subagent_tool_origin(backend_a, "writer") == subagent_tool_origin(backend_b, "writer")
    assert subagent_tool_origin(backend_a, "writer") == {
        "execution_mode": "subagent",
        "subagent_id": "child-7",
        "subagent_role": "writer",
    }


def test_raw_debug_payload_is_not_load_bearing() -> None:
    """A stale/absent ``raw['reasoning_content']`` must not affect attribution."""

    result = InvocationResult(
        content="answer",
        stop_reason=StopReason.END_TURN,
        reasoning_content=None,
        raw={"reasoning_content": "SHOULD-BE-IGNORED"},
    )
    assert _reasoning_content(result) is None
    assert subagent_tool_origin(result, "writer") is None


def test_registry_metadata_round_trips_through_typed_object(tmp_path) -> None:
    registry = ToolRegistry(default_context=ToolContext(artifact_root=tmp_path, dry_run=True))
    call = ToolCall(id="x", name="missing.tool", arguments={})
    metadata = registry.tool_execution_metadata(call)
    assert isinstance(metadata, ToolExecutionMetadata)
    assert metadata.registered is False
    # Unregistered tools omit the registered-only optional keys in the detail map.
    detail = metadata.as_detail()
    assert "requires_approval" not in detail
    assert detail["registered"] is False
    assert detail["risk_class"] == "unknown"
