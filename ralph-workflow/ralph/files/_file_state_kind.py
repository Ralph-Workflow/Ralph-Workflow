
from __future__ import annotations

from enum import Enum


class FileStateKind(Enum):

    MISSING = "missing"
    UNEXPECTED = "unexpected"
    CHANGED = "changed"


__all__ = ["FileStateKind"]
