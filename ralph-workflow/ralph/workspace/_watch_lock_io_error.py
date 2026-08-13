"""Error raised when the cross-process watch-lock sidecar I/O fails.

Extracted from :mod:`ralph.workspace._cross_process_watch_lock` so each
module defines exactly one public top-level class (repo-structure audit).
"""

from __future__ import annotations

__all__ = ["WatchLockIOError"]


class WatchLockIOError(OSError):
    """Raised when lock sidecar I/O fails; callers must enter live fallback."""
