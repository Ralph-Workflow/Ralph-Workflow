"""_HasDestPath protocol for watchdog events that expose a move destination."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _HasDestPath(Protocol):
    """Protocol for watchdog events that expose a move destination."""

    dest_path: str
