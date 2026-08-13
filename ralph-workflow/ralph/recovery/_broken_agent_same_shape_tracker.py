"""Pure tracker for consecutive identical broken-agent failures on one sole agent."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.recovery._broken_agent_same_shape_error import BrokenAgentSameShapeLimitError

BrokenAgentFingerprint = tuple[str, str]


@dataclass(frozen=True)
class BrokenAgentSameShapeTracker:
    """Bound consecutive ``(broken_agent_reason, agent_name)`` fingerprints."""

    limit: int = 2

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(
                "BrokenAgentSameShapeTracker.limit must be >= 1; "
                f"got {self.limit}"
            )

    def record_failure(
        self,
        *,
        broken_agent_reason: str,
        agent_name: str,
        prior_fingerprint: BrokenAgentFingerprint | None = None,
        prior_consecutive: int = 0,
    ) -> tuple[BrokenAgentFingerprint, int]:
        """Return the next fingerprint/count or raise when the bound is reached."""
        fingerprint = (broken_agent_reason, agent_name)
        consecutive = prior_consecutive + 1 if fingerprint == prior_fingerprint else 1
        if consecutive >= self.limit:
            raise BrokenAgentSameShapeLimitError(
                fingerprint=fingerprint,
                consecutive=consecutive,
                limit=self.limit,
            )
        return fingerprint, consecutive


__all__ = ["BrokenAgentFingerprint", "BrokenAgentSameShapeTracker"]
