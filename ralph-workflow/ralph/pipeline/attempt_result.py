from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import AttemptFacts
    from ralph.recovery.attempt_outcome import AttemptOutcome


@dataclass(frozen=True)
class AttemptResult:
    event: object
    facts: AttemptFacts
    outcome: AttemptOutcome
    attempt_outcomes: tuple[tuple[int, AttemptOutcome], ...]


__all__ = ("AttemptResult",)
