from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import TerminationIssuer

__all__ = ("TerminationLogEntry",)


@dataclass(frozen=True)
class TerminationLogEntry:
    issuer: TerminationIssuer
    source: str
