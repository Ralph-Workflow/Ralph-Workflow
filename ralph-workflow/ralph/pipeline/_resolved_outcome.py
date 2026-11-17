from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
