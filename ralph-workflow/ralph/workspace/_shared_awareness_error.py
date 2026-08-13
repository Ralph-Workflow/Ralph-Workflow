"""Error raised when the shared-awareness sidecar is unreadable.

Extracted from :mod:`ralph.workspace._shared_awareness` so each module
defines exactly one public top-level class (repo-structure audit).
"""

from __future__ import annotations

__all__ = ["SharedAwarenessError"]


class SharedAwarenessError(RuntimeError):
    """Raised when sidecar I/O fails; callers must enter bounded live fallback."""
