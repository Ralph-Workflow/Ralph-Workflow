from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import TerminationIssuer


@dataclass(frozen=True)
class TerminationOutcome:
    issuer: TerminationIssuer | None
    returncode: int | None
