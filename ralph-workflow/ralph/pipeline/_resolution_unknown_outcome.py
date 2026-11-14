from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException


__all__ = ["UnknownOutcome"]
