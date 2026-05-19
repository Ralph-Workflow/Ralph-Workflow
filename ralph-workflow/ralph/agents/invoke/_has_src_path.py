"""_HasSrcPath protocol for watchdog events that expose a source path."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _HasSrcPath(Protocol):
    """Protocol for watchdog events that expose a source path."""

    src_path: str
