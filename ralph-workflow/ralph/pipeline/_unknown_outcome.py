from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UnknownOutcome"]


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException
