"""Legacy rebase-recovery helpers split from :mod:`ralph.pipeline.auto_integrate_recovery`.

Holds the ``legacy_rebase_startup_block`` entry point (still public,
re-exported by the orchestrator) and the per-marker reclaim helpers
that close the wt-23 silent-noop bug. Every helper is one bounded
filesystem repair; none of them decides whether a reclaim may happen
at all. The orchestrator keeps the policy that does.
"""

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
    """Retire only a terminal checkpoint already proved clean by Git."""
    checkpoint_path = workspace_root / ".agent" / "rebase_checkpoint.json"
    for path in (checkpoint_path, checkpoint_path.with_suffix(".json.bak")):
        if path.exists():
            # filesystem-write-ok: retire only validated terminal legacy evidence
            path.unlink()


def legacy_rebase_startup_block(workspace_root: Path) -> RebaseState | None:
    """Reconcile only provably retired legacy evidence; otherwise block startup.

    Legacy checkpoints predate the auto-integration record.  Treating them as
    disposable made planning possible while a previous rebase was unresolved.
    This boundary is deliberately side-effect minimal: it clears a validated
    terminal checkpoint only after Git proves no rebase or merge exists.
    """
    # Non-repository test and orchestration contexts cannot have legacy Git
    # rebase state. Do not turn their ordinary startup into a filesystem read.
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
    """Reclaim inert rebase state that no ownership record accounts for.

    Failure mode this closes (recorded on wt-23, 2026-07-22):
    a leftover ``rebase-merge``/``rebase-apply`` directory
    or lone ``REBASE_HEAD`` marker with NO durable record fails
    ``check_rebase_preconditions`` at every seam -- startup, phase
    boundaries, the commit seam -- forever, and nothing ever cleans it,
    because recovery's no-record branch used to return ``None``
    unconditionally. That is the forbidden permanent silent noop.

    A clean index can be a valid resolution choosing HEAD's content.
    Live operation metadata therefore prevents reclamation even on a clean
    tree. Only malformed residue without a usable operation identity
    reaches cleanup; dirty work is preserved independently.

    Returns ``None`` when there is nothing to reclaim or the state is
    protected (dirty tree); a ``recovered`` state after a successful
    reclaim; a ``skipped`` state when the reclaim itself failed.

    Implementation note: the actual reclaim work is delegated to a
    sequence of small helpers (``_recover_rebase_state``,
    ``_recover_merge_marker``, ``_recover_sequencer_state``,
    ``_recover_detached_head``) so this orchestrator stays below the
    ruff PLR0912 (too-many-branches) cap while each branch lives in
    a focused helper that can be unit-tested in isolation.
    """
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        # Not a git checkout (or git unaskable): nothing observable to
        # reclaim, and the pre-fix contract for the no-record branch --
        # return ``None``, never a synthesized outcome -- must hold.
        return None
    blocking = _collect_blocking_markers(git_dir, root)
    if not blocking:
        return None
    if not is_repo_clean(root):
        # AC-11 case 4: unmerged paths / tracked modifications mean an
        # operator (or live agent) owns this rebase. Preserve it.
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
        # A9: the live-PID guard already cleared the index.lock for
        # us; nothing more to do.
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
    """Clean indexes can represent valid resolutions; reclaim only proven residue."""
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
    """Collect the blocking markers ``git_dir`` carries.

    Returns ``None`` when a LIVE ``index.lock`` holder means the
    reclaim must defer (E9 contention, NOT staleness). Returns an
    empty list when there is nothing to reclaim at all. Otherwise
    returns the list of marker names (``"index.lock"`` included when
    the holder is provably dead) that justify a reclaim.
    """
    blocking: list[str] = [name for name in _TERMINAL_MARKER_FILES if (git_dir / name).exists()]
    lock_paths = [git_dir / "index.lock", *_common_dir_lock_paths(root)]
    for lock_path in lock_paths:
        if not lock_path.exists():
            continue
        if not _lock_holder_is_dead(lock_path):
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
    """Clear rebase bookkeeping under ``git_dir``.

    A3: a corrupted state dir cannot be aborted with git's own
    commands because git refuses --abort/--continue when
    ``head-name``/``onto``/``todo`` are missing. We trust the
    local ``refs/heads/<branch>`` rather than git's state
    files, so the recover strategy is "delete the state dir,
    then leave the ref alone". A lone ``REBASE_HEAD`` marker
    (no state dir) is removed directly -- ``git rebase --abort``
    is not available for it but the precondition check blocks
    on its presence.
    """
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
    """Drop the progress sidecar of a rebase this recovery just ended.

    ``abort_rebase_discarding_progress`` pairs the two so they cannot
    drift; these branches end a rebase without it. Both restore HEAD to
    ``orig-head``, so retrying reproduces a byte-identical
    ``(orig-head, onto)`` identity -- the sidecar is accepted, its dead
    attempt's stops are read as already landed, and the retry SKIPS a
    genuinely unresolved stop instead of offering it to a resolver.
    Never raises: a sidecar that cannot be cleared must not abort
    recovery.
    """
    from ralph.pipeline.conflict_resolution.progress import clear_progress

    try:
        clear_progress(root)
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("auto_integrate: could not clear the rebase progress record: {}", exc)


def _recover_merge_marker(root: Path, git_dir: Path) -> None:
    """Clear a stale ``MERGE_HEAD`` after attempting ``git merge --abort``.

    ``git merge --abort`` can refuse a synthetic / truncated
    ``MERGE_HEAD``; on a clean tree the marker itself is the only
    thing blocking preconditions, so we remove it directly when
    ``--abort`` could not.
    """
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
    """Clear cherry-pick / revert residue under ``git_dir``.

    A6: sequencer operations leave ``CHERRY_PICK_HEAD`` /
    ``REVERT_HEAD`` / ``.git/sequencer`` on disk. ``sequencer/todo``
    exists iff a sequencer op is paused. Each marker is removed
    individually so partial residue does not leak across recovery
    attempts.
    """
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
    """Re-attach a detached HEAD residue to its original branch.

    A11: detached-HEAD residue (no state dir, HEAD on a raw
    OID) is re-attached to the original branch. The local ref
    still holds the branch tip; the HEAD is just detached, so
    updating HEAD back to ``refs/heads/<branch>`` is enough.
    """
    if not _detached_head_no_state(root, git_dir):
        return
    branch = _detached_branch_name(root)
    if branch is None:
        return
    _reattach_head_to_branch(root, branch)
