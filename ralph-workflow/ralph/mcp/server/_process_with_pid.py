"""Protocol for a process with a pid attribute."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _ProcessWithPid(Protocol):
    pid: int
