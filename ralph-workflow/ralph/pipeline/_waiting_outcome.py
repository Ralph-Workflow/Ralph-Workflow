from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WaitingOutcome"]


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str
