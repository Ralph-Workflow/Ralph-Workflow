"""Ephemeral writable overlays for MCP exec.

The exec tool runs commands inside a private workspace mirror so writes stay
isolated from the caller's filesystem view.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIRROR_IGNORE_NAMES = (
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
)


def _mirror_workspace(source_root: Path, overlay_root: Path) -> None:
    """Copy a workspace snapshot into the private overlay root."""

    def _ignore_workspace_entries(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _MIRROR_IGNORE_NAMES}

    shutil.copytree(
        source_root,
        overlay_root,
        dirs_exist_ok=True,
        ignore=_ignore_workspace_entries,
    )
    tmp_dir = overlay_root / ".agent" / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)


def _setup_private_gitdir(source_git: Path, overlay_git: Path) -> None:
    """Copy the source git metadata into a private overlay gitdir."""
    overlay_git.parent.mkdir(parents=True, exist_ok=True)
    if source_git.is_dir():
        shutil.copytree(source_git, overlay_git, dirs_exist_ok=True)
        return

    if source_git.is_file():
        gitdir_text = source_git.read_text(encoding="utf-8").strip()
        if gitdir_text.startswith("gitdir:"):
            gitdir_value = gitdir_text.split(":", 1)[1].strip()
            gitdir_path = Path(gitdir_value)
            if not gitdir_path.is_absolute():
                gitdir_path = (source_git.parent / gitdir_path).resolve()
            if gitdir_path.exists() and gitdir_path.is_dir():
                shutil.copytree(gitdir_path, overlay_git, dirs_exist_ok=True)
                return
        shutil.copy2(source_git, overlay_git)
        return

    raise FileNotFoundError(f"Git metadata not found at '{source_git}'")


def _ensure_git_isolation(source_root: Path, overlay_root: Path) -> None:
    """Ensure the overlay has private git metadata if the source workspace does."""
    source_git = source_root / ".git"
    if not source_git.exists():
        return
    overlay_git = overlay_root / ".git"
    if overlay_git.exists():
        if overlay_git.is_dir():
            shutil.rmtree(overlay_git)
        else:
            overlay_git.unlink()
    _setup_private_gitdir(source_git, overlay_git)


@contextlib.contextmanager
def create_ephemeral_overlay(source_root: Path) -> Iterator[Path]:
    """Create and clean up a private workspace mirror for exec commands."""
    with tempfile.TemporaryDirectory(prefix="ralph-exec-overlay-") as tmpdir:
        overlay_root = Path(tmpdir)
        _mirror_workspace(source_root, overlay_root)
        _ensure_git_isolation(source_root, overlay_root)
        yield overlay_root


__all__ = [
    "create_ephemeral_overlay",
]
