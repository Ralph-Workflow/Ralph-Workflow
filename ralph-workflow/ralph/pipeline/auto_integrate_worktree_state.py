
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.integration_observation import (
    Reading,
    WorktreeState,
    WorktreeVerdict,
    classify_worktree,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["_worktree_is_clean", "worktree_is_clean"]


def _verdict(root: Path) -> WorktreeVerdict:
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
            cwd=root,
            label="git-transition-status",
        )
    except Exception:
        return classify_worktree(WorktreeState.UNKNOWN)
    if result.returncode != 0:
        return classify_worktree(WorktreeState.UNKNOWN)
    return classify_worktree(Reading.known(result.stdout))


def _worktree_is_clean(root: Path) -> bool:
    return _verdict(root).dispatch_allowed


worktree_is_clean = _worktree_is_clean
