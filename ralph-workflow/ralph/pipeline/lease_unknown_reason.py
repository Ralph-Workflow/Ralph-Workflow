
from __future__ import annotations

from enum import Enum, auto


class LeaseUnknownReason(Enum):

    GIT_DIR_UNRESOLVED = auto()
    GIT_DIR_INVALID = auto()
    LOCK_IO_ERROR = auto()


__all__ = ["LeaseUnknownReason"]
