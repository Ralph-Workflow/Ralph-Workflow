from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus


@dataclass(frozen=True)
class IntegrationResolutionVerdict:
    status: IntegrationResolutionStatus
    reasons: tuple[str, ...] = ()
    recovery_executor: str | None = None

    @property
    def dispatch_allowed(self) -> bool:
        return self.status is IntegrationResolutionStatus.RESOLVED
