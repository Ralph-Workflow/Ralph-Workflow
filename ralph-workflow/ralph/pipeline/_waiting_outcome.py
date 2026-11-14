from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WaitingOutcome"]


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False
