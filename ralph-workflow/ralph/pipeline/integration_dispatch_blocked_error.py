"""Typed refusal carrying integration evidence to ordinary dispatch callers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict


class IntegrationDispatchBlockedError(RuntimeError):
    """Expose the blocked phase and verdict without parsing diagnostic text."""

    def __init__(self, phase: str, verdict: IntegrationResolutionVerdict) -> None:
        self.phase = phase
        self.verdict = verdict
        detail = "; ".join(verdict.reasons) or str(verdict.status)
        super().__init__(
            f"cannot dispatch {phase!r}: integration resolution is {verdict.status}: {detail}"
        )
