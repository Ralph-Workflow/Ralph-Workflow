from __future__ import annotations

from typing import Protocol


class ProcessLike(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...

    @property
    def pid(self) -> int: ...


__all__ = ["ProcessLike"]
