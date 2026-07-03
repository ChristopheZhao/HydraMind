#!/usr/bin/env bash
# Run the MAS production blog scenario through the `hydramind goal` main path.
#
# This wrapper is a thin shim around `hydramind goal`. It does NOT replace the
# framework's gate / verifier surface; all acceptance logic lives in the
# quality contract and the typed verifier runners. The wrapper only:
#
#   1. Loads the local .env (if present) so live providers/tools work.
#   2. Reads `goal_spec.json` to assemble the long CLI flag list.
#   3. Invokes `hydramind goal` with `--quality-contract`, `--trace-path`,
#      `--live-tools`, `--planner auto`, and enough tool/repair budget for this
#      long scenario. The agent semantic verifier is the default and runs
#      automatically when the quality contract declares a semantic rubric.
#   4. Echoes the final artifact_root + JSON output so the operator can hand
#      `evidence_collector.py` the session id.
#
# Dependencies (preference order):
#   - jq        : preferred for shell-driven JSON parsing.
#   - python3   : automatic fallback when jq is not available; uses stdlib only.
#
# Trace JSONL: this wrapper always passes `--trace-path` so the CLI wires
# `JsonlObserver` into the run and the evidence collector can copy the same
# file into the redaction-safe evidence package.

set -euo pipefail

SCENARIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCENARIO_DIR}/../../.." && pwd)"
SPEC_PATH="${SCENARIO_DIR}/goal_spec.json"
CONTRACT_PATH="${SCENARIO_DIR}/quality_contract.json"
ENV_FILE="${HYDRAMIND_ENV_FILE:-${REPO_ROOT}/.env}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARTIFACT_ROOT="${HYDRAMIND_ARTIFACT_ROOT:-artifacts/scenarios/mas-production-blog/${TIMESTAMP}}"
TRACE_PATH="${HYDRAMIND_TRACE_PATH:-${ARTIFACT_ROOT}/trace.jsonl}"
STORE_PATH="${HYDRAMIND_STORE_PATH:-var/blog.sqlite}"
HYDRAMIND_BIN="${HYDRAMIND_BIN:-${REPO_ROOT}/.venv/bin/hydramind}"
MAX_TOOL_ROUNDS="${HYDRAMIND_MAX_TOOL_ROUNDS:-12}"
MAX_AUTO_REPAIRS="${HYDRAMIND_MAX_AUTO_REPAIRS:-4}"

if [[ ! -f "${SPEC_PATH}" ]]; then
    echo "goal_spec.json not found at ${SPEC_PATH}" >&2
    exit 2
fi
if [[ ! -f "${CONTRACT_PATH}" ]]; then
    echo "quality_contract.json not found at ${CONTRACT_PATH}" >&2
    exit 2
fi
if [[ ! -x "${HYDRAMIND_BIN}" ]]; then
    echo "hydramind binary not found at ${HYDRAMIND_BIN}; set HYDRAMIND_BIN to override" >&2
    exit 2
fi

# Pick a JSON parser. jq is preferred; otherwise we fall back to python3.
JSON_TOOL=""
if command -v jq >/dev/null 2>&1; then
    JSON_TOOL="jq"
elif command -v python3 >/dev/null 2>&1; then
    JSON_TOOL="python3"
else
    echo "neither jq nor python3 available; cannot parse goal_spec.json" >&2
    exit 2
fi

read_array() {
    # Args: jq path expression matching scalar elements.
    local path="$1"
    if [[ "${JSON_TOOL}" == "jq" ]]; then
        jq -r "${path} | .[]" "${SPEC_PATH}"
    else
        python3 - "${SPEC_PATH}" "${path}" <<'PY'
import json
import sys

path_file, path_expr = sys.argv[1], sys.argv[2]
with open(path_file, encoding="utf-8") as fh:
    payload = json.load(fh)

# Accept ".key" or ".key.nested" style paths matching what we use below.
expr = path_expr.strip()
assert expr.startswith("."), f"unsupported path: {path_expr!r}"
node = payload
for part in [p for p in expr[1:].split(".") if p]:
    node = node[part]
if not isinstance(node, list):
    raise SystemExit(f"path {path_expr!r} is not a list")
for item in node:
    print(item)
PY
    fi
}

read_string() {
    local path="$1"
    if [[ "${JSON_TOOL}" == "jq" ]]; then
        jq -r "${path}" "${SPEC_PATH}"
    else
        python3 - "${SPEC_PATH}" "${path}" <<'PY'
import json
import sys

path_file, path_expr = sys.argv[1], sys.argv[2]
with open(path_file, encoding="utf-8") as fh:
    payload = json.load(fh)
expr = path_expr.strip()
assert expr.startswith("."), f"unsupported path: {path_expr!r}"
node = payload
for part in [p for p in expr[1:].split(".") if p]:
    node = node[part]
if not isinstance(node, str):
    raise SystemExit(f"path {path_expr!r} is not a string")
print(node)
PY
    fi
}

OBJECTIVE="$(read_string '.objective')"

CLI_ARGS=("goal" "${OBJECTIVE}")
CLI_ARGS+=("--provider" "env")
CLI_ARGS+=("--planner" "auto")
CLI_ARGS+=("--live-tools")
CLI_ARGS+=("--env-file" "${ENV_FILE}")
CLI_ARGS+=("--artifact-root" "${ARTIFACT_ROOT}")
CLI_ARGS+=("--quality-contract" "${CONTRACT_PATH}")
CLI_ARGS+=("--trace-path" "${TRACE_PATH}")
CLI_ARGS+=("--max-tool-rounds" "${MAX_TOOL_ROUNDS}")
CLI_ARGS+=("--max-auto-repairs" "${MAX_AUTO_REPAIRS}")
CLI_ARGS+=("--session-store" "sqlite")
CLI_ARGS+=("--store-path" "${STORE_PATH}")

while IFS= read -r tool_name; do
    [[ -z "${tool_name}" ]] && continue
    CLI_ARGS+=("--tool" "${tool_name}")
done < <(read_array '.available_tools')

while IFS= read -r required_tool; do
    [[ -z "${required_tool}" ]] && continue
    CLI_ARGS+=("--required-tool" "${required_tool}")
done < <(read_array '.required_tools')

while IFS= read -r artifact; do
    [[ -z "${artifact}" ]] && continue
    CLI_ARGS+=("--expected-artifact" "${artifact}")
done < <(read_array '.expected_artifacts')

while IFS= read -r constraint; do
    [[ -z "${constraint}" ]] && continue
    CLI_ARGS+=("--constraint" "${constraint}")
done < <(read_array '.constraints')

while IFS= read -r criterion; do
    [[ -z "${criterion}" ]] && continue
    CLI_ARGS+=("--success-criteria" "${criterion}")
done < <(read_array '.success_criteria')

echo "=== MAS Production Blog Scenario ==="
echo "scenario_dir : ${SCENARIO_DIR}"
echo "artifact_root: ${ARTIFACT_ROOT}"
echo "trace_path   : ${TRACE_PATH}"
echo "store_path   : ${STORE_PATH}"
echo "env_file     : ${ENV_FILE}"
echo "hydramind    : ${HYDRAMIND_BIN}"
echo "tool_rounds  : ${MAX_TOOL_ROUNDS}"
echo "auto_repairs : ${MAX_AUTO_REPAIRS}"
echo "json_parser  : ${JSON_TOOL}"
echo "command      : ${HYDRAMIND_BIN} ${CLI_ARGS[*]}"
echo "===================================="

# Execute. We let the CLI inherit stdout/stderr because the operator
# typically wants both the JSON tail and any tool/planner stderr noise.
exec "${HYDRAMIND_BIN}" "${CLI_ARGS[@]}"
