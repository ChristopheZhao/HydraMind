"""Example audio-delivery gate for the short_video reference workflow."""

from __future__ import annotations

from hydramind.control import AgentReport, Gate, NodeState, RuntimeSession
from hydramind.control.states import GateOutcome
from hydramind.gating import GateContract, GateEvaluator, GateRegistry, GateSeverity


class AudioDeliveryGateEvaluator(GateEvaluator):
    """Checks that the audio node produced at least one delivery artifact."""

    name = "workflow_video_audio_delivery"
    contract = GateContract(
        name=name,
        description="The audio step must produce a delivery artifact or explanation.",
        applies_to_nodes=("audio",),
        severity=GateSeverity.ADVISORY,
    )

    async def evaluate(
        self,
        session: RuntimeSession,
        node: NodeState,
        report: AgentReport,
    ) -> Gate | None:
        has_delivery = bool(report.output)
        return Gate(
            name=self.name,
            node_key=node.key,
            outcome=GateOutcome.PASS if has_delivery else GateOutcome.REQUIRES_DECISION,
            detail={
                "contract_version": "v1",
                "gate_name": self.name,
                "result": "pass" if has_delivery else "inconclusive",
                "reason_code": "artifact_present" if has_delivery else "audio_manifest_missing",
                "checked_fields": sorted(report.output),
            },
        )


def build_gate_registry() -> GateRegistry:
    return GateRegistry([AudioDeliveryGateEvaluator()])
