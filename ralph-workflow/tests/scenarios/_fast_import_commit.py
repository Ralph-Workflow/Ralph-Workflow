from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FastImportCommit"]


@dataclass(frozen=True)
class FastImportCommit:
    ref: str
    message: str
    files: tuple[tuple[str, bytes], ...]
    parent_index: int | None = None
    parent_sha: str | None = None
