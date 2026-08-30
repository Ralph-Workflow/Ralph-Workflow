"""Create and identify the self-contained snapshot used by ``make install``."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_NAMES = frozenset({".git", ".venv", "__pycache__", "build", "dist", "tmp"})

#: Snapshot entries a reinstall must leave standing.
#:
#: ``.venv`` is the snapshot's own virtualenv.  It is never copied from the
#: source checkout -- ``uv sync`` builds it in place afterwards -- so the
#: installer cannot restore what it deletes, and ``rdev`` runs *from* it:
#: ``<snapshot>/.venv/bin/python`` is the live ``sys.executable``, the same
#: binary Ralph re-spawns for the MCP server subprocess.  Wiping the snapshot
#: wholesale therefore killed any run that was in flight with
#: ``FileNotFoundError: .../.venv/bin/python`` and forced a from-scratch
#: environment rebuild for nothing.  ``uv sync`` reconciles a surviving
#: virtualenv in place, and recreates it itself when the interpreter no longer
#: matches, so keeping it is both safe and strictly faster.
#:
#: Every preserved name MUST also be ignored by the copy: preserving something
#: ``copytree`` would overwrite anyway buys nothing and hides the intent.
#: ``test_preserved_install_names_are_never_copied`` pins that.
_PRESERVED_NAMES = frozenset({".venv"})


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


def _clear_stale_snapshot_entries(destination: Path) -> None:
    """Empty ``destination`` of everything except :data:`_PRESERVED_NAMES`.

    Clearing entry by entry -- rather than removing ``destination`` itself --
    is what keeps the snapshot's live virtualenv, and therefore the running
    interpreter, in place across a reinstall.  Files the new snapshot no
    longer contains still go, so this is a replace and not a merge.
    """
    for entry in destination.iterdir():
        if entry.name in _PRESERVED_NAMES:
            continue
        # filesystem-write-ok: clear the disposable dev-install snapshot before copying it anew
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def copy_install_tree(source: Path, destination: Path) -> Path:
    """Replace ``destination`` with a complete installable checkout snapshot.

    Everything the previous snapshot held is discarded except the entries in
    :data:`_PRESERVED_NAMES`, which the installer does not own and cannot
    rebuild from ``source``.
    """
    if destination.exists():
        _clear_stale_snapshot_entries(destination)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & _IGNORED_NAMES

    # filesystem-write-ok: create the user-requested self-contained dev-install snapshot
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)
    return destination
