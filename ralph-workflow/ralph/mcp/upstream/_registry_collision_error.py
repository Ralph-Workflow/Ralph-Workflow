"""Error raised when two upstream servers produce the same proxy alias."""

from __future__ import annotations


class RegistryCollisionError(ValueError):
    """Raised when two upstream servers produce the same proxy alias for a tool."""
