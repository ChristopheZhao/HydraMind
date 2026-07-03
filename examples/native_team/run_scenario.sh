#!/usr/bin/env bash
# Run the native-team MAS example fully offline and show the verified artifact.
#
# This is a thin wrapper around `hydramind run`. It does NOT replace the
# framework's gate / verifier surface — verification lives in the workflow's
# `task_contract` and the runtime verifier stack. The wrapper only:
#   1. Runs the example with `--provider mock` + the committed offline fixture.
#   2. Writes artifacts under a throwaway artifact root.
#   3. Prints the run JSON and the produced `brief.md`.
#
# No network/live calls are made (MockProvider record/replay drives the team).
set -euo pipefail

SCENARIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCENARIO_DIR}/../.." && pwd)"
HYDRAMIND_BIN="${HYDRAMIND_BIN:-${REPO_ROOT}/.venv/bin/hydramind}"
ARTIFACT_ROOT="${HYDRAMIND_ARTIFACT_ROOT:-$(mktemp -d)}"
FIXTURE="${SCENARIO_DIR}/mock_fixture.json"
WORKFLOW="${SCENARIO_DIR}/workflow.yaml"

echo "artifact_root: ${ARTIFACT_ROOT}"
"${HYDRAMIND_BIN}" run "${WORKFLOW}" \
  --provider mock \
  --mock-fixture "${FIXTURE}" \
  --artifact-root "${ARTIFACT_ROOT}"

echo
echo "== produced artifact: ${ARTIFACT_ROOT}/brief.md =="
cat "${ARTIFACT_ROOT}/brief.md"
