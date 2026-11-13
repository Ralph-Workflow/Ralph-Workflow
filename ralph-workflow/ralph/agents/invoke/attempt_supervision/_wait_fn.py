from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _WaitFn(Protocol):
    def __call__(self, timeout: float | None = None) -> int: ...
