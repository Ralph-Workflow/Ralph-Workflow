from __future__ import annotations

from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "is_ralph_workspace_path",
    "move_stray_aside",
    "restore_one_unrequested_path",
    "revert_unrequested_paths",
    "worktree_dirty_paths",
]


def worktree_dirty_paths(root: Path) -> frozenset[str] | None:
    result = run_git(
        ("status", "--porcelain=v1", "-z"),
        cwd=root,
        label="git-worktree-dirty-paths",
    )
    if result.returncode != 0:
        logger.warning(
            "conflict_resolution: could not read the worktree status: {}",
            result.stderr.strip(),
        )
        return None
    return frozenset(_porcelain_paths(result.stdout))


_PORCELAIN_ENTRY_MIN_LEN: Final[int] = 4


def _porcelain_paths(blob: str) -> list[str]:
    entries = [entry for entry in blob.split("\0") if entry]
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < _PORCELAIN_ENTRY_MIN_LEN:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if ("R" in code or "C" in code) and index < len(entries):
            paths.append(entries[index])
            index += 1
    return paths


_RALPH_WORKSPACE_PREFIX = ".agent/"


def is_ralph_workspace_path(path: str) -> bool:
    normalised = path.strip().removeprefix("./")
    return normalised == ".agent" or normalised.startswith(_RALPH_WORKSPACE_PREFIX)


def restore_one_unrequested_path(root: Path, path: str) -> bool:
    tracked = run_git(
        ("ls-files", "--error-unmatch", "--", path),
        cwd=root,
        label="git-stray-tracked",
    )
    if tracked.returncode == 0:
        restored = run_git(
            ("checkout", "--", path),
            cwd=root,
            label="git-revert-stray",
        )
        return restored.returncode == 0
    target = root / path
    if target.is_dir():
        logger.info("conflict_resolution: leaving untracked directory '{}' in place", path)
        return True
    return move_stray_aside(target)


def move_stray_aside(target: Path) -> bool:
    if not target.exists() and not target.is_symlink():
        return True
    for suffix in range(1, 100):
        aside = target.with_name(f"{target.name}.ralph-set-aside-{suffix}")
        if aside.exists():
            continue
        try:
            target.rename(aside)
        except OSError:
            return False
        logger.warning(
            "conflict_resolution: '{}' was not part of the conflict; moved it aside to '{}'",
            target.name,
            aside.name,
        )
        return True
    return False


def revert_unrequested_paths(root: Path, paths: tuple[str, ...]) -> bool:
    if not paths:
        return True
    checkout = run_git(
        ("checkout", "--", *paths),
        cwd=root,
        label="git-revert-unrequested-paths",
    )
    if checkout.returncode == 0:
        return True
    return all(restore_one_unrequested_path(root, path) for path in paths)
