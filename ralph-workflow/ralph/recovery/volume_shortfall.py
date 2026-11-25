from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

GIB: Final[int] = 1024**3


def gib_text(size: int) -> str:
    return f"{max(size, 0) / GIB:.1f}"


@dataclass(frozen=True, slots=True)
class VolumeShortfall:
    path: Path
    free_bytes: int
    required_bytes: int

    def describe(self) -> str:
        return (
            f"{self.path} has {gib_text(self.free_bytes)} GiB free "
            f"(Ralph Workflow needs {gib_text(self.required_bytes)} GiB)"
        )


__all__ = ["GIB", "VolumeShortfall", "gib_text"]
