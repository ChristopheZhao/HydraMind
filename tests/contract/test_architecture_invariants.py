"""Architecture invariant tests for production source boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src" / "hydramind"
CLI_ENTRYPOINT = SRC_ROOT / "cli.py"
CLI_DOCTOR = SRC_ROOT / "cli_doctor.py"
CLI_QUEUE = SRC_ROOT / "cli_queue.py"
CLI_WORKER_SUPPORT = SRC_ROOT / "cli_worker_support.py"
CLI_WORKER_OPS = SRC_ROOT / "cli_worker_ops.py"
RUNTIME_ENTRYPOINT = SRC_ROOT / "runtime.py"
RUNTIME_BUNDLE = SRC_ROOT / "runtime_bundle.py"
RUNTIME_EXECUTION = SRC_ROOT / "runtime_execution.py"
RUNTIME_WORKER = SRC_ROOT / "runtime_worker.py"
RUNTIME_WORKER_READINESS = SRC_ROOT / "runtime_worker_readiness.py"
HARNESS_ROOT = SRC_ROOT / "harness"
HARNESS_INIT = HARNESS_ROOT / "__init__.py"
HARNESS_FACTORY = HARNESS_ROOT / "factory.py"
RUNTIME_SUPPORT = SRC_ROOT / "runtime_support.py"
OPENAI_COMPATIBLE = HARNESS_ROOT / "openai_compatible.py"
OPENAI_COMPATIBLE_SUPPORT = HARNESS_ROOT / "openai_compatible_support.py"
HARNESS_PROVIDER = HARNESS_ROOT / "provider.py"
# Replay/test doubles live in hydramind.testing (ADR-0010 / S6). The production
# harness package public API and the production provider factory may NOT import or
# construct the replay provider; replay is testing support, not a runtime provider.
REPLAY_BACKEND_NAMES = {"MockProvider", "ScriptedTurn", "invocation_fingerprint"}
REPLAY_BACKEND_MODULES = {
    "hydramind.harness.mock",
    "hydramind.testing",
    "hydramind.testing.replay",
}
# A class implementing only the ModelProvider contract carries NO harness surface:
# no subagent/compaction methods and no HarnessCapabilities construction (ADR-0010 §F2).
PROVIDER_FORBIDDEN_HARNESS_METHODS = {"spawn_subagent", "compact_context"}
PROVIDER_FORBIDDEN_HARNESS_NAMES = {
    "HarnessCapabilities",
    "HarnessCapability",
}
CONTROL_ROOT = SRC_ROOT / "control"
QUEUE_ROOT = SRC_ROOT / "queue"
REDIS_STREAM = QUEUE_ROOT / "redis_stream.py"
MEMORY_ROOT = SRC_ROOT / "memory"
MEMORY_STORES = MEMORY_ROOT / "stores.py"
ORCHESTRATION_ROOT = SRC_ROOT / "orchestration"
ORCHESTRATION_AGENT = ORCHESTRATION_ROOT / "agent.py"
ORCHESTRATION_AGENT_EXECUTION = ORCHESTRATION_ROOT / "agent_execution.py"
ORCHESTRATION_AGENT_INVOCATION = ORCHESTRATION_ROOT / "agent_invocation.py"
ORCHESTRATION_GOAL_AGENT = ORCHESTRATION_ROOT / "goal_agent.py"
ORCHESTRATION_COLLABORATION = ORCHESTRATION_ROOT / "collaboration.py"
ORCHESTRATION_COLLABORATION_MEMBER = ORCHESTRATION_ROOT / "collaboration_member.py"
ORCHESTRATION_COLLABORATION_TEAM = ORCHESTRATION_ROOT / "collaboration_team.py"
ORCHESTRATION_EXECUTION_HARNESS = ORCHESTRATION_ROOT / "execution_harness.py"
ORCHESTRATION_SUBAGENT_SPAWN = ORCHESTRATION_ROOT / "subagent_spawn.py"
ORCHESTRATION_COLLABORATION_MODULES = tuple(
    sorted(ORCHESTRATION_ROOT.glob("collaboration*.py"))
)
BUILTIN_PROMPTS_ROOT = SRC_ROOT / "orchestration" / "builtin_prompts"

VENDOR_SDK_MODULES = {"anthropic", "openai", "claude_agent_sdk"}
QUEUE_FORBIDDEN_IMPORTS = (
    "hydramind.control",
    "hydramind.orchestration",
    "hydramind.runtime",
    "hydramind.runtime_worker",
)
MEMORY_FORBIDDEN_IMPORTS = (
    "hydramind.control",
    "hydramind.harness",
    "hydramind.observability",
    "hydramind.orchestration",
    "hydramind.queue",
    "hydramind.runtime",
    "hydramind.runtime_worker",
    "hydramind.tools",
)
ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_IMPORT_MODULES = {
    "hydramind.memory.stores",
    "hydramind.runtime_memory",
    "hydramind.runtime_support",
    "sqlite3",
}
ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_IMPORT_NAMES = {
    "InMemoryMemoryStore",
    "MemoryStoreBuilder",
    "SqliteMemoryStore",
    "create_memory_store",
    "register_memory_store",
    "registered_memory_store_kinds",
    "reset_memory_store_registry",
}
ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_CALLS = {
    "create_memory_store",
    "register_memory_store",
    "registered_memory_store_kinds",
    "reset_memory_store_registry",
}
RUNTIME_MEMORY_ASSEMBLY_FORBIDDEN_IMPORT_NAMES = {
    "AgentTurnMemoryObserver",
    "EpisodeProjectorObserver",
    "EpisodicMemory",
    "InMemoryMemoryStore",
    "SqliteMemoryStore",
    "StoreMemoryContextRetriever",
}
RUNTIME_VERIFICATION_ASSEMBLY_FORBIDDEN_IMPORT_NAMES = {
    "ArtifactContainmentVerifierRunner",
    "CompositeVerifierRunner",
    "SemanticArtifactVerifierRunner",
    "TaskContractVerifierRunner",
}
RUNTIME_TOOL_ASSEMBLY_FORBIDDEN_IMPORT_NAMES = {
    "ExecutionEnvironment",
    "WorkflowToolProvider",
    "build_default_tool_registry",
    "default_external_tool_hosts",
}
RUNTIME_CONTROL_ASSEMBLY_FORBIDDEN_IMPORT_NAMES = {
    "GateRegistry",
    "VerifierFeedbackEvaluator",
}
RUNTIME_CONTROL_ASSEMBLY_FORBIDDEN_CALLS = {
    "ControlPlane",
    "GateRegistry",
    "SessionService",
    "VerifierFeedbackEvaluator",
}
RUNTIME_BUNDLE_ASSEMBLY_FORBIDDEN_IMPORT_NAMES = {
    "GoalDrivenOrchestratorAgent",
    "MemoryContextRetriever",
    "ModelGoalPlanner",
    "OrchestratorAgent",
    "PlannerProvider",
    "PromptLibrary",
    "build_default_goal_verifier_runner",
    "build_goal_control_runtime",
    "build_goal_memory_runtime",
    "build_goal_tool_runtime",
    "build_workflow_control_runtime",
    "build_workflow_tool_runtime",
}
RUNTIME_BUNDLE_ASSEMBLY_FORBIDDEN_CALLS = {
    "GoalDrivenOrchestratorAgent",
    "ModelGoalPlanner",
    "OrchestratorAgent",
    "PromptLibrary",
    "build_default_goal_verifier_runner",
    "build_goal_control_runtime",
    "build_goal_memory_runtime",
    "build_goal_tool_runtime",
    "build_workflow_control_runtime",
    "build_workflow_tool_runtime",
}
RUNTIME_EXECUTION_FACADE_FORBIDDEN_IMPORT_NAMES = {
    "_coerce_goal_spec",
    "runtime_queue",
}
CLI_RUNTIME_EXECUTION_IMPORTS = {
    "create_queued_goal_session",
    "create_queued_session",
    "run_goal",
    "run_queued_goal_session_once",
    "run_queued_session_once",
    "run_workflow_file",
}
RUNTIME_SUPPORT_FORBIDDEN_DEFS = {
    "WorkflowToolProvider",
    "_WorkflowToolProvider",
    "_node_from_dict",
    "_normalize_tool_names",
    "_tools_from_node_config",
    "_workflow_node_declared_tools",
    "create_session_store",
    "load_env_file",
    "load_gate_registry",
    "load_workflow_blueprint",
}
RUNTIME_SUPPORT_FORBIDDEN_IMPORTS = {
    "importlib.util",
    "yaml",
}
RUNTIME_QUEUE_FORBIDDEN_DEFS = {
    "_coerce_int",
    "_runtime_overrides_from_args",
}
RUNTIME_QUEUE_FORBIDDEN_IMPORT_NAMES = {
    "InMemoryQueueAdapter",
    "QueueExecutionHost",
}
RUNTIME_QUEUE_GOAL_FORBIDDEN_DEFS = {
    "_build_goal_session_orchestrator",
    "_persisted_runtime_overrides",
    "_QueuedGoalSessionOrchestrator",
    "coerce_int",
    "coerce_path",
    "coerce_str",
    "runtime_overrides_from_args",
}
RUNTIME_QUEUE_GOAL_FORBIDDEN_IMPORT_NAMES = {
    "SessionService",
    "create_session_store",
    "load_env_file",
}
RUNTIME_WORKER_READINESS_FORBIDDEN_DEFS = {
    "WorkerReadinessSnapshot",
    "_SessionStoreReadiness",
    "_queue_distribution",
    "_readiness_reasons",
    "_session_store_readiness",
    "worker_readiness",
}
RUNTIME_WORKER_READINESS_FORBIDDEN_IMPORT_NAMES = {
    "dataclass",
}
WORKER_HEALTH_READ_ONLY_FORBIDDEN_CALLS = {
    "ack",
    "dequeue",
    "nack",
    "run_loop",
    "run_once",
    "run_session",
}
WORKER_READINESS_READ_ONLY_FORBIDDEN_IMPORT_MODULES = {
    "hydramind.control",
    "hydramind.control.store",
    "hydramind.runtime_control",
    "hydramind.runtime_support",
}
WORKER_READINESS_READ_ONLY_FORBIDDEN_IMPORT_NAMES = {
    "ControlRuntime",
    "InMemorySessionStore",
    "QueueExecutionHost",
    "SessionService",
    "SessionStore",
    "SqliteSessionStore",
    "create_session_store",
}
WORKER_READINESS_READ_ONLY_FORBIDDEN_CALLS = {
    "QueueExecutionHost",
    "ack",
    "close",
    "create_session_store",
    "dead_letters",
    "dequeue",
    "get",
    "in_flight",
    "nack",
    "pending",
    "put",
    "run_loop",
    "run_once",
    "run_session",
}
CONTROL_PLANE_APPLY_FORBIDDEN_DEFS = {
    "_authorization_actor",
    "_coerce_apply_intent",
    "_ensure_session_running",
    "_intent_evidence",
    "_intent_from_legacy_payload",
    "_release_report_execution_lease_if_present",
    "_required_gate",
    "_required_node_key",
    "_resume_session_if_waiting",
}
CONTROL_PLANE_REPORT = CONTROL_ROOT / "control_reports.py"
CONTROL_GATE_DECISION = CONTROL_ROOT / "control_gate_decisions.py"
CONTROL_SESSION_PERSISTENCE = CONTROL_ROOT / "session_persistence.py"
CONTROL_PLANE_REPORT_FORBIDDEN_DEFS = {
    "ApplyDeriver",
    "GateFn",
    "_assert_report_execution_lease",
    "_has_execution_lease_metadata",
    "default_apply_deriver",
}
CONTROL_PLANE_GATE_DECISION_FORBIDDEN_CALLS = {
    "apply_gate_decision",
    "get_node",
    "requeue",
    "complete",
    "fail",
}
CONTROL_PLANE_GATE_DECISION_FORBIDDEN_NAMES = {
    "NodeStatus",
}
CONTROL_REPORT_FORBIDDEN_IMPORTS = {
    "hydramind.harness",
    "hydramind.memory",
    "hydramind.observability",
    "hydramind.orchestration",
    "hydramind.queue",
    "hydramind.runtime",
    "hydramind.runtime_worker",
    "hydramind.tools",
}
SESSION_SERVICE_NODE_LIFECYCLE_FORBIDDEN_DEFS = {
    "abort_and_requeue_node",
    "abort_running_attempt",
    "complete_running_attempt",
    "fail_running_attempt",
    "recover_expired_node_execution_leases",
}
SESSION_SERVICE_SESSION_LIFECYCLE_FORBIDDEN_DEFS = {
    "_session_correlation",
}
SESSION_SERVICE_SESSION_LIFECYCLE_FORBIDDEN_CALLS = {
    "is_valid_session_transition",
}
SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_DEFS = {
    "_emit",
    "_node_correlation",
}
SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_ASSIGNMENTS = {
    "_NODE_STATUS_TO_EVENT_KIND",
    "_SESSION_STATUS_TO_EVENT_KIND",
}
SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_IMPORT_NAMES = {
    "ObservationEvent",
    "ObservationEventKind",
}
SESSION_OBSERVABILITY_REPORTER_FORBIDDEN_IMPORT_MODULES = {
    "hydramind.control.session_service",
    "hydramind.control.store",
}
SESSION_OBSERVABILITY_REPORTER_FORBIDDEN_IMPORT_NAMES = {
    "SessionService",
    "SessionStore",
}
SESSION_PERSISTENCE_FORBIDDEN_IMPORTS = {
    "hydramind.harness",
    "hydramind.memory",
    "hydramind.observability",
    "hydramind.orchestration",
    "hydramind.queue",
    "hydramind.runtime",
    "hydramind.runtime_worker",
    "hydramind.tools",
}
COLLABORATION_INTERACTION_LOG_FORBIDDEN_IMPORT_MODULES = {
    "hydramind.control.session_service",
    "hydramind.control.store",
}
COLLABORATION_INTERACTION_LOG_FORBIDDEN_IMPORT_NAMES = {
    "RuntimeSession",
    "SessionService",
    "SessionStore",
}
PLANNING_PROMPT_DIAGNOSTIC_FORBIDDEN_DEFS = {
    "_append_diagnostic_phase",
    "_finished_diagnostics",
    "_initial_plan_prompt",
    "_planner_diagnostics",
    "_planner_error_summary",
    "_planner_json_repair_prompt",
    "_revise_plan_prompt",
    "_with_delta_diagnostics",
    "_with_planner_diagnostics",
}
PLANNING_PROMPT_DIAGNOSTIC_FORBIDDEN_ASSIGNMENTS = {
    "_INITIAL_PLAN_RESPONSE_SHAPE",
    "_PLAN_DELTA_RESPONSE_SHAPE",
    "_PLANNER_SYSTEM",
}
PLANNING_INVOCATION_FORBIDDEN_DEFS = {
    "_invoke_harness",
    "_invoke_planner_json",
    "_repair_planner_json",
}
RULE_PLANNER_NAMES = {
    "FallbackGoalPlanner",
    "StaticGoalPlanner",
}
RULE_VERIFY_REPAIR_NAMES = {
    "ContentQualityVerifierRunner",
    "VerifierFeedbackRepairPolicy",
}
# S102 dead-contract removals: the legacy control payload + the generic rule-engine
# gate evaluator. Neither may re-accumulate in src/ (ADR-0004/0008).
REMOVED_DEAD_CONTRACT_NAMES = {
    "ApplyPayload",
    "PolicyEvaluator",
}
GOAL_FEEDBACK_FORBIDDEN_DEFS = {
    "FeedbackRepairPolicy",
    "VerifierFeedbackRepairPolicy",
    "_dedupe",
    "_extend_failed_verifiers",
    "_extend_feedback",
    "_extend_feedback_records",
    "_feedback_for_replan",
    "_gate_has_failed_verifiers",
    "_gate_has_only_required_tool_feedback",
    "_should_approve_current_after_replan",
}
GOAL_SESSION_STATE_FORBIDDEN_DEFS = {
    "_plan_for_session",
}
GOAL_SESSION_STATE_FORBIDDEN_NAMES = {
    "WorkflowRevision",
}
GOAL_SESSION_STATE_FORBIDDEN_CALLS = {
    "WorkflowRevision",
    "apply_workflow_revision",
    "as_session_metadata",
    "model_validate",
}
GOAL_REPAIR_RUNTIME_FORBIDDEN_DEFS = {
    "_approve_or_replan_decision",
    "_gate_for_decision",
    "_maybe_auto_repair",
}
GOAL_REPAIR_RUNTIME_FORBIDDEN_NAMES = {
    "DecisionAction",
    "Gate",
    "RuntimeDecisionKind",
    "_feedback_for_replan",
    "_gate_has_failed_verifiers",
    "_should_approve_current_after_replan",
    "feedback_for_replan",
    "gate_has_failed_verifiers",
    "should_approve_current_after_replan",
}
GOAL_AGENT_FACTORY_FORBIDDEN_DEFS = {
    "_PlanToolProvider",
    "_agent_for_plan",
}
GOAL_AGENT_FACTORY_FORBIDDEN_NAMES = {
    "OrchestratorAgent",
    "PlanScopedToolProvider",
    "ToolSpec",
}
COLLABORATION_TEAM_FORBIDDEN_DEFS = {
    "_run_team_member",
    "_team_detail",
    "_team_origin",
    "_team_spec_from_config",
    "_tools_for_agent",
}
COLLABORATION_INTERACTION_FORBIDDEN_DEFS = {
    "_aggregate_results",
    "_interaction_to_harness",
    "_member_vote",
    "_team_detail",
    "_team_origin",
    "_vote_aggregation",
    "aggregate_results",
    "harness_message_from_interaction",
    "member_vote",
    "team_collaboration_payload",
    "team_detail",
    "team_invocation_result",
    "team_member_content",
    "team_origin",
    "team_turn_detail",
    "vote_aggregation",
}
COLLABORATION_MEMBER_RUNTIME_FORBIDDEN_DEFS = {
    "_run_team_member",
    "_tool_call_names",
    "_tools_for_agent",
    "member_parent_metadata",
    "member_result_payload",
    "tool_call_names",
    "tools_for_agent",
}
COLLABORATION_TEAM_EVENT_FORBIDDEN_NAMES = {
    "ObservationEventKind",
    "compact_text",
}
COLLABORATION_TEAM_RUNTIME_FORBIDDEN_NAMES = {
    "Interaction",
    "InteractionMessage",
    "InteractionMessageRole",
    "InteractionStatus",
    "InteractionTurn",
    "InteractionTurnStatus",
    "harness_message_from_interaction",
    "select_strategy",
    "team_turn_detail",
}
CLI_DOCTOR_TOOLS_FORBIDDEN_DEFS = {
    "_doctor_result_content",
    "_doctor_tool_args",
    "_doctor_tools",
    "_missing_live_tool_env",
    "_redact_media_payload",
}
CLI_DOCTOR_GOAL_SCENARIO_FORBIDDEN_DEFS = {
    "_doctor_goal_scenario",
    "_goal_scenario_evidence",
    "_goal_scenario_objective",
}
CLI_RUNTIME_WORKER_OPS_FORBIDDEN_DEFS = {
    "_queue_messages_payload",
    "_run_worker_dead_letters",
    "_run_worker_health",
    "_run_worker_readiness",
    "_worker_dead_letters_limit_error",
    "_worker_dead_letters_source_error",
    "_worker_health_source_error",
    "_worker_readiness_source_error",
    "_worker_readiness_store_error",
}
CLI_WORKER_OPS_FORBIDDEN_IMPORTS = {
    "hydramind.control",
    "hydramind.harness",
    "hydramind.memory",
    "hydramind.observability",
    "hydramind.orchestration",
    "hydramind.tools",
}
CLI_PARSER_FORBIDDEN_DEFS = {
    "_add_queue_arguments",
    "_build_parser",
    "build_parser",
}
CLI_PARSER_FORBIDDEN_CALLS = {
    "ArgumentParser",
    "add_argument",
    "add_parser",
    "add_subparsers",
}
CLI_QUEUE_HELPER_FORBIDDEN_DEFS = {
    "_build_cli_queue_adapter",
    "_enqueue_queue_error",
    "_queue_config_error",
    "_queue_messages_payload",
    "_queue_publish_payload",
    "build_cli_queue_adapter",
    "enqueue_queue_error",
    "queue_config_error",
    "queue_messages_payload",
    "queue_publish_payload",
}
CLI_QUEUE_HELPER_FORBIDDEN_IMPORT_NAMES = {
    "QueueAdapter",
    "QueueMessage",
    "create_queue_adapter",
}
CLI_WORKER_SUPPORT_FORBIDDEN_DEFS = {
    "_SignalStopper",
    "_run_goal_worker_daemon",
    "_run_goal_worker_loop",
    "_run_worker_daemon",
    "_run_worker_loop",
    "_worker_daemon_bound_error",
    "_worker_daemon_source_error",
    "_worker_loop_bound_error",
    "_worker_loop_source_error",
    "SignalStopper",
    "run_goal_worker_daemon",
    "run_goal_worker_loop",
    "run_worker_daemon",
    "run_worker_loop",
    "worker_daemon_bound_error",
    "worker_daemon_source_error",
    "worker_loop_bound_error",
    "worker_loop_source_error",
}
CLI_WORKER_SUPPORT_FORBIDDEN_IMPORT_NAMES = {
    "Callable",
    "FrameType",
    "WorkerLoopResult",
    "run_queued_goal_session_loop",
    "run_queued_session_loop",
}
CLI_WORKER_SUPPORT_FORBIDDEN_IMPORT_MODULES = {
    "signal",
}
CLI_WORKER_SUPPORT_LAYER_FORBIDDEN_IMPORTS = {
    "hydramind.control",
    "hydramind.harness",
    "hydramind.memory",
    "hydramind.orchestration",
    "hydramind.tools",
}
REDIS_STREAM_SUPPORT_FORBIDDEN_DEFS = {
    "_claimed_entries",
    "_first_stream_entry",
    "_join_handle",
    "_loads_metadata",
    "_message_fields",
    "_message_from_fields",
    "_next_delivery_metadata",
    "_next_replay_count",
    "_replay_metadata",
    "_split_handle",
    "_stream_entries",
    "_stream_response",
    "_to_text",
    "_validate_positive_limit",
}
AGENT_EXECUTION_SUPPORT_FORBIDDEN_DEFS = {
    "_emit_trace",
    "_heartbeat_execution_lease_until_stopped",
    "_invoke_for_node_with_lease_heartbeat",
    "_invoke_model",
    "_invoke_subagent",
    "_lease_heartbeat_interval_seconds",
    "_new_trace_id",
}
AGENT_NODE_INVOCATION_FORBIDDEN_DEFS = {
    "_NoToolProvider",
    "_dispatch_direct",
    "_dispatch_subagent",
    "_dispatch_team",
    "default_report_builder",
}
AGENT_NODE_INVOCATION_FORBIDDEN_IMPORTS = {
    "hydramind.control.session_service",
    "hydramind.control.store",
    "hydramind.queue",
    "hydramind.runtime",
    "hydramind.runtime_worker",
}
AGENT_NODE_INVOCATION_FORBIDDEN_CALLS = {
    "apply_decision",
    "open_runtime_decision",
    "record_session_complete",
}
OPENAI_COMPATIBLE_SUPPORT_FORBIDDEN_DEFS = {
    "_chat_completions_url",
    "_map_stop_reason",
    "_parse_completion",
    "_parse_tool_call",
    "_parse_usage",
    "_provider_tool_name",
    "_provider_tool_name_map",
    "_reasoning_content",
    "_reverse_tool_name_map",
    "_to_openai_messages",
    "_tool_to_openai",
}
OPENAI_COMPATIBLE_SUPPORT_FORBIDDEN_IMPORTS = {
    "hydramind.control",
    "hydramind.harness.routing",
    "hydramind.memory",
    "hydramind.observability",
    "hydramind.orchestration",
    "hydramind.queue",
    "hydramind.runtime",
    "hydramind.runtime_worker",
    "hydramind.tools",
}
RUNTIME_STATE_ATTRS = {
    "error_message",
    "nodes",
    "status",
    "summary_output",
    "updated_at",
}
RUNTIME_STATE_NAMES = {"attempt", "gate", "node", "session"}


def _source_files() -> list[Path]:
    return sorted(path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _is_under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def test_legacy_harness_backend_token_absent_from_src_python() -> None:
    """N3 invariant: the legacy production token must not exist in src/*.py."""

    token = "HarnessBackend"
    hits: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if token in line:
                hits.append(f"{_relative(path)}:{lineno}: {line.strip()}")

    assert hits == []


def test_vendor_sdk_imports_do_not_escape_harness_layer() -> None:
    violations: list[str] = []
    for path in _source_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names.append(node.module)
            for module in module_names:
                root_name = module.split(".", 1)[0]
                if root_name in VENDOR_SDK_MODULES and not _is_under(path, HARNESS_ROOT):
                    violations.append(f"{_relative(path)}:{node.lineno} imports {module}")

    assert violations == []


def test_runtime_session_mutations_stay_in_control_layer() -> None:
    violations: list[str] = []
    for path in _source_files():
        if _is_under(path, CONTROL_ROOT):
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if _mutates_runtime_state(target):
                        violations.append(f"{_relative(path)}:{node.lineno}")
            if isinstance(node, ast.Call) and _updates_runtime_state_copy(node):
                violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == []


def test_queue_layer_does_not_import_runtime_state_or_control() -> None:
    violations: list[str] = []
    for path in _source_files():
        if not _is_under(path, QUEUE_ROOT):
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if _is_forbidden_queue_import(module):
                    violations.append(f"{_relative(path)}:{node.lineno} imports {module}")

    assert violations == []


def test_memory_stores_stay_storage_local() -> None:
    tree = _parse(MEMORY_STORES)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if _is_forbidden_memory_import(module):
                violations.append(f"{_relative(MEMORY_STORES)}:{node.lineno} imports {module}")

    assert violations == []


def test_orchestration_memory_context_stays_storage_neutral() -> None:
    violations: list[str] = []
    for path in sorted(ORCHESTRATION_ROOT.glob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if module in ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_IMPORT_MODULES:
                    violations.append(f"{_relative(path)}:{node.lineno} imports {module}")
            for name in _imported_names(node):
                if name in ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_IMPORT_NAMES:
                    violations.append(f"{_relative(path)}:{node.lineno} imports {name}")
            if isinstance(node, ast.Call):
                called = _called_name(node.func)
                if called in ORCHESTRATION_MEMORY_STORAGE_FORBIDDEN_CALLS:
                    violations.append(f"{_relative(path)}:{node.lineno} calls {called}")

    assert violations == []


def test_runtime_entrypoint_delegates_memory_assembly() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_MEMORY_ASSEMBLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_entrypoint_delegates_verification_assembly() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_VERIFICATION_ASSEMBLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_entrypoint_delegates_tool_assembly() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_TOOL_ASSEMBLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_entrypoint_delegates_control_assembly() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_CONTROL_ASSEMBLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in RUNTIME_CONTROL_ASSEMBLY_FORBIDDEN_CALLS:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} calls {called}")

    assert violations == []


def test_runtime_entrypoint_delegates_bundle_assembly() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_BUNDLE_ASSEMBLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in RUNTIME_BUNDLE_ASSEMBLY_FORBIDDEN_CALLS:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} calls {called}")

    assert violations == []


def test_runtime_bundle_does_not_import_runtime_facade() -> None:
    tree = _parse(RUNTIME_BUNDLE)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if module == "hydramind.runtime":
                violations.append(f"{_relative(RUNTIME_BUNDLE)}:{node.lineno} imports {module}")

    assert violations == []


def test_runtime_entrypoint_delegates_execution_facade() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in RUNTIME_EXECUTION_FACADE_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_execution_does_not_import_runtime_facade() -> None:
    tree = _parse(RUNTIME_EXECUTION)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if module == "hydramind.runtime":
                violations.append(f"{_relative(RUNTIME_EXECUTION)}:{node.lineno} imports {module}")

    assert violations == []


def test_role_prompts_are_config_or_builtin_prompts() -> None:
    violations: list[str] = []
    for path in _source_files():
        if _is_under(path, BUILTIN_PROMPTS_ROOT):
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "You are" in node.value:
                    violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == []


def test_no_subagent_group_in_source() -> None:
    """The legacy ``subagent_group`` collaboration path is fully retired (DEV-10/28).

    Zero-occurrence guard: the string must appear nowhere in ``src/`` — not as a
    string literal, identifier, attribute, or comment — so the retired path
    cannot silently re-accumulate.
    """

    violations: list[str] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            if "subagent_group" in line:
                violations.append(f"{_relative(path)}:{lineno}")

    assert violations == []


def test_node_dispatch_table_covers_execution_mode_enum() -> None:
    """Lock-step: the node dispatch table's keys == the NodeExecutionMode enum (DEV-09).

    Guarantees the advertised surface, the enum, and the typed dispatch table can
    never silently diverge — adding an enum member without a handler (or vice
    versa) fails here.
    """

    from hydramind.control import (
        ControlPlane,
        InMemorySessionStore,
        SessionService,
        WorkflowBlueprint,
        WorkflowNodeSpec,
    )
    from hydramind.orchestration import NodeExecutionMode, OrchestratorAgent
    from hydramind.testing import MockProvider

    blueprint = WorkflowBlueprint(
        name="dispatch-probe",
        version="1",
        nodes=(WorkflowNodeSpec(key="only", role="executor"),),
    )
    agent = OrchestratorAgent(
        provider=MockProvider(),
        control=ControlPlane(SessionService(InMemorySessionStore())),
        workflow=blueprint,
    )
    table = agent._dispatch_table()

    assert set(table) == set(NodeExecutionMode)


def test_invoke_for_node_has_no_execution_mode_string_ladder() -> None:
    """``_invoke_for_node`` must dispatch via the typed table, not raw string ``==`` (DEV-09)."""

    tree = _parse(ORCHESTRATION_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if isinstance(left, ast.Name) and left.id == "execution_mode":
            violations.append(f"{_relative(ORCHESTRATION_AGENT)}:{node.lineno}")
        for comparator in node.comparators:
            if isinstance(comparator, ast.Name) and comparator.id == "execution_mode":
                violations.append(f"{_relative(ORCHESTRATION_AGENT)}:{node.lineno}")

    assert violations == []


def test_cli_entrypoint_delegates_doctor_implementation() -> None:
    tree = _parse(CLI_ENTRYPOINT)
    forbidden_defs = {
        "_doctor_env",
        "_doctor_goal_scenario",
        "_doctor_providers",
        "_doctor_tool_loop",
        "_doctor_tools",
        "_SelectedToolProvider",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in forbidden_defs:
                violations.append(f"{_relative(CLI_ENTRYPOINT)}:{node.lineno} {node.name}")

    assert violations == []


def test_cli_doctor_delegates_tool_diagnostics_helpers() -> None:
    tree = _parse(CLI_DOCTOR)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CLI_DOCTOR_TOOLS_FORBIDDEN_DEFS:
                violations.append(f"{_relative(CLI_DOCTOR)}:{node.lineno} {node.name}")

    assert violations == []


def test_cli_doctor_delegates_goal_scenario_helpers() -> None:
    tree = _parse(CLI_DOCTOR)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CLI_DOCTOR_GOAL_SCENARIO_FORBIDDEN_DEFS:
                violations.append(f"{_relative(CLI_DOCTOR)}:{node.lineno} {node.name}")

    assert violations == []


def test_cli_runtime_delegates_worker_operator_helpers() -> None:
    cli_runtime = SRC_ROOT / "cli_runtime.py"
    tree = _parse(cli_runtime)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CLI_RUNTIME_WORKER_OPS_FORBIDDEN_DEFS:
                violations.append(f"{_relative(cli_runtime)}:{node.lineno} {node.name}")

    assert violations == []


def test_cli_worker_ops_stays_cli_runtime_edge() -> None:
    tree = _parse(CLI_WORKER_OPS)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in CLI_WORKER_OPS_FORBIDDEN_IMPORTS
            ):
                violations.append(f"{_relative(CLI_WORKER_OPS)}:{node.lineno} {module}")

    assert violations == []


def test_cli_queue_stays_cli_runtime_edge() -> None:
    tree = _parse(CLI_QUEUE)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in CLI_WORKER_OPS_FORBIDDEN_IMPORTS
            ):
                violations.append(f"{_relative(CLI_QUEUE)}:{node.lineno} {module}")

    assert violations == []


def test_cli_command_modules_delegate_queue_helpers() -> None:
    violations: list[str] = []
    for path in (SRC_ROOT / "cli_runtime.py", CLI_WORKER_OPS):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in CLI_QUEUE_HELPER_FORBIDDEN_DEFS:
                    violations.append(f"{_relative(path)}:{node.lineno} {node.name}")
            for name in _imported_names(node):
                if name in CLI_QUEUE_HELPER_FORBIDDEN_IMPORT_NAMES:
                    violations.append(f"{_relative(path)}:{node.lineno} imports {name}")

    assert violations == []


def test_cli_runtime_delegates_worker_support_helpers() -> None:
    cli_runtime = SRC_ROOT / "cli_runtime.py"
    tree = _parse(cli_runtime)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CLI_WORKER_SUPPORT_FORBIDDEN_DEFS:
                violations.append(f"{_relative(cli_runtime)}:{node.lineno} {node.name}")
        for name in _imported_names(node):
            if name in CLI_WORKER_SUPPORT_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(cli_runtime)}:{node.lineno} imports {name}")
        for module in _imported_modules(node):
            if module in CLI_WORKER_SUPPORT_FORBIDDEN_IMPORT_MODULES:
                violations.append(f"{_relative(cli_runtime)}:{node.lineno} imports {module}")

    assert violations == []


def test_cli_worker_support_stays_cli_runtime_edge() -> None:
    tree = _parse(CLI_WORKER_SUPPORT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in CLI_WORKER_SUPPORT_LAYER_FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"{_relative(CLI_WORKER_SUPPORT)}:{node.lineno} {module}"
                )

    assert violations == []


def test_cli_entrypoint_delegates_parser_construction() -> None:
    tree = _parse(CLI_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CLI_PARSER_FORBIDDEN_DEFS:
                violations.append(f"{_relative(CLI_ENTRYPOINT)}:{node.lineno} {node.name}")
        for module in _imported_modules(node):
            if module == "argparse":
                violations.append(f"{_relative(CLI_ENTRYPOINT)}:{node.lineno} imports {module}")
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in CLI_PARSER_FORBIDDEN_CALLS:
                violations.append(
                    f"{_relative(CLI_ENTRYPOINT)}:{node.lineno} calls {called}"
                )

    assert violations == []


def test_redis_stream_delegates_serialization_and_metadata_helpers() -> None:
    tree = _parse(REDIS_STREAM)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in REDIS_STREAM_SUPPORT_FORBIDDEN_DEFS:
                violations.append(f"{_relative(REDIS_STREAM)}:{node.lineno} {node.name}")

    assert violations == []


def test_orchestrator_agent_delegates_execution_support_helpers() -> None:
    tree = _parse(ORCHESTRATION_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in AGENT_EXECUTION_SUPPORT_FORBIDDEN_DEFS:
                violations.append(f"{_relative(ORCHESTRATION_AGENT)}:{node.lineno} {node.name}")

    assert violations == []


def test_orchestrator_agent_delegates_node_invocation_helpers() -> None:
    tree = _parse(ORCHESTRATION_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in AGENT_NODE_INVOCATION_FORBIDDEN_DEFS:
                violations.append(f"{_relative(ORCHESTRATION_AGENT)}:{node.lineno} {node.name}")

    assert violations == []


def test_agent_invocation_helper_stays_orchestration_local() -> None:
    tree = _parse(ORCHESTRATION_AGENT_INVOCATION)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in AGENT_NODE_INVOCATION_FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"{_relative(ORCHESTRATION_AGENT_INVOCATION)}:{node.lineno} {module}"
                )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in AGENT_NODE_INVOCATION_FORBIDDEN_CALLS:
                violations.append(
                    f"{_relative(ORCHESTRATION_AGENT_INVOCATION)}:{node.lineno} calls {called}"
                )

    assert violations == []


def test_openai_compatible_delegates_wire_payload_helpers() -> None:
    tree = _parse(OPENAI_COMPATIBLE)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in OPENAI_COMPATIBLE_SUPPORT_FORBIDDEN_DEFS:
                violations.append(f"{_relative(OPENAI_COMPATIBLE)}:{node.lineno} {node.name}")

    assert violations == []


def test_openai_compatible_support_stays_wire_local() -> None:
    tree = _parse(OPENAI_COMPATIBLE_SUPPORT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in OPENAI_COMPATIBLE_SUPPORT_FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"{_relative(OPENAI_COMPATIBLE_SUPPORT)}:{node.lineno} imports {module}"
                )

    assert violations == []


def test_provider_classes_carry_no_harness_surface() -> None:
    """A class implementing the ModelProvider contract must expose no harness
    surface (ADR-0010 §F2): no ``spawn_subagent``/``compact_context`` methods and
    no ``HarnessCapabilities``/``HarnessCapability`` construction.

    AST-based, consistent with the existing invariant style. Scans every class in
    the harness layer that names ``ModelProvider``/``LLMProvider`` as a base.
    """

    violations: list[str] = []
    for path in sorted(HARNESS_ROOT.glob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {_called_name(base) for base in node.bases}
            if not base_names & {"ModelProvider", "LLMProvider"}:
                continue
            for item in ast.walk(node):
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name in PROVIDER_FORBIDDEN_HARNESS_METHODS
                ):
                    violations.append(
                        f"{_relative(path)}:{item.lineno} provider defines {item.name}"
                    )
                if (
                    isinstance(item, ast.Call)
                    and _called_name(item.func) in PROVIDER_FORBIDDEN_HARNESS_NAMES
                ):
                    violations.append(
                        f"{_relative(path)}:{item.lineno} provider constructs "
                        f"{_called_name(item.func)}"
                    )

    assert violations == []


def test_provider_module_has_no_harness_capability_surface() -> None:
    """The ModelProvider contract module must not reference harness capabilities,
    subagent, or compaction surfaces at all (ADR-0010 §F2 / Phase 1)."""

    tree = _parse(HARNESS_PROVIDER)
    forbidden = (
        PROVIDER_FORBIDDEN_HARNESS_NAMES
        | PROVIDER_FORBIDDEN_HARNESS_METHODS
        | {"SubagentHandle", "SubagentContext", "CompactionResult"}
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in forbidden:
                violations.append(f"{_relative(HARNESS_PROVIDER)}:{node.lineno} imports {name}")
        if isinstance(node, ast.Name) and node.id in forbidden:
            violations.append(f"{_relative(HARNESS_PROVIDER)}:{node.lineno} references {node.id}")
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in PROVIDER_FORBIDDEN_HARNESS_METHODS
        ):
            violations.append(f"{_relative(HARNESS_PROVIDER)}:{node.lineno} defines {node.name}")

    assert violations == []


def test_orchestration_compaction_policy_uses_execution_harness_runtime() -> None:
    """Compaction stays homed on the harness surface; spawn is NOT a harness call.

    Orchestration helpers must not read provider capabilities directly. The
    in-episode ``compact_context`` policy, when invoked, is called on the
    ``execution_harness`` surface (harness-homed, PLAN-20260621-001 M2). Per
    ADR-0012 the ``spawn`` ACT is orchestration-owned, so any ``spawn`` call must
    target a ``subagent_spawner`` owner — never the harness surface — and the
    legacy ``spawn_subagent`` harness method must not be called at all.
    """

    paths = (
        ORCHESTRATION_AGENT_EXECUTION,
        ORCHESTRATION_COLLABORATION,
        ORCHESTRATION_COLLABORATION_TEAM,
        ORCHESTRATION_COLLABORATION_MEMBER,
    )
    violations: list[str] = []
    forbidden_capability_names = {"HarnessCapabilities", "HarnessCapability"}
    for path in paths:
        tree = _parse(path)
        for node in ast.walk(tree):
            for name in _imported_names(node):
                if name in forbidden_capability_names:
                    violations.append(f"{_relative(path)}:{node.lineno} imports {name}")
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            attr = node.func.attr
            owner = ast.unparse(node.func.value)
            if attr == "spawn_subagent":
                violations.append(
                    f"{_relative(path)}:{node.lineno} calls legacy harness "
                    f"{owner}.spawn_subagent"
                )
            elif attr == "compact_context" and "execution_harness" not in owner:
                violations.append(
                    f"{_relative(path)}:{node.lineno} calls {owner}.compact_context"
                )
            elif attr == "spawn" and "spawner" not in owner:
                violations.append(
                    f"{_relative(path)}:{node.lineno} calls {owner}.spawn "
                    "(spawn act must target an orchestration spawner)"
                )

    assert violations == []


def test_spawn_is_orchestration_owned_not_on_harness_surface() -> None:
    """ADR-0012 narrow harness: the data-plane harness never spawns.

    Pins both halves of the boundary: (1) the harness-surface types
    ``ExecutionHarnessRuntime`` (Protocol) and ``ProviderExecutionHarnessRuntime``
    expose NO spawn/instantiation method; (2) the orchestration-owned
    ``SubagentSpawner`` owns the ``spawn`` act. Checked at source (AST) and by
    runtime introspection so the boundary cannot silently regress.
    """

    # (1) AST: no method whose name contains "spawn" on either harness-surface class.
    harness_tree = _parse(ORCHESTRATION_EXECUTION_HARNESS)
    harness_surface = {"ExecutionHarnessRuntime", "ProviderExecutionHarnessRuntime"}
    surface_violations = [
        f"{node.name}.{item.name}"
        for node in harness_tree.body
        if isinstance(node, ast.ClassDef) and node.name in harness_surface
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "spawn" in item.name
    ]
    assert surface_violations == [], (
        f"harness surface must not spawn (ADR-0012): {surface_violations}"
    )

    # (2) AST: the orchestration spawner owns a `spawn` act.
    spawn_tree = _parse(ORCHESTRATION_SUBAGENT_SPAWN)
    spawner_methods = {
        item.name
        for node in spawn_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SubagentSpawner"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "spawn" in spawner_methods, "SubagentSpawner must own the spawn act"

    # Runtime introspection backstop.
    from hydramind.orchestration.execution_harness import (
        ExecutionHarnessRuntime,
        ProviderExecutionHarnessRuntime,
    )
    from hydramind.orchestration.subagent_spawn import SubagentSpawner

    assert not hasattr(ProviderExecutionHarnessRuntime, "spawn_subagent")
    assert not hasattr(ExecutionHarnessRuntime, "spawn_subagent")
    assert not any(
        "spawn" in name
        for name in dir(ProviderExecutionHarnessRuntime)
        if not name.startswith("__")
    )
    assert hasattr(SubagentSpawner, "spawn")


def test_durable_interaction_recording_is_team_scoped_not_single_agent() -> None:
    """Durable interaction recording (S5a) is TEAM-scoped, not single-agent parity.

    ADR-0007 "a plain task node degrades to a single-member interaction" is a
    kernel/scheduling-MODEL unification statement — it is NOT a claim that plain
    task nodes share the durable interaction/turn RECORDING mechanism with MAS team
    members. In code, durable interaction recording
    (``DurableInteractionRecorder``/``durable_recorder``) is wired only on the TEAM
    seam (``CollaborationExecutor`` → ``collaboration_team.py``; ``agent.py`` passes
    ``durable_recorder=self._control``); the single-agent dispatch seam
    (``agent_invocation.py``: DIRECT/SUBAGENT) records none.

    Pin BOTH halves so a future change cannot silently claim parity (wire a durable
    recorder into single-agent dispatch) nor silently drop it from the team seam
    without also updating ``96`` §10. See ``96`` §10 Phase-2 residual closure (B).
    """

    durable_recorder_tokens = ("DurableInteractionRecorder", "durable_recorder")

    team_src = ORCHESTRATION_COLLABORATION_TEAM.read_text(encoding="utf-8")
    assert "DurableInteractionRecorder" in team_src, (
        "durable interaction recording must stay wired on the team seam "
        "(collaboration_team.py)"
    )

    dispatch_src = ORCHESTRATION_AGENT_INVOCATION.read_text(encoding="utf-8")
    leaked = [token for token in durable_recorder_tokens if token in dispatch_src]
    assert leaked == [], (
        "single-agent dispatch seam (agent_invocation.py) must NOT record durable "
        f"interactions (found {leaked}); 'plain node = single-member interaction' is "
        "a kernel-model statement, not durable-recording parity — S5a is team-scoped, "
        "record-only. If this changes, update 96 §10 Phase-2 closure (B) too."
    )


def test_execution_harness_policy_carries_no_unresolved_ref() -> None:
    """``ExecutionHarnessPolicy`` expresses self-contained knobs, never dangling refs.

    ADR-0010 §F / ``96`` §9: the harness's execution-policy ownership is typed I/O
    over SELF-CONTAINED knobs. PLAN-20260623-001 (A) trimmed the inert ``*_ref``
    carrier fields (dangling typed identifiers that imply an external resolver which
    does not exist). Soft口径 guard: recursively assert NO field name on
    ``ExecutionHarnessPolicy`` or any nested pydantic sub-policy ends in
    ``_ref``/``_refs``, so a dangling pointer cannot re-accumulate. PLUS a POSITIVE
    assertion that the one live read point (``constraints.max_turns``, consumed in
    ``explicit_submit_execution_harness.py``) still exists — so the soft rule cannot be
    satisfied by an all-dead, ref-free husk.
    """

    import typing

    from pydantic import BaseModel

    from hydramind.orchestration.execution_harness import ExecutionHarnessPolicy

    def _model_types(annotation: object) -> list[type[BaseModel]]:
        found: list[type[BaseModel]] = []
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found.append(annotation)
        for arg in typing.get_args(annotation):
            found.extend(_model_types(arg))
        return found

    def _ref_fields(model: type[BaseModel], seen: set[type[BaseModel]]) -> list[str]:
        if model in seen:
            return []
        seen.add(model)
        bad: list[str] = []
        for name, field in model.model_fields.items():
            if name.endswith(("_ref", "_refs")):
                bad.append(f"{model.__name__}.{name}")
            for sub in _model_types(field.annotation):
                bad.extend(_ref_fields(sub, seen))
        return bad

    refs = _ref_fields(ExecutionHarnessPolicy, set())
    assert refs == [], f"ExecutionHarnessPolicy carries unresolved *_ref field(s): {refs}"

    submit_src = (ORCHESTRATION_ROOT / "explicit_submit_execution_harness.py").read_text(
        encoding="utf-8"
    )
    assert "constraints.max_turns" in submit_src, (
        "constraints.max_turns must remain the live policy read point — the soft "
        "no-*_ref guard must not be satisfiable by an all-dead, ref-free husk"
    )


def test_cli_entrypoint_delegates_runtime_command_execution() -> None:
    tree = _parse(CLI_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "hydramind.runtime":
            continue
        for alias in node.names:
            if alias.name in CLI_RUNTIME_EXECUTION_IMPORTS:
                violations.append(f"{_relative(CLI_ENTRYPOINT)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_runtime_entrypoint_delegates_support_loaders_and_factories() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in RUNTIME_SUPPORT_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} defines {node.name}"
                )
        for module in _imported_modules(node):
            if module in RUNTIME_SUPPORT_FORBIDDEN_IMPORTS:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {module}")

    assert violations == []


def test_runtime_entrypoint_delegates_queued_execution_support() -> None:
    tree = _parse(RUNTIME_ENTRYPOINT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in RUNTIME_QUEUE_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} defines {node.name}"
                )
        for name in _imported_names(node):
            if name in RUNTIME_QUEUE_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_ENTRYPOINT)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_queue_delegates_goal_orchestrator_reconstruction() -> None:
    runtime_queue = SRC_ROOT / "runtime_queue.py"
    tree = _parse(runtime_queue)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in RUNTIME_QUEUE_GOAL_FORBIDDEN_DEFS:
                violations.append(f"{_relative(runtime_queue)}:{node.lineno} defines {node.name}")
        for name in _imported_names(node):
            if name in RUNTIME_QUEUE_GOAL_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(runtime_queue)}:{node.lineno} imports {name}")

    assert violations == []


def test_runtime_worker_delegates_readiness_preflight() -> None:
    tree = _parse(RUNTIME_WORKER)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in RUNTIME_WORKER_READINESS_FORBIDDEN_DEFS:
                violations.append(f"{_relative(RUNTIME_WORKER)}:{node.lineno} defines {node.name}")
        for name in _imported_names(node):
            if name in RUNTIME_WORKER_READINESS_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(RUNTIME_WORKER)}:{node.lineno} imports {name}")

    assert violations == []


def test_worker_health_stays_read_only_liveness_snapshot() -> None:
    tree = _parse(RUNTIME_WORKER)
    method = _class_method(tree, "QueueExecutionHost", "health")
    violations: list[str] = []
    for node in ast.walk(method):
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in WORKER_HEALTH_READ_ONLY_FORBIDDEN_CALLS:
                violations.append(f"{_relative(RUNTIME_WORKER)}:{node.lineno} calls {called}")

    assert violations == []


def test_worker_readiness_stays_read_only_preflight() -> None:
    tree = _parse(RUNTIME_WORKER_READINESS)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in WORKER_READINESS_READ_ONLY_FORBIDDEN_IMPORT_MODULES
            ):
                violations.append(
                    f"{_relative(RUNTIME_WORKER_READINESS)}:{node.lineno} imports {module}"
                )
        for name in _imported_names(node):
            if name in WORKER_READINESS_READ_ONLY_FORBIDDEN_IMPORT_NAMES:
                violations.append(
                    f"{_relative(RUNTIME_WORKER_READINESS)}:{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in WORKER_READINESS_READ_ONLY_FORBIDDEN_CALLS:
                violations.append(
                    f"{_relative(RUNTIME_WORKER_READINESS)}:{node.lineno} calls {called}"
                )

    assert violations == []


def test_control_apply_path_records_no_session_completion_decision() -> None:
    """Control's node-apply path RECORDS outcomes; it does not DECIDE session
    completion (ADR-0008).

    The apply path must not enumerate node statuses to decide the session is
    terminal (no ``all_terminal``-style scan) and must not call
    ``complete_session`` — session completion is the orchestrator's decision,
    recorded via ``ControlPlane.record_session_complete``.
    """
    control_apply = CONTROL_ROOT / "control_apply.py"
    tree = _parse(control_apply)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "all_terminal":
            violations.append(f"{_relative(control_apply)}:{node.lineno} scans all_terminal")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "complete_session":
                violations.append(
                    f"{_relative(control_apply)}:{node.lineno} calls complete_session"
                )

    assert violations == []


def test_control_plane_delegates_apply_intent_execution() -> None:
    control_plane = CONTROL_ROOT / "control_plane.py"
    tree = _parse(control_plane)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CONTROL_PLANE_APPLY_FORBIDDEN_DEFS:
                violations.append(f"{_relative(control_plane)}:{node.lineno} {node.name}")

    assert violations == []


def test_control_plane_delegates_report_decision_helpers() -> None:
    control_plane = CONTROL_ROOT / "control_plane.py"
    tree = _parse(control_plane)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in CONTROL_PLANE_REPORT_FORBIDDEN_DEFS:
                violations.append(f"{_relative(control_plane)}:{node.lineno} {node.name}")

    assert violations == []


def test_control_plane_delegates_gate_decision_apply() -> None:
    control_plane = CONTROL_ROOT / "control_plane.py"
    tree = _parse(control_plane)
    method = _class_method(tree, "ControlPlane", "apply_decision")
    violations: list[str] = []
    for node in ast.walk(method):
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in CONTROL_PLANE_GATE_DECISION_FORBIDDEN_CALLS:
                violations.append(f"{_relative(control_plane)}:{node.lineno} calls {called}")
        if isinstance(node, ast.Name) and node.id in CONTROL_PLANE_GATE_DECISION_FORBIDDEN_NAMES:
            violations.append(f"{_relative(control_plane)}:{node.lineno} references {node.id}")

    assert violations == []


def test_control_report_decision_helpers_stay_control_local() -> None:
    tree = _parse(CONTROL_PLANE_REPORT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in CONTROL_REPORT_FORBIDDEN_IMPORTS
            ):
                violations.append(f"{_relative(CONTROL_PLANE_REPORT)}:{node.lineno} {module}")

    assert violations == []


def test_control_gate_decision_helpers_stay_control_local() -> None:
    tree = _parse(CONTROL_GATE_DECISION)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in CONTROL_REPORT_FORBIDDEN_IMPORTS
            ):
                violations.append(f"{_relative(CONTROL_GATE_DECISION)}:{node.lineno} {module}")

    assert violations == []


def test_session_service_delegates_node_lifecycle_helpers() -> None:
    session_service = CONTROL_ROOT / "session_service.py"
    tree = _parse(session_service)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in SESSION_SERVICE_NODE_LIFECYCLE_FORBIDDEN_DEFS:
                violations.append(f"{_relative(session_service)}:{node.lineno} {node.name}")

    assert violations == []


def test_session_service_delegates_session_lifecycle_helpers() -> None:
    session_service = CONTROL_ROOT / "session_service.py"
    tree = _parse(session_service)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in SESSION_SERVICE_SESSION_LIFECYCLE_FORBIDDEN_DEFS:
                violations.append(f"{_relative(session_service)}:{node.lineno} defines {node.name}")
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id in SESSION_SERVICE_SESSION_LIFECYCLE_FORBIDDEN_CALLS
            ):
                violations.append(f"{_relative(session_service)}:{node.lineno} calls {func.id}")

    assert violations == []


def test_session_service_delegates_observability_helpers() -> None:
    session_service = CONTROL_ROOT / "session_service.py"
    tree = _parse(session_service)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_DEFS:
                violations.append(f"{_relative(session_service)}:{node.lineno} defines {node.name}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_ASSIGNMENTS
                ):
                    violations.append(
                        f"{_relative(session_service)}:{node.lineno} assigns {target.id}"
                    )
        for name in _imported_names(node):
            if name in SESSION_SERVICE_OBSERVABILITY_FORBIDDEN_IMPORT_NAMES:
                violations.append(f"{_relative(session_service)}:{node.lineno} imports {name}")
        if isinstance(node, ast.Call) and _is_session_event_emit(node):
            violations.append(
                f"{_relative(session_service)}:{node.lineno} calls raw event emit"
            )

    assert violations == []


def test_session_service_delegates_persistence_helpers() -> None:
    session_service = CONTROL_ROOT / "session_service.py"
    tree = _parse(session_service)
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "_store"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            violations.append(
                f"{_relative(session_service)}:{node.lineno} references self._store"
            )
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"get", "put"}
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_store"
            ):
                violations.append(
                    f"{_relative(session_service)}:{node.lineno} calls _store.{func.attr}"
                )

    assert violations == []


def test_session_persistence_helper_stays_storage_local() -> None:
    tree = _parse(CONTROL_SESSION_PERSISTENCE)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in SESSION_PERSISTENCE_FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"{_relative(CONTROL_SESSION_PERSISTENCE)}:{node.lineno} {module}"
                )

    assert violations == []


def test_session_observability_reporter_stays_read_only() -> None:
    session_observability = CONTROL_ROOT / "session_observability.py"
    tree = _parse(session_observability)
    violations: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if module in SESSION_OBSERVABILITY_REPORTER_FORBIDDEN_IMPORT_MODULES:
                violations.append(
                    f"{_relative(session_observability)}:{node.lineno} imports {module}"
                )
        for name in _imported_names(node):
            if name in SESSION_OBSERVABILITY_REPORTER_FORBIDDEN_IMPORT_NAMES:
                violations.append(
                    f"{_relative(session_observability)}:{node.lineno} imports {name}"
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if _mutates_runtime_state(target):
                    violations.append(
                        f"{_relative(session_observability)}:{node.lineno} mutates state"
                    )
        if isinstance(node, ast.Call) and _updates_runtime_state_copy(node):
            violations.append(
                f"{_relative(session_observability)}:{node.lineno} mutates state copy"
            )

    assert violations == []


def test_planning_delegates_prompt_and_diagnostic_helpers() -> None:
    planning = ORCHESTRATION_ROOT / "planning.py"
    tree = _parse(planning)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in PLANNING_PROMPT_DIAGNOSTIC_FORBIDDEN_DEFS:
                violations.append(f"{_relative(planning)}:{node.lineno} {node.name}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in PLANNING_PROMPT_DIAGNOSTIC_FORBIDDEN_ASSIGNMENTS
                ):
                    violations.append(f"{_relative(planning)}:{node.lineno} assigns {target.id}")

    assert violations == []


def test_planning_delegates_invocation_helpers() -> None:
    planning = ORCHESTRATION_ROOT / "planning.py"
    tree = _parse(planning)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in PLANNING_INVOCATION_FORBIDDEN_DEFS:
                violations.append(f"{_relative(planning)}:{node.lineno} {node.name}")

    assert violations == []


def test_no_rule_planner_in_planning_core() -> None:
    """DoD-1 (S96): no rule-based planner may re-accumulate in the source tree.

    The agent (``ModelGoalPlanner`` over the harness) is the only planning brain.
    This fails closed if any module defines, imports, or re-exports a class named
    ``StaticGoalPlanner``/``FallbackGoalPlanner`` -- the rule-base substitutes for
    agent intelligence deleted in S96 (ADR-0008).
    """
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in RULE_PLANNER_NAMES:
                violations.append(
                    f"{_relative(path)}:{node.lineno} class {node.name}"
                )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in RULE_PLANNER_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[-1] in RULE_PLANNER_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )

    assert violations == [], violations


def test_no_rule_verify_or_repair_in_source() -> None:
    """DoD (S97): no rule-based content-quality verifier or rule repair policy
    may re-accumulate in the source tree (ADR-0008).

    Verify-good-enough and repair/replan are AGENT decisions (semantic verifier
    + planner ``revise_plan``); determinism is retained ONLY for safety/boundary
    (artifact existence, artifact-root containment, schema). This fails closed if
    any module defines, imports, or re-exports a class named
    ``ContentQualityVerifierRunner`` (the rule quality thresholds) or
    ``VerifierFeedbackRepairPolicy`` (the rule repair matrix).
    """
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in RULE_VERIFY_REPAIR_NAMES:
                violations.append(f"{_relative(path)}:{node.lineno} class {node.name}")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in RULE_VERIFY_REPAIR_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[-1] in RULE_VERIFY_REPAIR_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )

    assert violations == [], violations


def test_goal_agent_delegates_feedback_repair_helpers() -> None:
    tree = _parse(ORCHESTRATION_GOAL_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in GOAL_FEEDBACK_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} {node.name}"
                )

    assert violations == []


def test_goal_agent_delegates_session_plan_state() -> None:
    tree = _parse(ORCHESTRATION_GOAL_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in GOAL_SESSION_STATE_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} {node.name}"
                )
        for name in _imported_names(node):
            if name in GOAL_SESSION_STATE_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Name) and node.id in GOAL_SESSION_STATE_FORBIDDEN_NAMES:
            violations.append(
                f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} references {node.id}"
            )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in GOAL_SESSION_STATE_FORBIDDEN_CALLS:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:"
                    f"{node.lineno} calls {called}"
                )

    assert violations == []


def test_goal_agent_delegates_repair_runtime() -> None:
    tree = _parse(ORCHESTRATION_GOAL_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in GOAL_REPAIR_RUNTIME_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} {node.name}"
                )
        for name in _imported_names(node):
            if name in GOAL_REPAIR_RUNTIME_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Name) and node.id in GOAL_REPAIR_RUNTIME_FORBIDDEN_NAMES:
            violations.append(
                f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} references {node.id}"
            )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in GOAL_REPAIR_RUNTIME_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} calls {called}"
                )

    assert violations == []


def test_goal_agent_delegates_agent_factory_and_tool_scope() -> None:
    tree = _parse(ORCHESTRATION_GOAL_AGENT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in GOAL_AGENT_FACTORY_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} {node.name}"
                )
        for name in _imported_names(node):
            if name in GOAL_AGENT_FACTORY_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Name) and node.id in GOAL_AGENT_FACTORY_FORBIDDEN_NAMES:
            violations.append(
                f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} references {node.id}"
            )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in GOAL_AGENT_FACTORY_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_GOAL_AGENT)}:{node.lineno} calls {called}"
                )

    assert violations == []


def test_collaboration_delegates_native_team_execution_helpers() -> None:
    collaboration = ORCHESTRATION_ROOT / "collaboration.py"
    tree = _parse(collaboration)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in COLLABORATION_TEAM_FORBIDDEN_DEFS:
                violations.append(f"{_relative(collaboration)}:{node.lineno} {node.name}")

    assert violations == []


def test_native_team_executor_delegates_interaction_shaping_helpers() -> None:
    tree = _parse(ORCHESTRATION_COLLABORATION_TEAM)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in COLLABORATION_INTERACTION_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} {node.name}"
                )

    assert violations == []


def test_native_team_executor_delegates_member_runtime_helpers() -> None:
    tree = _parse(ORCHESTRATION_COLLABORATION_TEAM)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in COLLABORATION_MEMBER_RUNTIME_FORBIDDEN_DEFS:
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} {node.name}"
                )

    assert violations == []


def test_native_team_executor_delegates_event_policy() -> None:
    tree = _parse(ORCHESTRATION_COLLABORATION_TEAM)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in COLLABORATION_TEAM_EVENT_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id == "ObservationEventKind":
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} references ObservationEventKind.{node.attr}"
                )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called == "compact_text":
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} calls compact_text"
                )

    assert violations == []


def test_native_team_executor_delegates_interaction_runtime() -> None:
    tree = _parse(ORCHESTRATION_COLLABORATION_TEAM)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in COLLABORATION_TEAM_RUNTIME_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} imports {name}"
                )
        if isinstance(node, ast.Name) and node.id in COLLABORATION_TEAM_RUNTIME_FORBIDDEN_NAMES:
            violations.append(
                f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                f"{node.lineno} references {node.id}"
            )
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called in COLLABORATION_TEAM_RUNTIME_FORBIDDEN_NAMES:
                violations.append(
                    f"{_relative(ORCHESTRATION_COLLABORATION_TEAM)}:"
                    f"{node.lineno} calls {called}"
                )

    assert violations == []


def test_collaboration_records_interaction_log_through_control_seam() -> None:
    violations: list[str] = []
    for path in ORCHESTRATION_COLLABORATION_MODULES:
        tree = _parse(path)
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if module in COLLABORATION_INTERACTION_LOG_FORBIDDEN_IMPORT_MODULES:
                    violations.append(
                        f"{_relative(path)}:{node.lineno} imports {module}"
                    )
            for name in _imported_names(node):
                if name in COLLABORATION_INTERACTION_LOG_FORBIDDEN_IMPORT_NAMES:
                    violations.append(
                        f"{_relative(path)}:{node.lineno} imports {name}"
                    )
            if isinstance(node, ast.Constant) and node.value == "interaction_log":
                violations.append(
                    f"{_relative(path)}:{node.lineno} references interaction_log"
                )

    assert violations == []


def _imported_modules(node: ast.AST) -> list[str]:
    module_names: list[str] = []
    if isinstance(node, ast.Import):
        module_names.extend(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        module_names.append(node.module)
    return module_names


def _imported_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names.extend(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        names.extend(alias.asname or alias.name for alias in node.names)
    return names


def _called_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _class_method(
    tree: ast.Module,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def _is_session_event_emit(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "emit":
        return False
    owner = func.value
    return isinstance(owner, ast.Attribute) and owner.attr == "_events"


def _is_forbidden_queue_import(module: str) -> bool:
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in QUEUE_FORBIDDEN_IMPORTS
    )


def _is_forbidden_memory_import(module: str) -> bool:
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in MEMORY_FORBIDDEN_IMPORTS
    )


def _mutates_runtime_state(target: ast.AST) -> bool:
    if isinstance(target, ast.Attribute):
        return (
            target.attr in RUNTIME_STATE_ATTRS
            and isinstance(target.value, ast.Name)
            and target.value.id in RUNTIME_STATE_NAMES
        )
    if isinstance(target, ast.Subscript):
        value = target.value
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "nodes"
            and isinstance(value.value, ast.Name)
            and value.value.id == "session"
        )
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_mutates_runtime_state(item) for item in target.elts)
    return False


def _updates_runtime_state_copy(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "model_copy":
        return False
    if not any(keyword.arg == "update" for keyword in node.keywords):
        return False
    return _runtime_state_expr_name(node.func.value) in RUNTIME_STATE_NAMES


def _runtime_state_expr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_no_removed_dead_contracts_in_source() -> None:
    """S102 (DEV-30/31): the removed legacy ApplyPayload + the generic-rule-engine
    PolicyEvaluator must not re-accumulate anywhere in ``src/``.

    Control carries no orchestration-decision payload (ApplyIntent only), and gates
    stay safety-authorization rather than a rule engine (ADR-0004/0008). Fails closed
    on any class def or import of these names.
    """
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in REMOVED_DEAD_CONTRACT_NAMES:
                violations.append(f"{_relative(path)}:{node.lineno} class {node.name}")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in REMOVED_DEAD_CONTRACT_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[-1] in REMOVED_DEAD_CONTRACT_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} import {alias.name}"
                        )

    assert violations == [], violations


TRACE_CONSOLE = SRC_ROOT / "trace_console.py"
CLI_TRACE = SRC_ROOT / "cli_trace.py"
# The read-only trace console is a pure consumer of the observability event
# model. It must not reach into the SoT/control/harness or any execution layer;
# observability (the event schema) is the only framework dependency allowed.
TRACE_CONSOLE_FORBIDDEN_IMPORTS = {
    "hydramind.control",
    "hydramind.harness",
    "hydramind.orchestration",
    "hydramind.runtime",
    "hydramind.runtime_worker",
    "hydramind.queue",
    "hydramind.memory",
    "hydramind.tools",
    "hydramind.governance",
    "hydramind.mas",
}


def test_trace_console_stays_read_only_observability_consumer() -> None:
    violations: list[str] = []
    for path in (TRACE_CONSOLE, CLI_TRACE):
        tree = _parse(path)
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in TRACE_CONSOLE_FORBIDDEN_IMPORTS
                ):
                    violations.append(f"{_relative(path)}:{node.lineno} imports {module}")

    assert violations == []


def _references_replay_backend(tree: ast.Module) -> list[str]:
    """AST findings where a module imports or constructs the replay provider.

    Catches (a) importing the replay provider names (MockProvider / ScriptedTurn /
    invocation_fingerprint), (b) importing the replay modules directly, and
    (c) constructing/calling MockProvider by name. Replay support lives in
    ``hydramind.testing`` (ADR-0010 / S6) and must not appear in the production
    harness public API or the production provider factory.
    """

    findings: list[str] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            if module in REPLAY_BACKEND_MODULES:
                findings.append(f"{node.lineno} imports module {module}")
        for name in _imported_names(node):
            if name in REPLAY_BACKEND_NAMES:
                findings.append(f"{node.lineno} imports name {name}")
        if isinstance(node, ast.Call):
            called = _called_name(node.func)
            if called == "MockProvider":
                findings.append(f"{node.lineno} constructs {called}")
    return findings


def test_production_harness_api_does_not_import_replay_backend() -> None:
    # The harness package public API is provider/execution-seam only. Replay
    # support (MockProvider) lives in hydramind.testing, never re-exported here.
    tree = _parse(HARNESS_INIT)
    findings = _references_replay_backend(tree)
    assert findings == [], f"{_relative(HARNESS_INIT)}: {findings}"


def test_production_backend_factory_does_not_construct_replay_backend() -> None:
    # create_model_provider_from_env builds real provider/SDK backends only;
    # it must neither import nor construct the replay provider.
    tree = _parse(HARNESS_FACTORY)
    findings = _references_replay_backend(tree)
    assert findings == [], f"{_relative(HARNESS_FACTORY)}: {findings}"


def test_runtime_support_replay_branch_sources_from_hydramind_testing() -> None:
    # The offline CLI replay path (`--provider mock`) is an acceptable testing
    # affordance, but it must import the replay provider from hydramind.testing,
    # NOT from the production harness package.
    tree = _parse(RUNTIME_SUPPORT)
    violations: list[str] = []
    for node in ast.walk(tree):
        for name in _imported_names(node):
            if name in REPLAY_BACKEND_NAMES:
                modules = _imported_modules(node)
                if not all(module in REPLAY_BACKEND_MODULES for module in modules):
                    violations.append(
                        f"{_relative(RUNTIME_SUPPORT)}:{node.lineno} imports {name} "
                        f"from {modules}"
                    )
    assert violations == []
