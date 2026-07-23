"""Startup crash recovery for interrupted auto-integrations (AC-11).

Extracted from :mod:`ralph.pipeline.auto_integrate` to keep that module
under the repo-structure ``_MAX_FILE_LINES`` cap. The public entry
point :func:`recover_incomplete_integration` is re-exported by
``ralph.pipeline.auto_integrate`` so existing imports and monkeypatch
targets keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_NONE,
    abort_merge,
    branch_sha,
    is_ancestor,
    merge_state,
    reset_hard,
)
from ralph.git.operations import is_repo_clean
from ralph.git.rebase.rebase import abort_rebase, rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    refresh_outcome_is_healthy,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    maybe_push_target,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.workspace.scope import WorkspaceScope

#: Mirrors of the outcome verbs in :mod:`ralph.pipeline.auto_integrate`
#: (kept local so this module never imports its re-exporting parent).
_ACTION_SKIPPED = "skipped"
_ACTION_RECOVERED = "recovered"


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


def _rebase_bookkeeping_dir(root: Path) -> Path | None:
    """Resolve the git dir whose rebase bookkeeping blocks preconditions.

    ``git rev-parse --git-dir`` returns the PRIVATE per-worktree dir for
    a linked worktree -- the same dir
    :func:`ralph.git.rebase.rebase_preconditions.check_rebase_preconditions`
    reads its blocking markers from. ``None`` when git cannot be asked;
    the caller treats that as "nothing observable to reclaim".
    """
    try:
        result = run_git(("rev-parse", "--git-dir"), cwd=root, label="recovery-git-dir")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    return git_dir


def _reclaim_unowned_stale_rebase(root: Path) -> RebaseState | None:
    """Reclaim inert rebase state that no ownership record accounts for.

    Failure mode this closes (recorded on wt-23, 2026-07-22):
    a leftover ``rebase-merge``/``rebase-apply`` directory
    or lone ``REBASE_HEAD`` marker with NO durable record fails
    ``check_rebase_preconditions`` at every seam -- startup, phase
    boundaries, the commit seam -- forever, and nothing ever cleans it,
    because recovery's no-record branch used to return ``None``
    unconditionally. That is the forbidden permanent silent noop.

    The discriminator against AC-11 case 4 (an operator's live
    in-progress rebase, which must stay byte-unchanged) is worktree
    cleanliness: a live conflict resolution has unmerged paths and
    tracked modifications, while inert stale state sits on a clean
    tree -- the only shape the boundary hook can meet, since it fires
    exclusively on clean trees.

    Returns ``None`` when there is nothing to reclaim or the state is
    protected (dirty tree); a ``recovered`` state after a successful
    reclaim; a ``skipped`` state when the reclaim itself failed.
    """
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        # Not a git checkout (or git unaskable): nothing observable to
        # reclaim, and the pre-fix contract for the no-record branch --
        # return ``None``, never a synthesized outcome -- must hold.
        return None
    dirs_present = (git_dir / "rebase-apply").exists() or (
        git_dir / "rebase-merge"
    ).exists()
    marker = git_dir / "REBASE_HEAD"
    marker_present = marker.exists()
    merge_marker = git_dir / "MERGE_HEAD"
    merge_present = merge_marker.exists()
    if not dirs_present and not marker_present and not merge_present:
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
    logger.warning(
        "recovery: reclaiming stale unowned rebase/merge state in {} (no"
        " ownership record, clean worktree) -- it was permanently blocking"
        " auto-integration preconditions",
        root,
    )
    try:
        if dirs_present:
            abort_rebase(repo_root=root)
        if marker.exists() and not rebase_in_progress(root):
            # ``git rebase --abort`` is not available for a lone marker
            # file; git itself treats a bare REBASE_HEAD as disposable
            # bookkeeping, but the precondition check blocks on it.
            marker.unlink()
        if merge_present and merge_state(root) != MERGE_STATE_NONE:
            abort_merge(root)
        if merge_marker.exists():
            # ``git merge --abort`` can refuse a synthetic / truncated
            # MERGE_HEAD; on a clean tree the marker itself is the only
            # thing blocking preconditions, so remove it directly.
            merge_marker.unlink()
    except Exception as exc:
        logger.warning(
            "recovery: failed to reclaim stale unowned rebase state: {}", exc
        )
        return _record_skip(
            reason=f"recovery: stale unowned rebase state could not be reclaimed: {exc}",
            target=None,
        )
    return RebaseState(
        last_action=_ACTION_RECOVERED,
        last_reason=(
            "reclaimed stale unowned rebase/merge state (no ownership"
            " record, clean worktree)"
        ),
        last_target=None,
        fast_forwarded=False,
    )


def _record_skip(
    *, reason: str, target: str | None, record_retained: bool = False
) -> RebaseState:
    """Build a ``RebaseState`` recording a recovery skip outcome.

    ``record_retained`` marks the skips that left the durable record on
    disk for the next startup to retry. It defaults to ``False`` so a
    skip only claims ownership when its branch explicitly says so; see
    :func:`recovery_retained_record`.
    """
    return RebaseState(
        last_action=_ACTION_SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
        recovery_record_retained=record_retained,
    )


def _refresh_before_verdict(
    config: UnifiedConfig | None,
    workspace_root: Path,
    target: str,
) -> tuple[str | None, bool]:
    """Re-read the mainline pointer the recovery verdicts are taken from.

    Both verdicts below -- "already landed" and "target advanced
    concurrently" -- are decided from ``branch_sha``, and the second one
    CLEARS the durable record permanently. Taking that decision from a
    pointer this process has never fetched is how a perfectly landable
    integration gets discarded after a crash.

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
) -> RebaseState:
    """Best-effort continue of an unfinished fast-forward (phase='integrated').

    Like :func:`recover_incomplete_integration`, this function
    RETAINS the durable record when the fast-forward fails so the
    next startup can retry. The record is cleared only when
    reconciliation succeeded -- the target ref equals
    ``feature_sha`` (already landed) or the worktree-aware CAS path
    moved it to ``feature_sha`` in this call.

    The defensive branches -- malformed record, target advanced
    concurrently, fast-forward refused -- all clear the record
    because they represent permanent states that further retries
    cannot resolve (no SHA to land, target diverged, ff-only
    refused). The transient failure cases -- an exception raised
    inside ``fast_forward_target`` -- retain the record.

    Those "permanent" verdicts are only permanent when the pointer
    they read is genuinely current, so when a refresh was configured
    and could not establish one this function FAILS CLOSED: it
    returns a retryable skip and RETAINS the record without
    evaluating either ancestry verdict. A refresh that raised or came
    back unreachable used to degrade to ``None`` here, after which a
    stale local pointer could still take the "target advanced
    concurrently" branch and discard a perfectly landable
    integration for good.
    """
    if record.integrated_feature_sha is None:
        # Defensive: malformed record. Clear it so we stop retrying.
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: malformed integrated record", target=record.target
        )
    feature_sha = record.integrated_feature_sha
    # ONE refresh, before either verdict: both of them are taken from the
    # target pointer, and the second one clears the record for good.
    refresh, pointer_is_fresh = _refresh_before_verdict(
        config, workspace_root, record.target
    )
    if not pointer_is_fresh:
        # Fail closed. Neither ancestry verdict below may be taken from
        # a pointer nothing vouched for, because both of them clear the
        # durable record and one of them does so permanently. Retain it
        # and let the next startup retry against a pointer that can be
        # refreshed.
        return record_refresh(
            _record_skip(
                reason=(
                    "recovery: target pointer could not be refreshed,"
                    " record retained for retry"
                ),
                target=record.target,
                record_retained=True,
            ),
            refresh,
        )
    if branch_sha(workspace_root, record.target) == feature_sha:
        # Already landed (another run / operator). Even here we
        # route through the push hook: a sibling that landed
        # locally does not necessarily push to every remote (its
        # config may differ or it may have been crashed mid-push),
        # and the AC-03 contract says the auto-integrate subsystem
        # is the place every local advance reaches every remote
        # when push is enabled.
        _clear_record(workspace_root)
        return record_refresh(
            maybe_push_target(
                config,
                workspace_root,
                record.target,
                RebaseState(
                    last_action=_ACTION_RECOVERED,
                    last_reason="already fast-forwarded",
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    if not is_ancestor(workspace_root, record.target, feature_sha):
        # Target advanced and is no longer an ancestor of the feature
        # SHA: this is a permanent state. Clear the record so we
        # don't keep retrying an impossible land.
        _clear_record(workspace_root)
        return record_refresh(
            _record_skip(
                reason="recovery: target advanced concurrently",
                target=record.target,
            ),
            refresh,
        )
    return _land_and_reconcile(workspace_root, record, feature_sha, refresh, config)


def _land_and_reconcile(
    workspace_root: Path,
    record: IntegrationRecord,
    feature_sha: str,
    refresh: str | None,
    config: UnifiedConfig | None = None,
) -> RebaseState:
    """Run the fast-forward and decide whether the record survives it.

    Split out of :func:`_continue_fast_forward_from_record` so each
    function keeps one job: the caller decides whether the pointer may
    be trusted at all, and this one interprets what the landing did.

    Only a PERMANENT refusal ("advanced concurrently") clears the
    record. A refusal that raised, or any other refusal text, is
    treated as transient and RETAINS the record so the next startup
    retries.

    The successful-landing branch routes through
    :func:`ralph.pipeline.auto_integrate_ff.maybe_push_target` so the
    AC-03 every-successful-advance-pushes contract is honoured at the
    recovery landing site too: a crashed previous run that retried its
    fast-forward on the next startup must push to every configured
    remote just as the happy path does, with ``config=None`` (the
    pre-seam behaviour) bypassing the push byte-for-byte.
    """
    ff_failed = False
    skip_reason = ""
    try:
        ok, skip_reason = fast_forward_target(
            workspace_root, record.target, feature_sha
        )
    except Exception as exc:
        ff_failed = True
        skip_reason = f"fast-forward raised: {exc}"
        logger.warning("recovery: fast_forward_target raised: {}", exc)
        ok = False
    if ok:
        _clear_record(workspace_root)
        return record_refresh(
            maybe_push_target(
                config,
                workspace_root,
                record.target,
                RebaseState(
                    last_action=_ACTION_RECOVERED,
                    last_reason=None,
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    if ff_failed or "advanced concurrently" not in skip_reason:
        # Transient failure: retain the record for retry.
        return record_refresh(
            _record_skip(
                reason=f"recovery: {skip_reason}; record retained for retry",
                target=record.target,
                record_retained=True,
            ),
            refresh,
        )
    # Permanent refusal (target advanced concurrently): clear the
    # record -- a retry won't change the outcome.
    _clear_record(workspace_root)
    return record_refresh(
        _record_skip(reason=f"recovery: {skip_reason}", target=record.target),
        refresh,
    )


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Abort any owned engine rebase/merge in flight before doing
      anything else. If the abort itself FAILS, the durable record
      is RETAINED so the next startup can retry; the returned
      ``RebaseState`` records the abort failure as a skip with
      the original target preserved. The merge check is read via
      :func:`ralph.git.merge.merge_state`, so a FAILED git query
      counts as a possible in-flight merge (abort attempted, record
      retained unless a readable state later proves ``MERGE_HEAD``
      absent) rather than as "nothing to abort".
    * If ``record.phase == 'integrating'``: the rebase/merge never
      completed → restore the feature branch to ``pre_feature_sha``
      via ``reset_hard``. The record is cleared ONLY after
      ``reset_hard`` succeeds AND no owned engine op remains; if
      reset_hard raises or any prior abort left an in-progress
      operation, the record is RETAINED for retry.
    * If ``record.phase == 'integrated'``: the rebase/merge already
      completed and only the fast-forward may be unfinished →
      safely CONTINUE the fast-forward (same worktree-aware CAS path
      used in the happy path).
    * The durable record is cleared only when the recovery path
      confirms (1) no owned git operation remains in flight AND
      (2) the feature SHA was restored to ``pre_feature_sha`` (for
      phase='integrating') OR the fast-forward reconciled cleanly
      (for phase='integrated'). Any failure retains the record so
      the next run can retry the recovery.
    * Every outcome that RETAINS the record sets
      ``RebaseState.recovery_record_retained``, which callers read
      through :func:`recovery_retained_record`. A caller that sees it
      must not start a fresh integration in the same startup: the
      integration writes its own durable record before mutating git
      and would overwrite the unreconciled one.

    Args:
        workspace_scope: Scope whose root holds the durable record.
        config: Run configuration, used ONLY to re-read the mainline
            pointer before the phase='integrated' ancestry verdicts.
            Optional, and ``None`` reproduces the pre-seam behaviour
            exactly, so a caller that holds no config keeps working.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            # No durable record: either nothing happened, or stale
            # unowned rebase bookkeeping is silently blocking every
            # integration seam. Reclaim the latter (clean tree only;
            # a dirty tree is operator-owned and preserved).
            return _reclaim_unowned_stale_rebase(root)

        # Step 1: abort any owned engine op we may have left behind.
        # If EITHER abort fails, retain the record so the next
        # startup can retry. This closes the bug where the prior
        # implementation unconditionally called ``_clear_record``
        # after the abort/reset path and returned
        # ``last_action='recovered'`` -- leaving the repository in
        # a rebase/merge state with no ownership marker.
        abort_failed = False
        try:
            if rebase_in_progress(root):
                if record.resolving_rebase:
                    # Named distinctly so an operator can tell this apart
                    # from an ordinary crashed rebase. The rebase is still
                    # ABORTED, never resumed: the agent session that was
                    # editing it died with the previous process, and a new
                    # process resuming a half-resolved replay it never saw
                    # would land whatever the dead agent happened to have
                    # written when it was killed.
                    logger.warning(
                        "recovery: found a rebase onto '{}' interrupted while a "
                        "conflict-resolution agent was working; aborting it "
                        "(an orphaned resolution is never resumed)",
                        record.target,
                    )
                abort_rebase(repo_root=root)
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_rebase raised: {}", exc)
        try:
            # MERGE_STATE_UNKNOWN deliberately lands in this branch:
            # "git could not be asked" is not evidence that no merge
            # is in flight, so the abort is attempted and only a
            # POSITIVE post-abort MERGE_STATE_NONE counts as success.
            if merge_state(root) != MERGE_STATE_NONE:
                aborted = abort_merge(root)
                if not aborted and merge_state(root) != MERGE_STATE_NONE:
                    abort_failed = True
                    logger.warning(
                        "recovery: merge abort did not prove MERGE_HEAD gone"
                        " in {}",
                        root,
                    )
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_merge raised: {}", exc)

        # Step 2: reconcile by phase.
        if record.phase == "integrating":
            # Restore the feature branch to its pre-integration state.
            reset_failed = False
            try:
                reset_hard(root, record.pre_feature_sha)
            except Exception as exc:
                reset_failed = True
                logger.warning("recovery: reset_hard failed: {}", exc)

            # Verify the restore actually landed: read HEAD and
            # compare to pre_feature_sha. If the reset raised OR
            # the verification fails OR an owned op still remains,
            # RETAIN the record so the next startup can retry. Only
            # when all three checks pass do we clear it.
            restored_ok = (
                not abort_failed
                and not reset_failed
                and not rebase_in_progress(root)
                and merge_state(root) == MERGE_STATE_NONE
                and _head_matches_sha(root, record.pre_feature_sha)
            )
            if not restored_ok:
                return _record_skip(
                    reason=(
                        "recovery: feature branch not restored, record"
                        " retained for retry"
                    ),
                    target=record.target,
                    record_retained=True,
                )
            _clear_record(root)
            return RebaseState(
                last_action=_ACTION_RECOVERED,
                last_reason="restored feature branch after interrupted rebase",
                last_target=record.target,
                fast_forwarded=False,
            )

        # phase == 'integrated': continue the fast-forward. A failed
        # (or unprovable) abort retains the record here too -- clearing
        # it while an owned merge may still be in flight would strand
        # the repository with no ownership marker.
        if abort_failed:
            return _record_skip(
                reason=(
                    "recovery: owned merge not proven aborted, record"
                    " retained for retry"
                ),
                target=record.target,
                record_retained=True,
            )
        return _continue_fast_forward_from_record(root, record, config)
    except Exception as exc:
        # Deliberately NOT marked ``record_retained``. This is the
        # catch-all for an unexpected failure anywhere above, including
        # before the record was even read, so it cannot honestly claim
        # ownership of a record it may never have seen. Claiming it
        # would gate the startup catch-up behind a fault that has no
        # bounded end, which is the opposite of recovery's fail-open
        # contract. The named retention branches above are the ones that
        # know a record is on disk.
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}", target=None
        )


def _head_matches_sha(repo_root: Path, expected_sha: str) -> bool:
    """Return True when ``HEAD`` resolves to ``expected_sha``.

    Uses ``git rev-parse --verify HEAD`` (a substring match is
    accepted since git treats an unambiguous prefix as a SHA).
    Returns False on any failure so the recovery path retains the
    durable record when verification is impossible -- a defensive
    fail-closed against a half-restored feature branch that we
    cannot prove equals ``pre_feature_sha``.
    """
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=repo_root,
            label="git-rev-parse-head",
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == expected_sha
