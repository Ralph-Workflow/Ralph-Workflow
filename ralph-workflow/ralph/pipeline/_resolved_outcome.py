from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ResolvedOutcome"]


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    succeeded: bool = True
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return True
