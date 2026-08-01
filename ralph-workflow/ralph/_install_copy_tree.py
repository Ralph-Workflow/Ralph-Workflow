"""Create the self-contained snapshot used by ``make install``."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_NAMES = frozenset({".git", ".venv", "__pycache__", "build", "dist", "tmp"})


def copy_install_tree(source: Path, destination: Path) -> Path:
    """Replace ``destination`` with a complete installable checkout snapshot."""
    if destination.exists():
        shutil.rmtree(destination)
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & _IGNORED_NAMES

    shutil.copytree(source, destination, ignore=ignore)
    return destination
