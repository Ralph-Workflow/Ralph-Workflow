from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path


def _rev(root: Path, label: str, *args: str) -> str | None:
    result = run_git(args, cwd=root, label=label)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _changed_between(root: Path, old: str, new: str) -> frozenset[str] | None:
    result = run_git(
        ("diff-tree", "-r", "--name-only", "--no-commit-id", "-z", old, new),
        cwd=root,
        label="git-auto-merged-diff-tree",
    )
    if result.returncode != 0:
        return None
    return frozenset(path for path in result.stdout.split("\0") if path)


def both_sides_changed_paths(root: Path, replayed_sha: str) -> tuple[str, ...]:
    parent = _rev(
        root, "git-auto-merged-rev-parse", "rev-parse", "--verify", "--quiet", f"{replayed_sha}^"
    )
    onto = _rev(root, "git-auto-merged-rev-parse", "rev-parse", "--verify", "--quiet", "HEAD")
    if parent is None or onto is None:
        return ()
    base = _rev(root, "git-auto-merged-merge-base", "merge-base", parent, onto)
    if base is None:
        return ()
    commit_side = _changed_between(root, parent, replayed_sha)
    target_side = _changed_between(root, base, onto)
    if commit_side is None or target_side is None:
        logger.warning(
            "conflict_resolution: could not list the paths both sides of {} changed",
            replayed_sha,
        )
        return ()
    return tuple(sorted(commit_side & target_side))


__all__ = ["both_sides_changed_paths"]
