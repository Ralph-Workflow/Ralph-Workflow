from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class GenerationPruner(Protocol):
    def __call__(self, generations: Path, *, keep: Path) -> tuple[Path, ...]: ...


__all__ = ["GenerationPruner"]
