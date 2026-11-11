
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict


class IntegrationDispatchBlockedError(RuntimeError):

    def __init__(self, phase: str, verdict: IntegrationResolutionVerdict) -> None:
        self.phase = phase
        self.verdict = verdict
        detail = "; ".join(verdict.reasons) or str(verdict.status)
        super().__init__(
            f"cannot dispatch {phase!r}: integration resolution is {verdict.status}: {detail}"
        )
