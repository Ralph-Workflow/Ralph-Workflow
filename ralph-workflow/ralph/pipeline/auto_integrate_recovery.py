"""Startup crash recovery for interrupted auto-integrations (AC-11)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    abort_merge,
    branch_sha,
    merge_state,
)
from ralph.git.merge_obstructions import ancestry_state
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
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
    is_in_active_resolution_state,
)
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._owned_landing_reintegration import (
    reintegrate_owned_landing as _reintegrate_owned_landing,
)
from ralph.pipeline._pending_merge_commit import _git_value, resume_pending_merge
from ralph.pipeline._pending_rebase_continue import resume_pending_rebase
from ralph.pipeline._recovery_fast_forward_landing import (
    _land_and_reconcile,
    _record_skip,
)
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    refresh_outcome_is_healthy,
)
from ralph.pipeline.auto_integrate_ff import (
    maybe_push_target,
)
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_MERGE,
    MARK_RESOLVING_REBASE,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_record import (
    write_record as _write_record,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    _TERMINAL_MARKER_FILES,
    TerminalStateViolationError,
    _head_matches_sha,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "TerminalStateViolationError",
    "legacy_rebase_startup_block",
    "post_attempt_verify",
    "recover_incomplete_integration",
    "recovery_retained_record",
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


def recovery_retained_record(state: RebaseState | None) -> bool:
    """Whether ``state`` came back still OWNING the durable record.

    :func:`recover_incomplete_integration` has two shapes of outcome.
    Either it reconciled the interrupted operation -- cleared the
    durable :class:`~ralph.pipeline.auto_integrate_record.IntegrationRecord`
    after proving the feature branch was restored or the fast-forward
    landed -- or it hit a transient failure and deliberately LEFT the
    record on disk so the next startup can retry it.

    In the second shape recovery is not finished, and the caller must
    NOT start a fresh integration in the same startup:
    ``auto_integrate._integrate_once`` writes a new
    ``IntegrationRecord(phase='integrating', ...)`` before it touches
    git, which overwrites the only durable metadata describing the
    still-unreconciled operation -- the pre-integration feature SHA a
    later recovery needs to restore, and the target the interrupted
    landing was aimed at.

    Callers read the retention fact THROUGH this predicate rather than
    from ``last_reason``: that string is operator display text, is
    formatted differently on every branch, and interpolates exception
    messages. ``None`` (no record existed, recovery was a no-op) is not
    retained.
    """
    return state is not None and state.recovery_record_retained


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
    Live operation metadata therefore prevents reclamation even on a
    clean tree. Only malformed residue without a usable operation
    identity reaches cleanup; dirty work is preserved independently.

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


def _refresh_before_verdict(
    config: UnifiedConfig | None,
    workspace_root: Path,
    target: str,
) -> tuple[str | None, bool]:
    """Re-read the mainline pointer the recovery verdicts are taken from.

    Landing and reintegration must use a refreshed target observation;
    failed refresh preserves the durable obligation for retry.

    Returns ``(outcome, pointer_is_fresh)``:

    * ``outcome`` is the ``REFRESH_*`` verb the caller stamps on the
      returned state, or ``None`` when no config was supplied.
    * ``pointer_is_fresh`` says whether the target pointer the verdicts
      are about to read can be trusted. It is ``True`` when no config
      was supplied -- that caller opted out of the seam entirely and
      keeps the pre-seam behaviour byte-for-byte -- and otherwise only
      when the refresh actually returned a HEALTHY outcome
      (:func:`~ralph.pipeline.auto_integrate_context.refresh_outcome_is_healthy`).
      A refresh that RAISED, or that came back unreachable / diverged /
      race-lost, establishes no fresh verdict, so the caller must fail
      closed rather than clear the durable record from a pointer
      nothing vouched for.

    A raise is reported as
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
    rather than ``None`` so the skip the caller returns names why the
    landing was deferred instead of looking like no refresh was even
    attempted. Never raises: recovery's contract is that it swallows
    everything.
    """
    if config is None:
        return None, True
    try:
        outcome = _refresh_target(config, workspace_root, target)
    except Exception as exc:
        logger.warning("recovery: target refresh raised: {}", exc)
        return REFRESH_UNREACHABLE, False
    return outcome, refresh_outcome_is_healthy(outcome)


def _continue_fast_forward_from_record(
    workspace_root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    """Best-effort continue of an unfinished fast-forward (phase='integrated').

    # AC-14 rationale: F6
    # AC-14 rationale: G3
    # AC-14 rationale: G4
    # AC-14 rationale: H7
    Retain transient failures and missing completion identities for retry.
    Clear ownership only after a verified landing. Target movement schedules
    owned reintegration. When refresh cannot establish a current target pointer,
    fail closed and retain the record rather than deciding from stale state.
    """
    feature_sha = record.integrated_feature_sha
    if feature_sha is None or not _head_matches_sha(workspace_root, feature_sha):
        return _record_skip(
            reason=(
                "recovery: malformed integrated record; verified feature SHA missing"
                if feature_sha is None
                else "recovery: feature tip changed or unreadable; landing withheld"
            ),
            target=record.target,
            retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
            record_retained=True,
        )
    refresh, pointer_is_fresh = _refresh_before_verdict(config, workspace_root, record.target)
    if not pointer_is_fresh:
        # Fail closed. Neither ancestry verdict below may be taken from
        # a pointer nothing vouched for, because both of them clear the
        # durable record and one of them does so permanently. Retain it
        # and let the next startup retry against a pointer that can be
        # refreshed.
        return record_refresh(
            _record_skip(
                reason=(
                    "recovery: target pointer could not be refreshed, record retained for retry"
                ),
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
                record_retained=True,
            ),
            refresh,
        )
    if branch_sha(workspace_root, record.target) == feature_sha:
        # Already landed (another run / operator). Route through the push
        # hook so an interrupted prior run can still publish the target to
        # its configured remote without changing the local landing.
        _clear_record(workspace_root)
        return record_refresh(
            maybe_push_target(
                config,
                workspace_root,
                record.target,
                RebaseState(
                    last_outcome=RebaseOutcomeKind.RECOVERED,
                    last_reason="already fast-forwarded",
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    ancestry = ancestry_state(workspace_root, record.target, feature_sha)
    if ancestry is None:
        return record_refresh(
            _record_skip(
                reason="recovery: target ancestry unreadable; record retained for retry",
                target=record.target,
                retention_reason=StartupRetentionReason.TARGET_POINTER_UNREFRESHABLE,
                record_retained=True,
            ),
            refresh,
        )
    if not ancestry:
        return _reintegrate_owned_landing(
            workspace_root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    return _land_and_reconcile(workspace_root, record, feature_sha, refresh, config)


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    """Recover owned Git operations under an exclusive worktree lease.

    ``workspace_scope`` selects the repository; optional ``config`` controls
    target freshness and publication. Return the recovery verdict, or ``None``
    when no operation needs recovery. Contention retains recovery state for
    retry. Restoration refuses to overwrite unfinished edits, and a durable
    record survives until restoration or target landing is proven.
    """
    with integration_transaction(Path(workspace_scope.root)) as acquired:
        if not acquired:
            return _record_skip(
                reason="integration worktree busy; recovery will retry",
                target=None,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        try:
            outcome = read_record_outcome(Path(workspace_scope.root))
        except Exception as exc:
            logger.critical("Integration ownership unreadable; recovery retained: {}", exc)
            return _record_skip(
                reason=f"integration ownership unreadable: {exc}",
                target=None,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            # Decision-complete startup handoff (U4d): a PRESENT-but-unreadable
            # record cannot answer the recovery authorities, so the startup
            # path returns the same typed intervention decision as the other
            # entry points instead of a generic retained skip. The unreadable
            # bytes stay untouched on disk as ownership evidence.
            reason = "durable integration record unreadable; intervention required" + (
                f" ({outcome.unreadable_reason})" if outcome.unreadable_reason else ""
            )
            return RebaseState(
                last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
                last_reason=reason,
                last_target=None,
                recovery_record_retained=True,
                recovery_record_retained_reason=(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK),
            )
        record = outcome.record
        if (
            record is not None
            and record.diagnostic_evidence
            and record.operation_kind != "target_reconcile"
        ):
            from ralph.pipeline._diagnostic_evidence_gate import diagnostic_evidence_gate_reason
            from ralph.pipeline._integration_diagnostic import release_verified_diagnostic
            from ralph.pipeline.operation_evidence import EvidenceVerdict

            verdict = release_verified_diagnostic(
                Path(record.owning_worktree or workspace_scope.root), record
            )
            if verdict == EvidenceVerdict.UNCHANGED:
                record = record.with_transition(
                    RELEASE_DIAGNOSTIC_EVIDENCE,
                    diagnostic_evidence=(),
                    diagnostic_ownership=None,
                )
                _write_record(Path(workspace_scope.root), record)
            elif verdict == EvidenceVerdict.CHANGED:
                return _record_skip(
                    reason=diagnostic_evidence_gate_reason(verdict),
                    target=record.target,
                    retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                    record_retained=True,
                )
            else:
                return _record_skip(
                    reason=diagnostic_evidence_gate_reason(verdict),
                    target=record.target,
                    retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                    record_retained=True,
                )
        return _recover_incomplete_integration_owned(
            workspace_scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
        )


def _recover_unresolved_integrating(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    if merge_state(root) not in {MERGE_STATE_NONE, MERGE_STATE_IN_PROGRESS}:
        return _record_skip(
            reason="recovery: merge state unreadable; record retained for retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    return _reintegrate_owned_landing(
        root,
        record,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )


def _recover_pending_merge(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    if record.operation_kind == "target_reconcile":
        from ralph.pipeline._remote_reconcile_recovery import recover_target_resolution

        return recover_target_resolution(
            root, record, config, conflict_resolver, rebase_stop_resolver
        )
    if (
        next_integration_action(record) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
        and record.merge_commit_tree is None
    ):
        return _recover_unresolved_integrating(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    if (
        (record.resolving_merge or record.resolving_rebase)
        and not rebase_in_progress(root)
        and merge_state(root) == MERGE_STATE_NONE
        and is_repo_clean(root)
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.pre_feature_sha
    ):
        cleared_record = record.with_transition(
            MARK_RESOLVING_REBASE, resolving_rebase=False
        ).with_transition(MARK_RESOLVING_MERGE, resolving_merge=False)
        return _reintegrate_owned_landing(
            root,
            cleared_record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    kind = classify_git_operation(record)
    if kind in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.RESOLVING_REBASE):
        from ralph.pipeline._integration_continuation import continue_retained_resolution

        resumed = continue_retained_resolution(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    else:
        resumed = (
            resume_pending_rebase(root, record)
            if kind is GitOperationKind.AWAITING_CONTINUE
            else resume_pending_merge(root, record)
        )
    if isinstance(resumed, str):
        logger.critical("CRITICAL: {}", resumed)
        if is_in_active_resolution_state(record):
            from ralph.pipeline.auto_integrate_outcome import record_conflict
            from ralph.pipeline.rebase_state import ResolutionFailureCause

            conflicted = record_conflict(
                reason=resumed,
                target=record.target,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
            return conflicted.set_recovery_record_retained(
                StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH
            )
        return _record_skip(
            reason=resumed,
            target=record.target,
            record_retained=True,
            retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
        )
    return _continue_fast_forward_from_record(
        root,
        resumed,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )


def _recover_incomplete_integration_owned(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Preserve agent-owned resolution and verified pending commit/continuation
      records; retry prepared operations before considering generic cleanup.
    * Every outcome that RETAINS the record sets
      ``RebaseState.recovery_record_retained``, which callers read
      through :func:`recovery_retained_record`. A caller that sees it
      must not start a fresh integration in the same startup: the
      integration writes its own durable record before mutating git
      and would overwrite the unreconciled one.

    Args:
        workspace_scope: Scope whose root holds the durable record.
        config: Run configuration for owned continuation and target refresh.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            from ralph.pipeline.integration_resolution import retained_integration_reason

            retained = retained_integration_reason(root)
            return (
                _record_skip(
                    reason=retained,
                    target=None,
                    record_retained=True,
                    retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                )
                if retained is not None
                else _reclaim_unowned_stale_rebase(root)
            )

        if (
            classify_git_operation(record) is not GitOperationKind.NONE
            or (
                record.operation_kind == "feature_integrate"
                and next_integration_action(record) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
            )
            or (record.operation_kind == "target_reconcile")
        ):
            return _recover_pending_merge(
                root, record, config, conflict_resolver, rebase_stop_resolver
            )

        if rebase_in_progress(root) or merge_state(root) != MERGE_STATE_NONE:
            return _record_skip(
                reason="recovery: later Git operation is not owned by completed landing; finish it before retry",
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
                record_retained=True,
            )
        return _continue_fast_forward_from_record(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )

    except Exception as exc:
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}",
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )


# ----- AC-14 catalog evidence -----
# AC-14 rationale: A1
# ladder rung: 2
# AC-14 rationale: A10
# ladder rung: 2
# AC-14 rationale: A11
# ladder rung: 2
# AC-14 rationale: A3
# ladder rung: 2
# AC-14 rationale: A4
# ladder rung: 1
# AC-14 rationale: A5
# ladder rung: 2
# AC-14 rationale: A6
# ladder rung: 2
# AC-14 rationale: A9
# ladder rung: 2
# AC-14 rationale: C5
# ladder rung: 2
# AC-14 rationale: E10
# ladder rung: 4
# AC-14 rationale: E7
# ladder rung: 1
# AC-14 rationale: E9
# ladder rung: 3
