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
    abort_merge,
    branch_sha,
    is_ancestor,
    merge_in_progress,
    reset_hard,
)
from ralph.git.rebase.rebase import abort_rebase, rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.workspace.scope import WorkspaceScope

#: Mirrors of the outcome verbs in :mod:`ralph.pipeline.auto_integrate`
#: (kept local so this module never imports its re-exporting parent).
_ACTION_SKIPPED = "skipped"
_ACTION_RECOVERED = "recovered"


def _record_skip(*, reason: str, target: str | None) -> RebaseState:
    """Build a ``RebaseState`` recording a recovery skip outcome."""
    return RebaseState(
        last_action=_ACTION_SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
    )


def _continue_fast_forward_from_record(
    workspace_root: Path,
    record: IntegrationRecord,
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
    """
    if record.integrated_feature_sha is None:
        # Defensive: malformed record. Clear it so we stop retrying.
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: malformed integrated record", target=record.target
        )
    feature_sha = record.integrated_feature_sha
    if branch_sha(workspace_root, record.target) == feature_sha:
        # Already landed (another run / operator).
        _clear_record(workspace_root)
        return RebaseState(
            last_action=_ACTION_RECOVERED,
            last_reason="already fast-forwarded",
            last_target=record.target,
            fast_forwarded=True,
        )
    if not is_ancestor(workspace_root, record.target, feature_sha):
        # Target advanced and is no longer an ancestor of the feature
        # SHA: this is a permanent state. Clear the record so we
        # don't keep retrying an impossible land.
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: target advanced concurrently", target=record.target
        )
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
        return RebaseState(
            last_action=_ACTION_RECOVERED,
            last_reason=None,
            last_target=record.target,
            fast_forwarded=True,
        )
    # Fast-forward was refused or raised. Only clear the record for
    # permanent refusals (target advanced concurrently) -- retain it
    # for transient failures so the next startup retries.
    if ff_failed or "advanced concurrently" not in skip_reason:
        # Transient failure: retain the record for retry.
        return RebaseState(
            last_action=_ACTION_SKIPPED,
            last_reason=(
                f"recovery: {skip_reason}; record retained for retry"
            ),
            last_target=record.target,
            fast_forwarded=False,
        )
    # Permanent refusal (target advanced concurrently): clear the
    # record -- a retry won't change the outcome.
    _clear_record(workspace_root)
    return _record_skip(reason=f"recovery: {skip_reason}", target=record.target)


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Abort any owned engine rebase/merge in flight before doing
      anything else. If the abort itself FAILS, the durable record
      is RETAINED so the next startup can retry; the returned
      ``RebaseState`` records the abort failure as a skip with
      the original target preserved.
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

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            return None

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
                abort_rebase(repo_root=root)
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_rebase raised: {}", exc)
        try:
            if merge_in_progress(root):
                abort_merge(root)
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
                and not merge_in_progress(root)
                and _head_matches_sha(root, record.pre_feature_sha)
            )
            if not restored_ok:
                return RebaseState(
                    last_action=_ACTION_SKIPPED,
                    last_reason=(
                        "recovery: feature branch not restored, record"
                        " retained for retry"
                    ),
                    last_target=record.target,
                    fast_forwarded=False,
                )
            _clear_record(root)
            return RebaseState(
                last_action=_ACTION_RECOVERED,
                last_reason="restored feature branch after interrupted rebase",
                last_target=record.target,
                fast_forwarded=False,
            )

        # phase == 'integrated': continue the fast-forward.
        return _continue_fast_forward_from_record(root, record)
    except Exception as exc:
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
