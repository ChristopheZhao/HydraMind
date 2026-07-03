"""HydraMind testing/replay support: deterministic, non-agent test doubles.

This namespace holds deterministic REPLAY / TEST support, deliberately kept
out of the production provider API and production provider factory
(ADR-0010 / PLAN-20260618-001 S6; basis
``docs/architecture/95-execution-harness-correction.md`` §F4 +
"Move Out of Normal Runtime").

``MockProvider`` here makes no model decisions and no network calls. It is
replay/test evidence only, never production model access and never live-agent or
live-MAS acceptance evidence.
"""

from hydramind.testing.replay import (
    MockProvider,
    ScriptedTurn,
    invocation_fingerprint,
)

__all__ = [
    "MockProvider",
    "ScriptedTurn",
    "invocation_fingerprint",
]
