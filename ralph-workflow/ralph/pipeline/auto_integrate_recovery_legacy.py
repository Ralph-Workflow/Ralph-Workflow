from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, abort_merge, merge_state
from ralph.git.operations import is_repo_clean
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.rebase.rebase_checkpoint import (
    LegacyCheckpointStatus,
    inspect_legacy_rebase_checkpoint,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_recovery_git_state import (
    common_dir_lock_paths as _common_dir_lock_paths,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    detached_branch_name as _detached_branch_name,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    detached_head_no_state as _detached_head_no_state,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    lock_holder_is_dead as _lock_holder_is_dead,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    reattach_head_to_branch as _reattach_head_to_branch,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    rebase_bookkeeping_dir as _rebase_bookkeeping_dir,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    rebase_state_dir_is_corrupt as _rebase_state_dir_is_corrupt,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    remove_path as _remove_path,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    run_sequencer_quit as _run_sequencer_quit,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    select_rebase_state_dir as _select_rebase_state_dir,
)
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._recovery_fast_forward_landing import _record_skip
from ralph.pipeline.auto_integrate_recovery_terminal import (
    _TERMINAL_MARKER_FILES,
)
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "legacy_rebase_startup_block",
]


def _clear_legacy_checkpoint(workspace_root: Path) -> None:
    checkpoint_path = workspace_root / ".agent" / "rebase_checkpoint.json"
    for path in (checkpoint_path, checkpoint_path.with_suffix(".json.bak")):
        if path.exists():
            path.unlink()


def legacy_rebase_startup_block(workspace_root: Path) -> RebaseState | None:
    if not (workspace_root / ".git").exists():
        return None
    inspection = inspect_legacy_rebase_checkpoint(workspace_root)
    if inspection.status == LegacyCheckpointStatus.ABSENT:
        return None
    checkpoint = inspection.checkpoint
    identity = {
        "legacy_checkpoint_blocked": True,
        "legacy_phase": checkpoint.phase.value if checkpoint is not None else None,
        "legacy_conflicted_files": tuple(checkpoint.conflicted_files) if checkpoint else (),
        "legacy_resolved_files": tuple(checkpoint.resolved_files) if checkpoint else (),
        "legacy_error_count": checkpoint.error_count if checkpoint else 0,
        "legacy_timestamp": checkpoint.timestamp if checkpoint else None,
        "legacy_source": ".agent/rebase_checkpoint.json",
        "last_target": checkpoint.upstream_branch if checkpoint else None,
    }
    if inspection.status == LegacyCheckpointStatus.TERMINAL:
        terminal_state = RebaseState(**identity)
        if inspect_integration_resolution(workspace_root, terminal_state).dispatch_allowed:
            _clear_legacy_checkpoint(workspace_root)
            return None
        return RebaseState(
            last_outcome=RebaseOutcomeKind.CONFLICT,
            last_reason="legacy terminal checkpoint contradicts live Git",
            **identity,
        )
    reason = inspection.reason or "legacy rebase checkpoint requires recovery"
    return RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT, last_reason=reason, **identity)


def _reclaim_unowned_stale_rebase(root: Path) -> RebaseState | None:
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        return None
    blocking = _collect_blocking_markers(git_dir, root)
    if not blocking:
        return None
    if not is_repo_clean(root):
        logger.warning(
            "recovery: unowned in-progress rebase found in {} but the tree"
            " is dirty; preserving it (operator-owned, AC-11 case 4)",
            root,
        )
        return None
    if _unowned_operation_may_be_live(root, git_dir):
        logger.critical(
            "recovery: live merge/rebase metadata in {} has no readable ownership record; "
            "retaining operation and resolved index for supervised recovery",
            root,
        )
        return _record_skip(
            reason="live merge/rebase has no readable ownership record; progress retained",
            target=None,
            retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
            record_retained=True,
        )
    logger.warning(
        "recovery: reclaiming stale unowned rebase/merge state in {} (no"
        " ownership record, clean worktree) -- it was permanently blocking"
        " auto-integration preconditions",
        root,
    )
    try:
        _recover_rebase_state(root, git_dir)
        _recover_merge_marker(root, git_dir)
        _recover_sequencer_state(root, git_dir)
        _recover_detached_head(root, git_dir)
    except Exception as exc:
        logger.warning("recovery: failed to reclaim stale unowned rebase state: {}", exc)
        return _record_skip(
            reason=f"recovery: stale unowned rebase state could not be reclaimed: {exc}",
            target=None,
        )
    return RebaseState(
        last_outcome=RebaseOutcomeKind.RECOVERED,
        last_reason=(
            "reclaimed stale unowned rebase/merge state (no ownership record, clean worktree)"
        ),
        last_target=None,
        fast_forwarded=False,
    )


def _unowned_operation_may_be_live(root: Path, git_dir: Path) -> bool:
    state_dir = _select_rebase_state_dir(git_dir)
    if state_dir is not None and not _rebase_state_dir_is_corrupt(state_dir):
        return True
    if not (git_dir / "MERGE_HEAD").exists():
        return False
    parent = run_git(
        ("rev-parse", "--verify", "MERGE_HEAD^{commit}"),
        cwd=root,
        label="recovery:unowned-merge-identity",
    )
    return parent.returncode == 0 or (git_dir / "MERGE_MSG").exists()


def _collect_blocking_markers(git_dir: Path, root: Path) -> list[str] | None:
    blocking: list[str] = [name for name in _TERMINAL_MARKER_FILES if (git_dir / name).exists()]
    lock_paths = [git_dir / "index.lock", *_common_dir_lock_paths(root)]
    for lock_path in lock_paths:
        if not lock_path.exists():
            continue
        if not _lock_holder_is_dead(lock_path, gitdir=git_dir):
            logger.warning(
                "recovery: live lock holder in {}; leaving it for retry",
                lock_path,
            )
            return None
        _remove_path(lock_path)
        blocking.append(str(lock_path))
        logger.warning("recovery: reclaimed dead-holder lock {}", lock_path)
    return blocking


def _recover_rebase_state(root: Path, git_dir: Path) -> None:
    rebase_state_dir = _select_rebase_state_dir(git_dir)
    if rebase_state_dir is not None:
        if _rebase_state_dir_is_corrupt(rebase_state_dir):
            _remove_path(rebase_state_dir)
            _discard_dead_rebase_progress(root)
        elif rebase_state_dir.name == "rebase-apply" and (rebase_state_dir / "applying").exists():
            run_git(("am", "--abort"), cwd=root, label="recovery:git-am-abort")
            _discard_dead_rebase_progress(root)
        else:
            abort_rebase_discarding_progress(root)
    if (git_dir / "REBASE_HEAD").exists() and not rebase_in_progress(root):
        _remove_path(git_dir / "REBASE_HEAD")


def _discard_dead_rebase_progress(root: Path) -> None:
    from ralph.pipeline.conflict_resolution.progress import clear_progress

    try:
        clear_progress(root)
    except Exception as exc:
        logger.warning("auto_integrate: could not clear the rebase progress record: {}", exc)


def _recover_merge_marker(root: Path, git_dir: Path) -> None:
    if not (git_dir / "MERGE_HEAD").exists():
        return
    if merge_state(root) == MERGE_STATE_NONE:
        return
    aborted = abort_merge(root)
    if aborted:
        return
    if (git_dir / "MERGE_HEAD").exists():
        _remove_path(git_dir / "MERGE_HEAD")


def _recover_sequencer_state(root: Path, git_dir: Path) -> None:
    sequencer_todo = git_dir / "sequencer" / "todo"
    if not (
        (git_dir / "CHERRY_PICK_HEAD").exists()
        or (git_dir / "REVERT_HEAD").exists()
        or sequencer_todo.exists()
    ):
        return
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        _run_sequencer_quit(root, "cherry-pick")
    if (git_dir / "REVERT_HEAD").exists():
        _run_sequencer_quit(root, "revert")
    for marker in ("CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir / marker).exists():
            _remove_path(git_dir / marker)
    sequencer_dir = git_dir / "sequencer"
    if sequencer_dir.exists():
        _remove_path(sequencer_dir)


def _recover_detached_head(root: Path, git_dir: Path) -> None:
    if not _detached_head_no_state(root, git_dir):
        return
    branch = _detached_branch_name(root)
    if branch is None:
        return
    _reattach_head_to_branch(root, branch)
