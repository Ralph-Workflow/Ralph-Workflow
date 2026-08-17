"""Create and identify the self-contained snapshot used by ``make install``."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_NAMES = frozenset({".git", ".venv", "__pycache__", "build", "dist", "tmp"})


@dataclass(frozen=True)
class SnapshotIdentity:
    """Which checkout a dev snapshot was built from, and what it contains.

    Every checkout and git worktree shares one snapshot directory and one
    ``rdev`` launcher, so an install silently takes the launcher over from
    whichever checkout installed last.  This is the record that makes the
    takeover reportable.
    """

    source_path: str
    source_commit: str
    version: str


def _read_string_assignment(source: str, name: str) -> str:
    """Return the double-quoted value assigned to ``name``, or ``""`` when absent."""
    match = re.search(rf'^{re.escape(name)}(?::\s*str)?\s*=\s*"([^"]*)"', source, re.MULTILINE)
    return match.group(1) if match else ""


def read_snapshot_identity(snapshot_dir: Path) -> SnapshotIdentity | None:
    """Return the identity of an installed snapshot, or ``None`` when there is none.

    The snapshot is read as text rather than imported: it is a foreign copy of
    the package that may be older than, or incompatible with, the installer
    doing the reading.  Any unreadable snapshot is reported as absent so a
    damaged previous install can never block a fresh one.
    """
    package = snapshot_dir / "ralph"
    try:
        build_meta = (package / "_build_meta.py").read_text(encoding="utf-8")
        init = (package / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return SnapshotIdentity(
        source_path=_read_string_assignment(build_meta, "BUILD_SOURCE_PATH"),
        source_commit=_read_string_assignment(build_meta, "BUILD_SOURCE_COMMIT"),
        version=_read_string_assignment(init, "__version__"),
    )


def copy_install_tree(source: Path, destination: Path) -> Path:
    """Replace ``destination`` with a complete installable checkout snapshot."""
    if destination.exists():
        # filesystem-write-ok: replace the disposable dev-install snapshot before copying it anew
        shutil.rmtree(destination)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & _IGNORED_NAMES

    # filesystem-write-ok: create the user-requested self-contained dev-install snapshot
    shutil.copytree(source, destination, ignore=ignore)
    return destination
