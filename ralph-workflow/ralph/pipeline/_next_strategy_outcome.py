from __future__ import annotations

from dataclasses import dataclass

__all__ = ["NextStrategyOutcome"]


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: str
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False
