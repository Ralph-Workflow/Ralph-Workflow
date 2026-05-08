"""Skip patterns for recursive workspace traversal.

Defines ``RECURSIVE_SKIP_DIRECTORY_NAMES``, the canonical frozenset of
directory names that must never be recursed into during workspace file
discovery or context-window content gathering. Applying this set keeps scans
fast and prevents noise from VCS internals, build caches, and vendored package
trees.

Currently skipped: ``.git``, ``.hg``, ``.mypy_cache``, ``.pytest_cache``,
``.ruff_cache``, ``.svn``, ``.venv``, ``__pycache__``, ``node_modules``,
``target``.
"""

from __future__ import annotations

RECURSIVE_SKIP_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "target",
    }
)
