"""Ephemeral writable overlays for MCP exec."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_GENERATED_DIR_NAMES = (
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".tox",
    ".nox",
)


def _mirror_workspace(source_root: Path, overlay_root: Path) -> None:
    """Copy the workspace into a private overlay, dereferencing symlinks."""

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _GENERATED_DIR_NAMES}

    shutil.copytree(
        source_root,
        overlay_root,
        symlinks=False,
        ignore=_ignore,
        ignore_dangling_symlinks=True,
    )


def _resolve_gitdir_pointer(source_git: Path) -> Path:
    gitdir_text = source_git.read_text(encoding="utf-8").strip()
    if not gitdir_text.startswith("gitdir:"):
        raise ValueError(f"{source_git} is not a gitdir pointer")

    gitdir_value = gitdir_text.split(":", 1)[1].strip()
    gitdir_path = Path(gitdir_value)
    if not gitdir_path.is_absolute():
        gitdir_path = (source_git.parent / gitdir_path).resolve()
    return gitdir_path


def _resolve_shared_gitdir(source_gitdir: Path) -> Path:
    commondir = source_gitdir / "commondir"
    if commondir.exists():
        common_value = commondir.read_text(encoding="utf-8").strip()
        common_path = Path(common_value)
        if not common_path.is_absolute():
            common_path = (source_gitdir / common_path).resolve()
        return common_path

    if source_gitdir.parent.name == "worktrees":
        return source_gitdir.parent.parent

    return source_gitdir


def _patch_core_worktree(config_text: str, overlay_root: Path) -> str:
    lines = config_text.splitlines()
    if not lines:
        return f"[core]\n\tworktree = {overlay_root}\n"

    output: list[str] = []
    in_core = False
    core_seen = False
    worktree_written = False

    for line in lines:
        stripped = line.strip()
        is_section = stripped.startswith("[") and stripped.endswith("]")
        if is_section:
            if in_core and not worktree_written:
                output.append(f"\tworktree = {overlay_root}")
                worktree_written = True
            in_core = stripped.lower() == "[core]"
            core_seen = core_seen or in_core
            output.append(line)
            continue

        if in_core and stripped.startswith("worktree ="):
            output.append(f"\tworktree = {overlay_root}")
            worktree_written = True
        else:
            output.append(line)

    if in_core and not worktree_written:
        output.append(f"\tworktree = {overlay_root}")
        worktree_written = True

    if not core_seen:
        if output and output[-1] != "":
            output.append("")
        output.append("[core]")
        output.append(f"\tworktree = {overlay_root}")

    return "\n".join(output) + "\n"


def _write_private_config(source_gitdir: Path, private_gitdir: Path, overlay_root: Path) -> None:
    source_config = source_gitdir / "config"
    if source_config.exists():
        config_text = source_config.read_text(encoding="utf-8")
        private_config = _patch_core_worktree(config_text, overlay_root)
    else:
        private_config = (
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            f"\tworktree = {overlay_root}\n"
        )
    private_gitdir.write_text(private_config, encoding="utf-8")


def _copy_worktree_state(source_gitdir: Path, private_gitdir: Path) -> None:
    for filename in (
        "HEAD",
        "index",
        "MERGE_HEAD",
        "MERGE_MSG",
        "CHERRY_PICK_HEAD",
        "REBASE_HEAD",
        "COMMIT_EDITMSG",
    ):
        source = source_gitdir / filename
        if source.exists():
            shutil.copy2(source, private_gitdir / filename)


def _copy_worktree_refs(shared_gitdir: Path, private_gitdir: Path) -> None:
    source_refs = shared_gitdir / "refs"
    private_refs = private_gitdir / "refs"
    private_refs.mkdir(parents=True, exist_ok=True)
    if source_refs.exists():
        shutil.copytree(source_refs, private_refs, dirs_exist_ok=True)

    packed_refs = shared_gitdir / "packed-refs"
    if packed_refs.exists():
        shutil.copy2(packed_refs, private_gitdir / "packed-refs")


def _write_alternates(shared_gitdir: Path, private_gitdir: Path) -> None:
    alternates = private_gitdir / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{shared_gitdir / 'objects'}\n", encoding="utf-8")


def _setup_private_gitdir(
    source_git: Path,
    overlay_git: Path,
    overlay_root: Path,
    tmp_root: Path,
) -> None:
    """Create a private gitdir for a worktree-style .git file."""
    source_gitdir = _resolve_gitdir_pointer(source_git)
    shared_gitdir = _resolve_shared_gitdir(source_gitdir)
    private_gitdir = tmp_root / "private-gitdir"

    if private_gitdir.exists():
        shutil.rmtree(private_gitdir)
    private_gitdir.mkdir(parents=True, exist_ok=True)

    _copy_worktree_state(source_gitdir, private_gitdir)
    _copy_worktree_refs(shared_gitdir, private_gitdir)
    _write_alternates(shared_gitdir, private_gitdir)
    _write_private_config(source_gitdir, private_gitdir / "config", overlay_root)

    overlay_git.unlink(missing_ok=True)
    overlay_git.write_text(f"gitdir: {private_gitdir}\n", encoding="utf-8")


def _ensure_git_isolation(source_root: Path, overlay_root: Path, tmp_root: Path) -> None:
    """Rewrite a copied worktree .git file to point at a private gitdir."""
    source_git = source_root / ".git"
    overlay_git = overlay_root / ".git"
    if source_git.is_file():
        _setup_private_gitdir(source_git, overlay_git, overlay_root, tmp_root)


@contextmanager
def create_ephemeral_overlay(source_root: Path) -> Iterator[Path]:
    """Create a temporary workspace mirror for isolated exec runs."""
    with tempfile.TemporaryDirectory(prefix="ralph-exec-overlay-") as tmpdir:
        overlay_root = Path(tmpdir) / "ws"
        overlay_root.parent.mkdir(parents=True, exist_ok=True)
        _mirror_workspace(source_root, overlay_root)
        _ensure_git_isolation(source_root, overlay_root, Path(tmpdir))
        yield overlay_root


__all__ = ["create_ephemeral_overlay"]
