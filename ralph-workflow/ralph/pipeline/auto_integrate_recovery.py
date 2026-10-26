"""Startup crash recovery for interrupted auto-integrations (AC-11).

This module is the entry point. The reusable pieces live next to it:

* :mod:`ralph.pipeline.auto_integrate_recovery_legacy` -- the
  ``legacy_rebase_startup_block`` entry point and the per-marker
  reclaim helpers that close the wt-23 silent-noop bug.
* :mod:`ralph.pipeline.auto_integrate_recovery_record` -- the
  diagnostic-evidence / record-store path: target refresh, the
  fast-forward / land-and-reconcile continuations, the
  BUSY/UNREADABLE refusal mapping, and the pending-merge /
  pending-rebase continuation.

Every private helper the broader pipeline imports by name from
``ralph.pipeline.auto_integrate_recovery`` is re-exported here so the
seam stays stable.

Decision ownership
------------------

The **mutation ladder** -- which integration rung the boundary must
climb next -- is decided by the single owner
:func:`ralph.pipeline.integration_owner.decide_integration`, reached
through the one seam :func:`ralph.pipeline._integration_owner_seam.decide_seam_action`.
This module asks the seam and acts on its verdict; it never re-derives
the rung from the durable record.

The **record facts the pure observation table cannot express** stay
with the seam:

* ``REQUIRE_INTERVENTION`` -- derived from the persisted retry
  deadline and the exhausted-diagnostics escalation, both of which are
  durable record facts rather than observations.
* the completion / publication verdicts (``COMPLETE_WITH_PROOF``,
  ``PUBLISH_AFTER_LANDING``) -- derived from the record's landing
  proof.

The split is deliberate: the owner decides *what to do to the
integration state*, the record facts decide *whether the obligation is
already finished*.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, abort_merge, branch_sha, merge_state
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
)
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_diagnostic import (
    busy_refusal_outcome as _canonical_busy_refusal_outcome,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline._recovery_fast_forward_landing import (
    _land_and_reconcile,
    _record_skip,
)
from ralph.pipeline.auto_integrate_record import (
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
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_recovery_legacy import (
    _reclaim_unowned_stale_rebase,
    legacy_rebase_startup_block,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    _continue_fast_forward_from_record,
    _maybe_release_diagnostic,
    _read_outcome_or_skip,
    _recover_pending_merge,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    TerminalStateViolationError,
    _head_matches_sha,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_remote_publication,
    remote_sync_enabled,
    remote_target_name,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_director import DEFAULT_REMOTE
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "TerminalStateViolationError",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "post_attempt_verify",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction | RecordFactOutcome:
    """Map a persisted ``RebaseState.retry_deadline`` to the typed retry decision.

    Delegates to the canonical mapping in
    :mod:`ralph.pipeline._integration_record_facts`: an unexpired
    persisted deadline yields ``WAIT_FOR_BOUNDED_RETRY``; a missing,
    malformed, or expired deadline fails closed to the typed
    :attr:`RecordFactOutcome.STALE_INTENT` verdict, which the caller
    routes to the autonomous fallback ladder — never an operator
    hand-off.

    The verdict is a **durable record fact**, not an observation: the
    persisted deadline is what bounds the wait and the pure observation
    table has no deadline dimension, so the owner cannot decide it. The
    seam keeps the deadline rule; see the module docstring.
    """
    return _canonical_busy_refusal_outcome(retry_deadline, clock=clock)


class _StartupRecoveryRoute(StrEnum):
    """The startup recovery path the owner's seam verdict selects.

    The startup boundary never picks a recovery path from raw record
    flags; it consumes the single owner's verdict
    (:func:`ralph.pipeline._integration_owner_seam.decide_seam_action`)
    projected onto this closed route vocabulary.
    """

    RECOVERY_OWNS_OPERATION = "recovery_owns_operation"
    LANDING_CONTINUATION = "landing_continuation"
    DEFER_INTERVENTION = "defer_intervention"
    DEFER_RETRY = "defer_retry"


def _startup_recovery_route(
    action: IntegrationAction | RecordFactOutcome,
    record: IntegrationRecord,
) -> _StartupRecoveryRoute:
    """Map the owner's seam verdict onto the startup recovery route.

    The owner is always asked first; its verdict gates every state that
    is not a durable record fact.  Three record facts cannot be expressed
    by the owner's pure observation and stay with the seam (see
    :mod:`ralph.pipeline._integration_record_facts`):

    * an owned in-flight Git operation, a target reconciliation or a
      scheduled re-integration (``classify_git_operation(record)`` is not
      ``NONE``, ``operation_kind == "target_reconcile"``,
      ``reintegrate_pending``) keeps the pending-merge recovery path that
      performs the owed operation -- including the stale-intent replay
      that re-enters at the automatic rebase;
    * an ``integrated`` record whose fast-forward landing is still owed:
      until the landing the target is behind the feature, so the owner
      reads the state as "re-integrate" and the record fact decides the
      landing split.

    For everything else the seam verdict is the whole decision: a typed
    fail-closed :class:`RecordFactOutcome` verdict (a spent diagnostic
    budget, a stale retained intent) and ``REQUIRE_INTERVENTION`` defer
    without mutating — the typed outcome is routed to the autonomous
    fallback ladder by the boundary that owns the durable ledger — and
    every mutation-ladder action routes to the recovery path that owns
    the obligation.
    """
    if (
        classify_git_operation(record) is not GitOperationKind.NONE
        or record.operation_kind == "target_reconcile"
        or record.reintegrate_pending
    ):
        return _StartupRecoveryRoute.RECOVERY_OWNS_OPERATION
    if record.phase == "integrated":
        return _StartupRecoveryRoute.LANDING_CONTINUATION
    if isinstance(action, RecordFactOutcome):
        return _StartupRecoveryRoute.DEFER_INTERVENTION
    if action is IntegrationAction.REQUIRE_INTERVENTION:
        return _StartupRecoveryRoute.DEFER_INTERVENTION
    if action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY:
        return _StartupRecoveryRoute.DEFER_RETRY
    return _StartupRecoveryRoute.RECOVERY_OWNS_OPERATION


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


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
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
        # Startup seam: if a previous run landed locally but never
        # pushed, the reconciler catches up on this start. The
        # publication outcome is annotated on the durable record and
        # on the recovery's RebaseState return; the next stage
        # consumes it like any other landing.
        startup_publication: RebaseState | None = None
        if config is not None and remote_sync_enabled(config):
            target_obj = _configured_target(config)
            if isinstance(target_obj, str) and target_obj:
                startup_publication = reconcile_remote_publication(
                    config,
                    Path(workspace_scope.root),
                    target_obj,
                    remote=remote_target_name(config),
                    rebase_stop_resolver=rebase_stop_resolver,
                )

        record_outcome = read_record_outcome(Path(workspace_scope.root), backend=backend)
        if record_outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            # Fail closed: the unreadable bytes stay untouched as
            # ownership evidence; the typed ladder-escalation outcome
            # keeps the obligation blocking while the autonomous
            # fallback ladder escalates — never a human hand-off.
            reason = (
                "durable integration record unreadable; escalating through the "
                "autonomous fallback ladder; record retained, bytes preserved"
            ) + (
                f" ({record_outcome.unreadable_reason})" if record_outcome.unreadable_reason else ""
            )
            return RebaseState(
                last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
                last_reason=reason,
                last_target=None,
                recovery_record_retained=True,
                recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            )

        early = _read_outcome_or_skip(Path(workspace_scope.root), backend)
        if early is not None:
            return early
        record = _maybe_release_diagnostic(Path(workspace_scope.root), workspace_scope, backend)
        if isinstance(record, RebaseState):
            return record
        recovered = _recover_incomplete_integration_owned(
            workspace_scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            backend=backend,
        )
        if (
            recovered is None
            and startup_publication is not None
            and (
                startup_publication.last_push_status == "pushed"
                or (
                    startup_publication.last_publication_decision is not None
                    and startup_publication.last_publication_decision
                    != PublicationDecision.PUBLICATION_TERMINAL
                )
            )
        ):
            return startup_publication
        return recovered


def _recover_incomplete_integration_owned(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
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
    * The mutation ladder is asked of the single owner through the one
      seam (:func:`ralph.pipeline._integration_owner_seam.decide_seam_action`);
      the durable record decides only whether the obligation is still an
      integration or already a landing.

    Args:
        workspace_scope: Scope whose root holds the durable record.
        config: Run configuration for owned continuation and target refresh.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root, backend=backend)
        if record is None:
            return _recover_without_durable_record(root)

        # The owner decides the mutation ladder: it observes the
        # worktree and the durable record, loads the durable attempt
        # ledger and returns the one rung the boundary must climb next.
        # No agent phase is active during startup recovery, so the
        # observation records that fact. The observation itself is built
        # by the director -- the one observation owner -- against the
        # CONFIGURED target and remote (``main`` and ``origin`` are
        # defaults only), and the owner then decides on it. The boundary
        # asks through the ONE seam, so the durable record facts are
        # overlaid on the owner's answer.
        action = decide_seam_action(
            root,
            record=record,
            agent_active=False,
            ledger_path=None,
            retained=True,
            target=_configured_target(config) if config is not None else record.target,
            remote=remote_target_name(config) if config is not None else DEFAULT_REMOTE,
        )

        # The owner decided the mutation ladder above; the boundary now
        # consumes its verdict through the ONE typed route.  No inline
        # record-flag decision is made here beyond the durable record
        # facts the owner's projection cannot express (documented on
        # :func:`_startup_recovery_route`).
        route = _startup_recovery_route(action, record)

        if route is _StartupRecoveryRoute.LANDING_CONTINUATION:
            # Record-fact completion: the work is integrated and the
            # fast-forward landing path owns it. The owner's verdict
            # cannot express that landing -- until the fast-forward
            # lands, the target is behind the feature and the owner
            # reads the state as "re-integrate" -- so the durable
            # record decides this split.
            return _recover_landing_from_record(
                root, record, config, conflict_resolver, rebase_stop_resolver
            )

        if route is _StartupRecoveryRoute.DEFER_INTERVENTION:
            # The durable record carries a fail-closed typed verdict (a
            # spent diagnostic budget or a stale retained intent) and the
            # live Git state does not prove the claim stale, so the
            # boundary must not mutate: the record is retained and the
            # typed LADDER_ESCALATION outcome names the next autonomous
            # action — the fallback ladder, rung 6 hard reset as the
            # last resort, never a person. (The seam can only ever turn
            # the owner's rung into this one; the owner's own answer
            # never carries it.)
            deferred: RebaseState | None = RebaseState(
                last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
                last_reason=(
                    "recovery: durable record requires autonomous ladder escalation; "
                    "record retained, escalating through the fallback ladder"
                ),
                last_target=record.target,
                recovery_record_retained=True,
                recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            )
        elif route is _StartupRecoveryRoute.DEFER_RETRY:
            # The owner could not read the environment (or the escalation
            # ladder is exhausted): the boundary must not mutate. Defer
            # with the record retained so the next startup re-observes.
            deferred = _record_skip(
                reason="recovery: integration ladder deferred; durable record retained for retry",
                target=record.target,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        else:
            deferred = None
        if deferred is not None:
            return deferred

        # RECOVERY_OWNS_OPERATION: an owned Git operation, a target
        # reconciliation, a scheduled re-integration, or a
        # mutation-ladder verdict (RESOLVE / CONTINUE / REINTEGRATE) all
        # mean the integration is still owed; the pending-merge recovery
        # path performs it, including the stale-intent replay that drops
        # flags Git disproves and re-enters at the automatic rebase.
        return _recover_pending_merge(root, record, config, conflict_resolver, rebase_stop_resolver)

    except Exception as exc:
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}",
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )


def _recover_without_durable_record(root: Path) -> RebaseState | None:
    """Recover when no durable integration record exists on disk.

    A retained integration reason keeps the obligation visible; a stale
    rebase nobody owns is reclaimed. Nothing else is touched: an
    operator's own Git operation is never disturbed.
    """
    retained = retained_integration_reason(root)
    if retained is not None:
        return _record_skip(
            reason=retained,
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )
    return _reclaim_unowned_stale_rebase(root)


def _recover_landing_from_record(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    """Continue the fast-forward landing an integrated record still owes.

    A later Git operation that the record does not own blocks the
    landing: the record is retained so the operator can finish it.
    """
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


# Re-export the private helpers imported by sibling ralph/ modules
# (notably :mod:`ralph.pipeline._owned_landing_reintegration`,
# :mod:`ralph.pipeline.auto_integrate_precondition`, and
# :mod:`ralph.pipeline.auto_integrate_catalog_rationales`) so their
# existing import paths keep resolving.
__all__ = [
    "MERGE_STATE_NONE",
    "REFRESH_UNREACHABLE",
    "IntegrationRecord",
    "TerminalStateViolationError",
    "_clear_record",
    "_head_matches_sha",
    "_land_and_reconcile",
    "_read_record",
    "_refresh_target",
    "abort_merge",
    "abort_rebase_discarding_progress",
    "ancestry_state",
    "apply_record_transition",
    "branch_sha",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "merge_state",
    "post_attempt_verify",
    "read_record_outcome",
    "rebase_in_progress",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


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
# AC-14 rationale: F6
# ladder rung: 3
# AC-14 rationale: G3
# ladder rung: 1
# AC-14 rationale: G4
# ladder rung: 1
# AC-14 rationale: H7
# ladder rung: 4
