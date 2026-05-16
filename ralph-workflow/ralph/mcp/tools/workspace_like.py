"""Protocol for workspace access used by coordination tools."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceLike(Protocol):
    """Placeholder workspace protocol for handler parity."""

    def absolute_path(self, path: str) -> str:
        """Return an absolute workspace path for the provided relative path."""
        ...
