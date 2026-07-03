"""Runtime-edge assembly for the default verifier stack."""

from __future__ import annotations

from pathlib import Path

from hydramind.harness import ModelProvider
from hydramind.orchestration import (
    ArtifactContainmentVerifierRunner,
    CompositeVerifierRunner,
    SemanticArtifactVerifierRunner,
    TaskContractVerifierRunner,
    VerifierRunner,
)


def build_default_goal_verifier_runner(
    *,
    artifact_root: Path,
    provider: ModelProvider,
) -> VerifierRunner:
    """Assemble the standard goal/workflow verifier runner stack (ADR-0008).

    Order: deterministic safety and boundary checks first, then the agent
    semantic judge. The semantic runner no-ops unless the task quality contract
    includes an enabled semantic rubric, so offline mock runs stay deterministic.
    """

    return CompositeVerifierRunner(
        runners=(
            TaskContractVerifierRunner(artifact_root),
            ArtifactContainmentVerifierRunner(artifact_root),
            SemanticArtifactVerifierRunner(
                provider=provider,
                artifact_root=artifact_root,
            ),
        )
    )
