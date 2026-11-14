from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["ResolvedOutcome"]


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()
    failure_cause: ResolutionFailureCause | None = None

    @property
    def succeeded(self) -> bool:
        return True

    @property
    def reason(self) -> object:
        return None

    def __iter__(self) -> Iterator[object]:
        return iter((True, ""))

    def __getitem__(self, index: int) -> object:
        return (True, "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return True
