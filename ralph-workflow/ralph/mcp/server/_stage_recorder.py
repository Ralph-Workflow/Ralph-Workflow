from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class StageRecorder(Protocol):
    def __call__(self, root: Path, stage: str, *, timeout_s: float | None = None) -> None: ...


__all__ = ["StageRecorder"]
