from __future__ import annotations

from enum import StrEnum


class AccessMode(StrEnum):
    READ_ONLY = "ReadOnly"
    READ_WRITE = "ReadWrite"

    def allows_write(self) -> bool:
        return self is AccessMode.READ_WRITE


__all__ = ["AccessMode"]
