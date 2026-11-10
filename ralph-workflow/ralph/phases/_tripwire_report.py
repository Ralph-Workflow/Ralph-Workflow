"""Tripwire report representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.phases._tripwire_finding import Finding


@dataclass(frozen=True)
class TripwireReport:
    """Report containing hard and soft tripwire evaluation results.

    Attributes:
        hard: Findings that trigger an immediate hard rejection without agent review.
        soft: Findings that are passed to Stage 2 agent review as evidence.
    """

    hard: tuple[Finding, ...] = ()
    soft: tuple[Finding, ...] = ()

    @property
    def has_hard_tripwires(self) -> bool:
        """Return True if any hard tripwire triggered."""
        return len(self.hard) > 0

    @property
    def has_soft_tripwires(self) -> bool:
        """Return True if any soft tripwire triggered."""
        return len(self.soft) > 0

    @property
    def is_clean(self) -> bool:
        """Return True if no hard or soft tripwires triggered."""
        return not self.hard and not self.soft

    @property
    def passed(self) -> bool:
        """Return True if no hard tripwires triggered (can advance to Stage 2)."""
        return not self.has_hard_tripwires
