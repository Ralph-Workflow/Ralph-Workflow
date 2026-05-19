"""AccessMode — server access mode for MCP tool dispatch."""

from __future__ import annotations

from enum import StrEnum


class AccessMode(StrEnum):
    """Server access mode for MCP tool dispatch."""

    READ_ONLY = "ReadOnly"
    READ_WRITE = "ReadWrite"

    def allows_write(self) -> bool:
        """Return whether this access mode allows write operations."""
        return self is AccessMode.READ_WRITE


__all__ = ["AccessMode"]
