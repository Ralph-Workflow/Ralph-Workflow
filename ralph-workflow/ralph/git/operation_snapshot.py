from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.git import subprocess_runner
from ralph.git._operation_state import OperationState
from ralph.git.merge import (
    observe_branch_sha,
    observe_unmerged_paths,
    staged_conflict_marker_paths,
)
from ralph.git.operations import _resolve_gitdir_paths, get_head_sha

__all__ = ["OperationSnapshot", "OperationState", "observe_operation_snapshot"]

_MINIMUM_REBASE_SIGNALS = 2


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    unmerged_paths: tuple[str, ...]
    unmerged_query_ok: bool
    operation_state: OperationState
    head_sha: str | None
    target_tip: str | None
    merge_head: str | None
    rebase_head: str | None
    index_tree: str | None
    staged_conflict_marker_paths: tuple[str, ...]


def _read_marker(gitdir: Path, name: str) -> str | None:
    path = gitdir / name
    try:
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def _marker_exists(gitdir: Path, name: str) -> bool:
    try:
        return (gitdir / name).exists()
    except OSError:
        return False


def _head_detached(gitdir: Path) -> bool | None:
    head = _read_marker(gitdir, "HEAD")
    if head is None:
        return None
    return not head.startswith("ref:")


def _rebase_signal_count(
    gitdir: Path,
    *,
    detached: bool | None,
    rebase_head: str | None,
    has_unmerged: bool,
) -> int:
    rebase_merge = gitdir / "rebase-merge"
    rebase_apply = gitdir / "rebase-apply"
    plan_present = (
        _marker_exists(rebase_merge, "onto") and _marker_exists(rebase_merge, "head-name")
    ) or (_marker_exists(rebase_apply, "onto") and _marker_exists(rebase_apply, "head-name"))
    sequence_present = _marker_exists(rebase_merge, "git-rebase-todo") or (
        _marker_exists(rebase_apply, "next") and _marker_exists(rebase_apply, "last")
    )
    stopped_present = _marker_exists(rebase_merge, "stopped-sha") or _marker_exists(
        rebase_merge, "orig-head"
    )
    return sum(
        (
            rebase_head is not None,
            detached is True,
            has_unmerged,
            plan_present,
            sequence_present,
            stopped_present,
        )
    )


def _operation_state(
    gitdir: Path | None,
    *,
    detached: bool | None,
    merge_head: str | None,
    rebase_head: str | None,
    has_unmerged: bool,
) -> OperationState:
    if gitdir is None or detached is None:
        return OperationState.UNKNOWN
    rebase_marker = _marker_exists(gitdir, "rebase-merge") or _marker_exists(gitdir, "rebase-apply")
    if (
        rebase_marker
        and _rebase_signal_count(
            gitdir,
            detached=detached,
            rebase_head=rebase_head,
            has_unmerged=has_unmerged,
        )
        >= _MINIMUM_REBASE_SIGNALS
    ):
        return OperationState.REBASE_PAUSED
    merge_evidence = any(
        (
            _marker_exists(gitdir, "MERGE_MODE"),
            _marker_exists(gitdir, "MERGE_MSG"),
            has_unmerged,
        )
    )
    if merge_head is not None and merge_evidence:
        return OperationState.MERGE_PAUSED
    if detached:
        return OperationState.DETACHED
    return OperationState.NONE


def _read_index_tree(root: Path, *, query_ok: bool, has_unmerged: bool) -> str | None:
    if not query_ok or has_unmerged:
        return None
    try:
        result = subprocess_runner.run_git(
            ("write-tree",),
            cwd=root,
            label="operation-snapshot:index-tree",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def observe_operation_snapshot(
    repo_root: Path | str,
    *,
    target: str | None = None,
) -> OperationSnapshot:
    root = Path(repo_root)
    observed_unmerged = observe_unmerged_paths(root)
    paths = tuple(sorted(set(observed_unmerged.paths)))
    try:
        gitdir, _commondir = _resolve_gitdir_paths(root)
    except (OSError, ValueError):
        gitdir = None
    try:
        head_sha = get_head_sha(root)
    except Exception:
        head_sha = None
    target_tip = observe_branch_sha(root, target)[0] if target is not None else None
    merge_head = _read_marker(gitdir, "MERGE_HEAD") if gitdir is not None else None
    rebase_head = _read_marker(gitdir, "REBASE_HEAD") if gitdir is not None else None
    detached = _head_detached(gitdir) if gitdir is not None else None
    state = _operation_state(
        gitdir,
        detached=detached,
        merge_head=merge_head,
        rebase_head=rebase_head,
        has_unmerged=bool(paths),
    )
    try:
        marker_paths = tuple(staged_conflict_marker_paths(root))
    except Exception:
        marker_paths = ("<staged-marker-query-failed>",)
    return OperationSnapshot(
        unmerged_paths=paths,
        unmerged_query_ok=observed_unmerged.query_ok,
        operation_state=state,
        head_sha=head_sha,
        target_tip=target_tip,
        merge_head=merge_head,
        rebase_head=rebase_head,
        index_tree=_read_index_tree(
            root,
            query_ok=observed_unmerged.query_ok,
            has_unmerged=bool(paths),
        ),
        staged_conflict_marker_paths=marker_paths,
    )
