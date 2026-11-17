from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WaitingOutcome"]


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        return self.terminal_reason
