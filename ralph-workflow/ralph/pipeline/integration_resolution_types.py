"""Typed verdict values for the integration-resolution dispatch invariant."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus


@dataclass(frozen=True)
class IntegrationResolutionVerdict:
    """Evidence-backed answer to whether an ordinary phase may dispatch."""

    status: IntegrationResolutionStatus
    reasons: tuple[str, ...] = ()
    recovery_executor: str | None = None

    @property
    def dispatch_allowed(self) -> bool:
        """Whether a non-resolution phase is safe to dispatch."""
        return self.status is IntegrationResolutionStatus.RESOLVED
