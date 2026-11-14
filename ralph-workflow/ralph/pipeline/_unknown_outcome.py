from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UnknownOutcome"]


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False
