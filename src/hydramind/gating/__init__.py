"""Gating — first-class contracts that authorize state advances.

See ``docs/architecture/30-gating.md``. Public surface:

    GateContract     — typed declaration of what a gate guards
    GateSeverity     — ADVISORY | BLOCKING
    GateEvaluator    — Protocol for runnable evaluators
    GateRegistry     — composes evaluators into a single GateFn

    Built-in evaluators:
        SchemaCheckEvaluator
        TimeoutEvaluator
        HumanInLoopEvaluator
        VerifierFeedbackEvaluator
"""

from hydramind.gating.base import (
    GateContract,
    GateEvaluator,
    GateRegistry,
    GateSeverity,
)
from hydramind.gating.evaluators import (
    HumanInLoopEvaluator,
    SchemaCheckEvaluator,
    TimeoutEvaluator,
    VerifierFeedbackEvaluator,
)

__all__ = [
    "GateContract",
    "GateEvaluator",
    "GateRegistry",
    "GateSeverity",
    "HumanInLoopEvaluator",
    "SchemaCheckEvaluator",
    "TimeoutEvaluator",
    "VerifierFeedbackEvaluator",
]
