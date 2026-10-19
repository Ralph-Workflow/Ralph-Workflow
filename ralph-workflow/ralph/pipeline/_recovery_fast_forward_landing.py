"""Recovery fast-forward landing for owned integration records.

Split from :mod:`ralph.pipeline.auto_integrate_recovery` to keep that
module under the 1000-line repo-structure cap; the landing/verify/
cleanup contract is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backup_refs import qualified_backup_ref
from ralph.pipeline.auto_integrate_context import record_refresh
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_record import clear_record as _clear_record
from ralph.pipeline.auto_integrate_recovery_terminal import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    delete_rebase_backup_refs as _delete_rebase_backup_refs,
)
from ralph.pipeline.auto_integrate_remote_sync import reconcile_remote_publication
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig


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

    Refusals retain the record so the next startup retries. Only a
    proved successful landing releases ownership.

    The successful-landing branch routes through
    :func:`reconcile_remote_publication` so recovery
    can publish a retried local landing to its configured remote. With
    ``config=None`` the pre-seam behavior bypasses remote sync byte-for-byte.

    R6/AC-06: :func:`post_attempt_verify` runs on EVERY exit path of
    the recovery fast-forward BEFORE backup-ref cleanup or record
    clearing. A failed invariant raises
    :class:`TerminalStateViolationError`, which the recovery code
    path then propagates; the backup refs and the durable record
    stay on disk so a subsequent recovery run (or the operator)
    can still read the pre-attempt tip. The previous shape only
    called ``_delete_rebase_backup_refs`` and skipped verification
    entirely, so a recovery landing that left an in-progress
    marker on disk (e.g. a CAS contention that lost a rebase
    state in flight) silently cleared the record and stranded
    the state.
    """
    ff_failed = False
    skip_reason = ""
    try:
        from ralph.pipeline.auto_integrate_remote_sync import reclaim_target_worktree_enabled

        ok, skip_reason = fast_forward_target(
            workspace_root,
            record.target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree_enabled(config),
        )
    except Exception as exc:
        ff_failed = True
        skip_reason = f"fast-forward raised: {exc}"
        logger.warning("recovery: fast_forward_target raised: {}", exc)
        ok = False
    # R6/AC-06: terminal-state verification on EVERY exit path of
    # the recovery fast-forward. The landing either succeeded (no
    # expected-head-sha check needed -- the target moved to
    # feature_sha, not the feature) or recorded a loud retryable
    # skip (the record is retained in that branch below). The
    # verification runs BEFORE backup-ref cleanup or record
    # clearing so a leaked marker can never be silently swept
    # away by the recovery code path.
    expected_head_sha_for_verify: str | None = None
    # The recovery fast-forward moves ``refs/heads/<target>`` to
    # ``feature_sha``, not the feature branch. On the success
    # path the feature's pre-attempt tip is no longer the
    # current HEAD (a successful land left the feature tip at
    # ``feature_sha`` -- and a previous verify pass already
    # accepted that on the integrate path). We only want the
    # HEAD-matches-pre-attempt check to fire on a
    # permanently-aborted landing that left the target moved but
    # the feature stranded, which is a real bug we want to
    # surface. The recovery path's input is always
    # ``record.pre_feature_sha``; passing it is harmless on
    # success (the verifier would only raise if HEAD did not
    # match, which would be the precise leak we want loud) and
    # exactly the right check on a stranded-feature branch.
    # See ``record.pre_feature_sha`` in
    # :mod:`ralph.pipeline.auto_integrate_record`.
    if not ok and not ff_failed:
        # A non-ok landing whose underlying fast_forward_target
        # did NOT raise is the "feature untouched, target did
        # not move" path. The verifier's HEAD-matches check is
        # the right discriminator: a stranded feature branch
        # raises; an expected "advanced concurrently" refusal
        # matches the recorded pre-attempt SHA and passes.
        expected_head_sha_for_verify = feature_sha
    try:
        post_attempt_verify(
            workspace_root,
            expected_head_sha=expected_head_sha_for_verify,
            owns_resolution=False,
        )
    except TerminalStateViolationError:
        # R6/AC-06: invariant failed. Retain the record so the
        # NEXT recovery run (or the operator) can restore the
        # pre-attempt tip from it. Re-raise after retaining --
        # the caller (the recovery preamble in
        # :func:`recover_incomplete_integration`) wraps the
        # entire code path in a broad try/except, so a
        # raised violation still surfaces in the operator log
        # and the durable record survives the wrap.
        logger.warning(
            "recovery: terminal-state invariant violation after fast-forward; "
            "the record and backup refs are RETAINED so the next recovery "
            "can still restore the pre-attempt tip. The leaked marker must "
            "be reclaimed manually or on the next attempt."
        )
        return record_refresh(
            _record_skip(
                reason=("recovery: terminal-state invariant violated; record retained for retry"),
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
                record_retained=True,
            ),
            refresh,
        )
    # R6/AC-06: the invariant passed. Safe to clean up.
    if ok:
        _delete_rebase_backup_refs(
            workspace_root,
            qualified_backup_ref(
                record.owning_worktree or str(workspace_root), record.pre_feature_sha
            ),
        )
        _clear_record(workspace_root)
        return record_refresh(
            reconcile_remote_publication(
                config,
                workspace_root,
                record.target,
                source_state=RebaseState(
                    last_outcome=RebaseOutcomeKind.RECOVERED,
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
                retention_reason=StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE,
                record_retained=True,
            ),
            refresh,
        )
    return record_refresh(
        _record_skip(
            reason=f"recovery: {skip_reason}",
            target=record.target,
            retention_reason=StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE,
            record_retained=True,
        ),
        refresh,
    )


def _record_skip(
    *,
    reason: str,
    target: str | None,
    record_retained: bool = False,
    retention_reason: StartupRetentionReason | None = None,
) -> RebaseState:
    """Build a ``RebaseState`` recording a recovery skip outcome.

    ``record_retained`` marks the skips that left the durable record on
    disk for the next startup to retry. It defaults to ``False`` so a
    skip only claims ownership when its branch explicitly says so; see
    :func:`recovery_retained_record`.
    """
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
        recovery_record_retained=record_retained,
        recovery_record_retained_reason=retention_reason if record_retained else None,
    )
