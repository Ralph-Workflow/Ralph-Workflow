from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    merge_state,
    staged_conflict_marker_paths,
    unmerged_paths,
)
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "conflict_resolution_warranted",
    "retained_round_fingerprint",
]


def conflict_resolution_warranted(root: Path) -> bool:
    warranted = False
    try:
        if rebase_in_progress_at(root):
            warranted = True
    except Exception:
        warranted = True
    if not warranted:
        try:
            warranted = bool(unmerged_paths(root))
        except Exception:
            warranted = True
    if not warranted:
        try:
            warranted = bool(staged_conflict_marker_paths(root))
        except Exception:
            warranted = True
    return warranted


def retained_round_fingerprint(root: Path) -> str:
    return "|".join((_head_identity(root), _stop_identity(root), _unmerged_identity(root)))


def _head_identity(root: Path) -> str:
    try:
        from ralph.git.operations import get_head_sha

        return get_head_sha(root)
    except Exception:
        return "unreadable"


def _stop_identity(root: Path) -> str:
    try:
        if merge_state(root) == MERGE_STATE_IN_PROGRESS:
            return _marker_sha(root, "MERGE_HEAD") or "merge-in-progress"
    except Exception:
        return "unreadable"
    try:
        if rebase_in_progress_at(root):
            return _marker_sha(root, "REBASE_HEAD") or "rebase-in-progress"
    except Exception:
        return "unreadable"
    return "none"


def _marker_sha(root: Path, marker: str) -> str | None:
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("rev-parse", "--verify", marker),
        cwd=root,
        label=f"integration-gate:{marker.lower()}",
    )
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _unmerged_identity(root: Path) -> str:
    try:
        return ",".join(sorted(unmerged_paths(root)))
    except Exception:
        return "unreadable"
