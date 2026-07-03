"""Prompt and memory-context composition for ``OrchestratorAgent``."""

from __future__ import annotations

import json
from typing import Any

from hydramind.control.models import RuntimeSession, WorkflowBlueprint
from hydramind.orchestration.builtin_prompts import render_default_node_prompt
from hydramind.orchestration.memory_context import (
    MemoryContext,
    MemoryContextPolicy,
    MemoryContextRequest,
    MemoryContextRetriever,
)
from hydramind.orchestration.prompts import PromptLibrary


class AgentPromptContextBuilder:
    """Build node prompts and bounded memory context for agent execution."""

    def __init__(
        self,
        *,
        workflow: WorkflowBlueprint,
        prompts: PromptLibrary,
        memory_retriever: MemoryContextRetriever | None = None,
    ) -> None:
        self._workflow = workflow
        self._prompts = prompts
        self._memory_retriever = memory_retriever

    async def compose_messages(
        self,
        session: RuntimeSession,
        node_key: str,
        agent_role: str,
    ) -> tuple[str, str]:
        memory_context = await self._memory_context_for_node(
            session,
            node_key,
        )
        memory_payload = (
            memory_context.as_prompt_payload() if memory_context is not None else {}
        )
        memory_context_json = json.dumps(
            memory_payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            template = self._prompts.lookup(agent_role)
            variables = dict(session.input_payload)
            variables.setdefault("input", json.dumps(session.input_payload))
            variables["node_key"] = node_key
            variables["workflow_name"] = session.workflow_name
            variables["memory_context"] = memory_context_json
            variables["context"] = upstream_context_json(
                session,
                self._workflow,
                node_key,
            )
            return template.render(**variables)
        except KeyError:
            return render_default_node_prompt(
                agent_role=agent_role,
                node_key=node_key,
                workflow_name=session.workflow_name,
                input_payload=session.input_payload,
                context=execution_context(
                    session,
                    self._workflow,
                    node_key,
                    memory_context=memory_context,
                ),
            )

    async def _memory_context_for_node(
        self,
        session: RuntimeSession,
        node_key: str,
    ) -> MemoryContext | None:
        if self._memory_retriever is None:
            return None
        policy = session_memory_context_policy(session)
        if policy is None or not policy.enabled:
            return None
        return await self._memory_retriever.retrieve(
            MemoryContextRequest(
                policy=policy,
                purpose="executor.node_prompt",
                workflow_name=session.workflow_name,
                session_id=session.id,
                node_key=node_key,
                goal_objective=goal_objective(session),
            )
        )


def upstream_context_json(
    session: RuntimeSession,
    workflow: WorkflowBlueprint,
    node_key: str,
) -> str:
    return json.dumps(
        upstream_context(session, workflow, node_key),
        ensure_ascii=False,
        sort_keys=True,
    )


def upstream_context(
    session: RuntimeSession,
    workflow: WorkflowBlueprint,
    node_key: str,
) -> dict[str, Any]:
    seen: set[str] = set()
    ordered: list[str] = []

    def visit(key: str) -> None:
        for req in workflow.node_spec(key).requires:
            if req in seen:
                continue
            seen.add(req)
            visit(req)
            ordered.append(req)

    visit(node_key)
    nodes: dict[str, Any] = {}
    for key in ordered:
        node = session.nodes.get(key)
        latest = node.latest_attempt() if node is not None else None
        nodes[key] = {
            "status": node.status.value if node is not None else "missing",
            "output": dict(latest.output) if latest is not None else {},
        }
    return {"nodes": nodes} if nodes else {}


def execution_context(
    session: RuntimeSession,
    workflow: WorkflowBlueprint,
    node_key: str,
    *,
    memory_context: MemoryContext | None,
) -> dict[str, Any]:
    context = upstream_context(session, workflow, node_key)
    if memory_context is None or not memory_context.entries:
        return context
    enriched = dict(context)
    enriched["memory"] = memory_context.as_prompt_payload()
    return enriched


def session_memory_context_policy(
    session: RuntimeSession,
) -> MemoryContextPolicy | None:
    raw = session.metadata.get("memory_context")
    if raw is None:
        raw_goal = session.metadata.get("goal")
        if isinstance(raw_goal, dict):
            raw = raw_goal.get("memory_context")
    if raw is None:
        return None
    if isinstance(raw, MemoryContextPolicy):
        return raw
    if isinstance(raw, dict):
        return MemoryContextPolicy.model_validate(raw)
    return None


def goal_objective(session: RuntimeSession) -> str | None:
    raw_goal = session.metadata.get("goal")
    if isinstance(raw_goal, dict):
        objective = raw_goal.get("objective")
        if isinstance(objective, str) and objective:
            return objective
    payload_goal = session.input_payload.get("goal")
    return payload_goal if isinstance(payload_goal, str) and payload_goal else None
