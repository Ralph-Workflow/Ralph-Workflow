from __future__ import annotations

from typing import Protocol


class ExitHandler(Protocol):
    def __call__(self, exit_code: int, /) -> object: ...


__all__ = ["ExitHandler"]
