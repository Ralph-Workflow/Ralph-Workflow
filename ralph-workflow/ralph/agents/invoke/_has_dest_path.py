from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _HasDestPath(Protocol):
    dest_path: str
