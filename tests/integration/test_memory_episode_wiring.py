"""Episodic-memory wiring — opt-in projector records goal-run episodes.

Major #4 regression guard: the memory layer (AgentMemory / EpisodicMemory /
EpisodeProjectorObserver / AgentTurnMemoryObserver) is fully implemented but the
projector must be wired into a runtime path.
``build_goal_runtime_bundle(enable_episodic_memory=True)`` must attach an
:class:`EpisodeProjectorObserver` to the runtime emitter so a compact episode
summary lands in the workflow-scoped store once a goal reaches a terminal state.
``enable_agent_memory=True`` attaches an :class:`AgentTurnMemoryObserver` so each
agent turn is recorded to ``MemoryScope.AGENT`` (DEV-14). With both flags off, no
store is created and nothing is recorded.
"""

from __future__ import annotations

import json

import pytest

from hydramind.control import SessionStatus
from hydramind.memory import MemoryScope
from hydramind.orchestration import GoalSpec
from hydramind.runtime import build_goal_runtime_bundle
from hydramind.testing import MockProvider, ScriptedTurn


def _plan_turn() -> ScriptedTurn:
    """Scripted agent-planner plan JSON (S96: no rule planner provides this)."""
    return ScriptedTurn(
        content=json.dumps({"tasks": [{"key": "work", "role": "executor"}]})
    )


@pytest.mark.asyncio
async def test_enable_episodic_memory_records_episode_on_completion() -> None:
    backend = MockProvider(
        scripted=[_plan_turn(), ScriptedTurn(content='{"done": true}')]
    )
    bundle = build_goal_runtime_bundle(
        provider=backend,
        env_file=None,
        enable_episodic_memory=True,
    )

    session = await bundle.agent.run_goal(
        GoalSpec(objective="Produce a concise status report")
    )

    assert session.status is SessionStatus.COMPLETED
    assert bundle.memory_store is not None

    episodes = await bundle.memory_store.scan(
        MemoryScope.WORKFLOW, "goals", key_prefix="episode."
    )
    assert episodes, "episode projector wrote no episode into the workflow store"

    entry = episodes[-1]
    # The recorded summary should carry trace/execution provenance metadata.
    assert entry.metadata.get("source") == "observability_trace_projection"
    assert "trace_ids" in entry.metadata
    assert "execution_ids" in entry.metadata
    # And the compact summary value reflects this terminal session.
    assert entry.value["session_id"] == session.id
    assert entry.value["terminal_event"] == "session_completed"


@pytest.mark.asyncio
async def test_enable_agent_memory_attaches_agent_turn_observer() -> None:
    # DEV-14: opt-in agent memory wires the AgentTurnMemoryObserver (the
    # MemoryScope.AGENT production write path) onto the runtime emitter, mirroring
    # episodic-memory wiring.
    from hydramind.memory import AgentTurnMemoryObserver

    backend = MockProvider(
        scripted=[_plan_turn(), ScriptedTurn(content='{"done": true}')]
    )
    bundle = build_goal_runtime_bundle(
        provider=backend,
        env_file=None,
        enable_agent_memory=True,
    )

    assert bundle.memory_store is not None
    assert bundle.emitter is not None
    assert any(
        isinstance(observer, AgentTurnMemoryObserver)
        for observer in bundle.emitter.observers()
    )


@pytest.mark.asyncio
async def test_episodic_memory_disabled_records_nothing() -> None:
    backend = MockProvider(
        scripted=[_plan_turn(), ScriptedTurn(content='{"done": true}')]
    )
    bundle = build_goal_runtime_bundle(provider=backend, env_file=None)

    session = await bundle.agent.run_goal(
        GoalSpec(objective="Produce a concise status report")
    )

    assert session.status is SessionStatus.COMPLETED
    assert bundle.memory_store is None
