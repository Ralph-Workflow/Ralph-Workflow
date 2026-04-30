"""Skip patterns for recursive workspace traversal."""

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
