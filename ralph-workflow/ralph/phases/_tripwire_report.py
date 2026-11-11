
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.phases._tripwire_finding import Finding


@dataclass(frozen=True)
class TripwireReport:

    hard: tuple[Finding, ...] = ()
    soft: tuple[Finding, ...] = ()

    @property
    def has_hard_tripwires(self) -> bool:
        return len(self.hard) > 0

    @property
    def has_soft_tripwires(self) -> bool:
        return len(self.soft) > 0

    @property
    def is_clean(self) -> bool:
        return not self.hard and not self.soft

    @property
    def passed(self) -> bool:
        return not self.has_hard_tripwires
