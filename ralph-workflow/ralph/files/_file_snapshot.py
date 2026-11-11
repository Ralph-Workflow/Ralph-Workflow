
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class FileSnapshot:

    path: Path
    checksum: str
    size: int
    exists: bool


__all__ = ["FileSnapshot"]
