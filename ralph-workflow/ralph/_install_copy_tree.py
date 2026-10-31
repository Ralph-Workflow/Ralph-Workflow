"""Create and identify the self-contained snapshot used by ``make install``."""

from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_NAMES = frozenset(
    {
        ".agent",
        ".agents",
        ".claude",
        ".codex",
        ".gemini",
        ".hypothesis",
        ".mypy_cache",
        ".opencode",
        ".pytest_cache",
        ".ruff_cache",
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "tmp",
    }
)


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
    """Return the string literal assigned to ``name``, or ``""`` when absent.

    Parsed as Python, not matched as text: the installer writes these values
    with ``repr`` (single quotes, escapes), and a published package may use
    double quotes, so only a real literal parse reads both back faithfully.
    The source is never executed.
    """
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        named = any(isinstance(target, ast.Name) and target.id == name for target in targets)
        if named and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return ""


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

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & _IGNORED_NAMES

    # filesystem-write-ok: create the user-requested self-contained dev-install snapshot
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)
    return destination
