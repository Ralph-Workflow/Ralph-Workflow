"""FileStateKind — kinds of file-state drift detected during checkpoint validation."""

from __future__ import annotations

from enum import Enum


class FileStateKind(Enum):
    """Kinds of file-state drift detected during checkpoint validation."""

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    CHANGED = "changed"


__all__ = ["FileStateKind"]
