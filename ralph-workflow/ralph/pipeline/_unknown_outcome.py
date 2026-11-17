from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UnknownOutcome"]


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        return self.terminal_reason
