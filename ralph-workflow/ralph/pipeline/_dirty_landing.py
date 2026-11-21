from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.git.branch_relation import BranchRelation, branch_relation
from ralph.git.rebase.rebase import RebaseNoOp
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_rebase_resolve import RebaseRunResult

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.integration_resolution import TreeReadinessAssessment

__all__ = [
    "DIRTY_TREE_REASON",
    "fast_forward_only_result",
    "has_uncommitted_tracked_changes",
    "lands_without_worktree",
    "log_rebase_wait",
]

DIRTY_TREE_REASON: Final[str] = (
    "worktree not clean; uncommitted tracked changes deferred catch-up integration"
)
_FAST_FORWARDABLE: Final[frozenset[BranchRelation]] = frozenset(
    {BranchRelation.FEATURE_AHEAD, BranchRelation.SYNCED}
)
_LOGGED_WAITS: deque[tuple[tuple[str, str], tuple[str, str]]] = deque(maxlen=64)


def lands_without_worktree(root: Path, target: str, readiness: TreeReadinessAssessment) -> bool:
    if readiness.block_kinds or not readiness.blocking_reasons:
        return False
    if any(reason != DIRTY_TREE_REASON for reason in readiness.blocking_reasons):
        return False
    branch = _rev(root, "symbolic-ref", "--quiet", "--short", "HEAD", label="git-symbolic-ref-head")
    if not branch or branch == target:
        return False
    return branch_relation(root, target) in _FAST_FORWARDABLE


def has_uncommitted_tracked_changes(root: Path) -> bool:
    try:
        result = run_git(
            ("status", "--porcelain", "--untracked-files=no"),
            cwd=root,
            label="git-transition-status",
        )
    except (OSError, RuntimeError, ValueError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def fast_forward_only_result() -> RebaseRunResult:
    return RebaseRunResult(
        rebase_outcome=RebaseNoOp(
            "worktree has uncommitted changes; fast-forwarding the committed commits only"
        ),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=None,
    )


def _rev(root: Path, *args: str, label: str = "git-branch-sha") -> str:
    result = run_git(args, cwd=root, label=label)
    return result.stdout.strip() if result.returncode == 0 else ""


def log_rebase_wait(root: Path, target: str) -> None:
    head = _rev(root, "rev-parse", "HEAD")
    target_sha = _rev(root, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
    key = (str(root), target)
    state = (head, target_sha)
    if next((seen for logged, seen in reversed(_LOGGED_WAITS) if logged == key), None) == state:
        return
    _LOGGED_WAITS.append((key, state))
    waiting = _rev(root, "rev-list", "--count", f"{target}..HEAD") or "0"
    logger.info("{} committed commits wait for a clean worktree to rebase onto {}", waiting, target)
