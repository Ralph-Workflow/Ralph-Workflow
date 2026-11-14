from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str


__all__ = ["WaitingOutcome"]
