"""Resolve the rebase in place before merge on unresolved conflict."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.merge import branch_sha
from ralph.git.operations import get_head_sha
from ralph.git.rebase import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._commit_completion_authority import complete_commit
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_continuation import (
    recover_before_attempt as _recover_before_attempt,
)
from ralph.pipeline._target_reconciliation_handoff import scheduled_landing
from ralph.pipeline.auto_integrate_backoff import wait_before_retry
from ralph.pipeline.auto_integrate_boundary import (
    phase_boundary_outcome as _phase_boundary_outcome,
)
from ralph.pipeline.auto_integrate_budget_seam import (
    apply_completed_attempt_budget,
    carry_budget_through_skip,
    charge_failed_attempt,
    observe_conflict_identity,
)
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    resolver_allowed,
)
from ralph.pipeline.auto_integrate_context import (
    current_branch_or_detached_marker as _current_branch_or_detached_marker,
)
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
)
from ralph.pipeline.auto_integrate_ff import (
    is_retryable_fast_forward_failure,
)
from ralph.pipeline.auto_integrate_outcome import (
    record_rebase_outcome as _record_rebase_outcome,
)
from ralph.pipeline.auto_integrate_outcome import (
    record_skip as _record_skip,
)
from ralph.pipeline.auto_integrate_precondition import (
    _handle_precondition_failure,
    _read_post_integration_head_sha,
)
from ralph.pipeline.auto_integrate_rebase_merge import (
    run_rebase_or_merge as _run_rebase_or_merge,
)
from ralph.pipeline.auto_integrate_rebase_merge import (
    with_skip_reason,
)
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    write_record as _write_record,
)
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_refresh import (
    refresh_target as _refresh_target,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    RemoteSyncStatus,
    pull_and_reconcile_target,
    reconcile_after_rejected_push,
    reconcile_remote_publication,
    remote_sync_enabled,
)
from ralph.pipeline.auto_integrate_resolution_state import (
    CONFLICT_RESOLUTION_STRATEGIES,
    contained_land,
    containment_release,
    preserve_unresolved_resolution_state,
)
from ralph.pipeline.auto_integrate_terminal import (
    verify_and_cleanup_backup as _verify_and_cleanup_backup,
)
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.commit_executor import (
    reattach_head_if_detached as _reattach_head_if_detached,
)
from ralph.pipeline.integration_actions import create_backup_ref as _create_rebase_backup_ref
from ralph.pipeline.integration_actions import (
    current_agent_runner,
    current_git_ops,
    install_executors,
    reset_executors,
)
from ralph.pipeline.integration_actions import delete_backup_ref as _delete_rebase_backup_ref
from ralph.pipeline.integration_actions import fast_forward_target as _fast_forward_target
from ralph.pipeline.rebase_state import StartupRetentionReason

_random = (
    random.Random()
)  # module-local RNG: thread-safe jitter without mutating global random state

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import AgentRunner, GitOps
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope


def auto_integrate_after_commit(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _random.random,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> RebaseState | None:
    """Integrate after a commit, recording skips and surfacing terminal-state violations.

    ``git_ops`` and ``agent_runner`` are the injected MUTATION seams: every Git
    mutation and the resolver dispatch of this attempt runs through them.
    """
    executors_token = install_executors(git_ops, agent_runner)
    try:
        # An unresolved integration must not SKIP this seam: this seam is the
        # recovery path, and returning `state` here left the only operation
        # able to clear the verdict blocked by the verdict. The evidence is
        # protected on the way OUT -- see ``preserve_unresolved_resolution_state``.
        from ralph.pipeline.integration_resolution import retained_integration_reason

        root = Path(workspace_scope.root)
        target = resolve_integration_target(config, root) if (root / ".git").exists() else None
        # Git is ground truth: release a contained integration before any
        # blocking decision, so a retained record cannot outlive its work.
        state = containment_release(root, target, state)
        # A non-git workspace has no integration target to probe; the
        # context resolver intentionally propagates repo errors for real
        # repos, so exclude this no-op case before it can raise.
        if retained_integration_reason(root) is None and (root / ".git").exists():
            ctx = _auto_integrate_resolve_context(config, workspace_scope)
            early_skip, _usable_ctx = _check_early_skips(
                ctx,
                missing_target=_configured_target(config),
            )
            if early_skip is not None:
                return carry_budget_through_skip(
                    early_skip,
                    prior=state,
                    attempts=config.conflict_resolution.max_consecutive_resolver_attempts,
                )

        identity = observe_conflict_identity(root, target or "") if target else ConflictIdentity()
        resolver_attempts = config.conflict_resolution.max_consecutive_resolver_attempts
        allowed = (
            _configured_resolver_allowed(state, target, identity, resolver_attempts)
            if target
            else True
        )
        effective_resolver = conflict_resolver if allowed else None
        resolver_suppressed = conflict_resolver is not None and not allowed
        recovered = _recover_before_attempt(
            config,
            workspace_scope,
            None,
            effective_resolver,
            rebase_stop_resolver if allowed else None,
        )
        if recovered is not None:
            if target:
                recovered = apply_completed_attempt_budget(
                    recovered,
                    prior=state,
                    target=target,
                    identity=identity,
                    resolver_suppressed=resolver_suppressed,
                    attempts=resolver_attempts,
                )
            if resolver_suppressed:
                from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
                from ralph.pipeline.auto_integrate_record import clear_record
                from ralph.pipeline.auto_integrate_resolve import _abort_merge_safely

                if merge_state(root) == MERGE_STATE_IN_PROGRESS:
                    _abort_merge_safely(root)
                    clear_record(root)
                    recovered.clear_recovery_record_retained()
            return preserve_unresolved_resolution_state(recovered, prior=state)
        return preserve_unresolved_resolution_state(
            _auto_integrate_after_commit_inner(
                config,
                workspace_scope,
                state,
                conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
                display=display,
                sleep=sleep,
                jitter=jitter,
            ),
            prior=state,
        )
    except TerminalStateViolationError:
        raise
    except Exception as exc:
        logger.warning("auto_integrate_after_commit: unexpected failure: {}", exc)
        skipped = _record_skip(reason=f"unexpected failure: {exc}", target=None)
        if (
            read_record_outcome(Path(workspace_scope.root)).disposition
            is RecordReadOutcomeDisposition.ABSENT
        ):
            return skipped
        return skipped.set_recovery_record_retained(
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
    finally:
        reset_executors(executors_token)


def auto_integrate_on_phase_transition(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _random.random,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> RebaseState | None:
    """Keep a clean worktree synchronized at phase boundaries without hiding failures.

    ``git_ops`` and ``agent_runner`` are the injected MUTATION seams.
    """
    executors_token = install_executors(git_ops, agent_runner)
    try:
        # Same rule as the commit seam: an unresolved integration must not
        # skip the seam that would resolve it. Run, then protect the evidence.
        return preserve_unresolved_resolution_state(
            _auto_integrate_on_phase_transition_inner(
                config,
                workspace_scope,
                state,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
                display=display,
                sleep=sleep,
                jitter=jitter,
            ),
            prior=state,
        )
    finally:
        reset_executors(executors_token)


def _auto_integrate_on_phase_transition_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _random.random,
) -> RebaseState | None:
    try:
        root = Path(workspace_scope.root)
        if not (root / ".git").exists():
            return None
        recovered = _recover_before_attempt(
            config,
            workspace_scope,
            None,
            conflict_resolver,
            rebase_stop_resolver,
        )
        if recovered is not None:
            return recovered
        if not config.general.auto_integrate_enabled:
            return None
        target = resolve_integration_target(config, root)
        _reattach_head_if_detached(root, config=config, auto_integrate_target=target)
        boundary_handled, boundary_outcome = _phase_boundary_outcome(
            config, root, target, state, rebase_stop_resolver=rebase_stop_resolver
        )
        if boundary_handled:
            return boundary_outcome
    except Exception as exc:
        # Record pre-check failures rather than hiding them as a no-op.
        logger.warning(
            "auto_integrate: phase-transition pre-check failed: {}\n"
            "  recording skip (AC-08); the next seam will retry from a "
            "fresh live local ref",
            exc,
        )
        try:
            recorded_target = resolve_integration_target(config, Path(workspace_scope.root)) or ""
        except Exception:
            recorded_target = ""
        return _record_skip(
            reason=f"phase-transition pre-check failed: {exc}",
            target=recorded_target,
        )
    return auto_integrate_after_commit(
        config,
        workspace_scope,
        state,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
        sleep=sleep,
        jitter=jitter,
    )


#: Maximum end-to-end integration attempts per commit. Attempt N+1 runs
#: only when attempt N completed a rebase/merge but the fast-forward did
#: not land (AC-08); the retry re-integrates onto the moved tip. The
#: exhausted budget is not silent: the record names it (see
#: :func:`_record_attempt_budget_spent`).
_MAX_INTEGRATION_ATTEMPTS = 3


def _auto_integrate_after_commit_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _random.random,
) -> RebaseState | None:
    """Resolve context, integrate, and retry bounded fast-forward races."""
    ctx = _auto_integrate_resolve_context(config, workspace_scope)
    # The refresh outcome every decision below is made against; carried
    # down so the conflict and short-circuit records say how fresh the
    # mainline pointer was too (AC-04).
    refresh: str | None = ctx[3] if ctx is not None else None
    early_skip, usable_ctx = _check_early_skips(
        ctx,
        missing_target=_configured_target(config),
    )
    if early_skip is not None:
        return carry_budget_through_skip(
            early_skip,
            prior=state,
            attempts=config.conflict_resolution.max_consecutive_resolver_attempts,
        )
    if usable_ctx is None:
        # R2/AC8: ladder rung 3 -- only the explicit disabled AC-01 path
        # reaches this bare None; enabled lookup failures are recorded above.
        return None
    root, _current_branch, target = usable_ctx

    remote_record: RebaseState | None = None
    identity = observe_conflict_identity(root, target)
    strategy_attempt = state.conflict_strategy_index == 1
    resolver_attempts = config.conflict_resolution.max_consecutive_resolver_attempts
    allowed = _configured_resolver_allowed(state, target, identity, resolver_attempts)
    effective_resolver = conflict_resolver if allowed else None
    resolver_suppressed = conflict_resolver is not None and not allowed
    if resolver_suppressed:
        strategy_index = min(state.conflict_strategy_index, len(CONFLICT_RESOLUTION_STRATEGIES) - 1)
        logger.warning(
            "auto_integrate: conflict resolution budget exhausted for '{}'; "
            "moving to resolution strategy '{}'",
            target,
            CONFLICT_RESOLUTION_STRATEGIES[strategy_index],
        )

    record: RebaseState | None = None
    prefer_merge = False
    attempts_exhausted = False
    try:
        for attempt in range(_MAX_INTEGRATION_ATTEMPTS):
            if attempt:
                wait_before_retry(attempt, sleep=sleep, jitter=jitter)
            recovered = _recover_before_attempt(
                config,
                workspace_scope,
                record,
                effective_resolver,
                rebase_stop_resolver if allowed else None,
            )
            if recovered is not None:
                record = recovered
                break
            remote_record, refresh = _freshen_attempt_target(
                config,
                root,
                target,
                attempt=attempt,
                initial_refresh=refresh,
                rebase_stop_resolver=rebase_stop_resolver if allowed else None,
                prior=state,
                force_refresh=strategy_attempt,
            )
            if remote_record is not None and not remote_record.freshness_safe:
                return remote_record
            identity = observe_conflict_identity(root, target)
            # Rebase-first on EVERY attempt, including a retry after the conflict
            # ladder escalated: the escalation changes the RESOLUTION strategy,
            # never the order of the integration.
            record, retry_ff = _integrate_once(
                config,
                root,
                target,
                effective_resolver,
                prefer_merge=prefer_merge,
                refresh=refresh,
                rebase_stop_resolver=(rebase_stop_resolver if allowed else None),
                display=display,
            )
            if not retry_ff:
                break
            prefer_merge = record is not None and record.last_outcome == RebaseOutcomeKind.MERGED
            if attempt + 1 < _MAX_INTEGRATION_ATTEMPTS:
                logger.info(
                    "auto_integrate: fast-forward did not land on attempt {}; "
                    "re-integrating onto the moved target",
                    attempt + 1,
                )
                continue
            # Last iteration: the range ends here and NOTHING
            # re-integrates. Promising a retry that never runs, and
            # recording only the last fast-forward skip reason, left the
            # operator with no way to tell "the target moved once" from
            # "the target kept moving until I ran out of attempts".
            attempts_exhausted = True
            logger.warning(
                "auto_integrate: fast-forward did not land after {} integration "
                "attempts; giving up until the next seam",
                _MAX_INTEGRATION_ATTEMPTS,
            )
    except TerminalStateViolationError:
        # R6/AC-06: a terminal-state violation is loud, not silent. The
        # durable record is RETAINED so the recovery preamble can reclaim
        # the leaked state using the recorded pre-attempt SHA. Re-raise so
        # the outer guard propagates the failure instead of recording a
        # skip the operator would never correlate with the leak.
        raise
    except Exception as exc:
        # Caught here, not only in the caller's broad guard: this is
        # the first frame knowing the target/identity to scope by.
        logger.warning("auto_integrate: integration attempt failed: {}", exc)
        charged = record_refresh(
            _record_skip(reason=f"unexpected failure: {exc}", target=target),
            refresh,
        )
        # Conditional expression, not an if-statement: this handler already
        # sits at the function's PLR0912 branch budget.
        charged = (
            charged.set_recovery_record_retained(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK)
            if read_record_outcome(root).disposition
            in (
                RecordReadOutcomeDisposition.PRESENT,
                RecordReadOutcomeDisposition.UNREADABLE,
                RecordReadOutcomeDisposition.BUSY,
            )
            else charged
        )
        return charge_failed_attempt(
            charged,
            prior=state,
            target=target,
            identity=identity,
            attempts=resolver_attempts,
        )
    if record is None:
        # The normal local seam can be a no-op while remote sync still has a
        # useful visible outcome (for example a failed fetch). Preserve it.
        return remote_record
    if remote_record is not None:
        record = record.model_copy(
            update={
                "last_remote_sync": remote_record.last_remote_sync,
                "last_remote": remote_record.last_remote,
                "last_refresh": remote_record.last_refresh or record.last_refresh,
                "last_reason": record.last_reason or remote_record.last_reason,
                "freshness_verdict": remote_record.freshness_verdict,
                "freshness_source": remote_record.freshness_source,
                "freshness_safe": remote_record.freshness_safe,
                "freshness_target_sha": remote_record.freshness_target_sha,
            }
        )
    if attempts_exhausted:
        record = _record_attempt_budget_spent(record)
    return apply_completed_attempt_budget(
        record,
        prior=state,
        target=target,
        identity=identity,
        resolver_suppressed=resolver_suppressed,
        attempts=resolver_attempts,
    )


def _configured_resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity,
    attempts: int,
) -> bool:
    """Apply the configured budget while tolerating legacy predicate doubles."""
    try:
        return resolver_allowed(state, target, identity, attempts=attempts)
    except TypeError as exc:
        if "unexpected keyword argument 'attempts'" not in str(exc):
            raise
        return resolver_allowed(state, target, identity)


def _freshen_attempt_target(
    config: UnifiedConfig,
    root: Path,
    target: str,
    *,
    attempt: int,
    initial_refresh: str | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    prior: RebaseState | None = None,
    force_refresh: bool = False,
) -> tuple[RebaseState | None, str | None]:
    """Return the current remote verdict or local-fleet observation."""
    if not remote_sync_enabled(config):
        if attempt or force_refresh:
            return None, _refresh_target(config, root, target)
        return None, initial_refresh
    record = pull_and_reconcile_target(
        config, root, target, rebase_stop_resolver=rebase_stop_resolver, prior=prior
    )
    if record is None:
        return None, None
    refresh = record.last_refresh
    if record.last_remote_sync == RemoteSyncStatus.NO_REMOTE:
        refresh = _refresh_target(config, root, target)
    return record, refresh


def _record_attempt_budget_spent(record: RebaseState) -> RebaseState:
    """Append the spent-attempt-budget fact to a record's skip reason.

    The underlying concurrency reason is kept as the headline -- it is
    what an operator needs in order to act -- and the exhaustion is
    appended, so the ``auto-integrate:`` line says both WHY the landing
    kept failing and that the bounded loop stopped trying.
    """
    base = record.last_reason or "fast-forward did not land"
    return record.model_copy(
        update={
            "last_reason": (f"{base}; exhausted {_MAX_INTEGRATION_ATTEMPTS} integration attempts")
        }
    )


def _reintegrate_after_remote_reconcile(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    refresh: str | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    display: ParallelDisplay | None,
) -> bool:
    """Rebase and land the feature after a target-to-remote reconciliation."""
    record, retry = _integrate_once_owned(
        config,
        root,
        target,
        conflict_resolver,
        refresh=refresh,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
        publish=False,
        owned_record=scheduled_landing(root, target),
    )
    return record is not None and record.fast_forwarded and not retry


def _integrate_once(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    publish: bool = True,
    require_in_sync: bool = False,
) -> tuple[RebaseState | None, bool]:
    with integration_transaction(root) as acquired:
        if not acquired:
            return _record_skip(
                reason="integration already running in this worktree", target=target
            ), False
        return _integrate_once_owned(
            config,
            root,
            target,
            conflict_resolver,
            prefer_merge=prefer_merge,
            refresh=refresh,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
            publish=publish,
            require_in_sync=require_in_sync,
            owned_record=scheduled_landing(root, target),
        )


def _integrate_once_owned(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    publish: bool = True,
    require_in_sync: bool = False,
    owned_record: IntegrationRecord | None = None,
) -> tuple[RebaseState | None, bool]:
    """Run one rebase-or-merge integration and report whether a landing race merits retry."""
    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    # Idempotent land: the target already holds every feature commit, so
    # nothing needs rebasing, merging or moving. Report the same success a
    # first landing reports and release every latch the record was holding.
    # A catch-up (``require_in_sync``) is the planning path: the branch must
    # also absorb the target tip, so "already inside target" is not enough.
    contained = contained_land(root, target, require_in_sync=require_in_sync)
    if contained is not None:
        return contained, False
    pre_feature_sha = get_head_sha(root)
    pre_target_sha = branch_sha(root, target)
    retained = read_record(root)
    if retained != owned_record or (
        read_record_outcome(root).disposition is RecordReadOutcomeDisposition.UNREADABLE
    ):
        return _record_skip(
            reason="unfinished integration retained for recovery", target=target
        ), False
    attempt_record = (
        owned_record.with_transition(
            START_INTEGRATING,
            phase="integrating",
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
            integrated_feature_sha=None,
        )
        if owned_record is not None
        else IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
        )
    )
    _write_record(root, attempt_record)
    # B11/E5: backup the original feature tip on a uniquely named
    # ``refs/rebase-backup/<id>`` ref BEFORE any mutation so a
    # concurrent ``git gc --prune`` cannot reclaim the in-flight commits.
    # The ref is retained through the attempt (the recovery preamble
    # reads it) and deleted after a successful land or a verified abort.
    backup_ref = _create_rebase_backup_ref(
        root,
        pre_feature_sha,
        attempt_record.owning_worktree or str(root),
    )
    owns_resolution = False
    try:
        rebase_result = _run_rebase_or_merge(
            root,
            target,
            conflict_resolver,
            prefer_merge=prefer_merge,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
            conflict_resolution_config=config.conflict_resolution,
            git_ops=current_git_ops(),
            agent_runner=current_agent_runner(),
        )
        if rebase_result.short_circuit is not None:
            # Resolved failures clear the record; an abort that leaves a rebase
            # in progress retains it for startup recovery.
            if not rebase_result.short_circuit.recovery_record_retained:
                _verify_and_cleanup_backup(root, backup_ref, pre_feature_sha, owns_resolution)
            return record_refresh(rebase_result.short_circuit, refresh), False

        feature_sha = _read_post_integration_head_sha(root, target)
        if feature_sha is None:
            _verify_and_cleanup_backup(root, backup_ref, pre_feature_sha, owns_resolution)
            return record_refresh(
                _record_skip(reason="post-integration HEAD read failed", target=target),
                refresh,
            ), False

        post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
        # Flip the durable record to 'integrated' BEFORE any ref move so
        # a crash between this point and the fast-forward will be
        # recoverable as a continue-fast-forward rather than a restore
        # (AC-11).
        _write_record(
            root,
            complete_commit(attempt_record, integrated_feature_sha=feature_sha).record,
        )

        refresh_outcome = refresh
        from ralph.pipeline.auto_integrate_remote_sync import reclaim_target_worktree_enabled

        reclaim_target_worktree = reclaim_target_worktree_enabled(config)
        reclamation: ReclaimResult | None = None

        def remember_reclamation(result: ReclaimResult) -> None:
            nonlocal reclamation
            reclamation = result

        ok, skip_reason = _fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=remember_reclamation,
        )

        # R6/AC-06: post-attempt terminal-state verification on EVERY
        # exit path. A completed rebase/merge leaves HEAD at feature_sha,
        # regardless of whether the target fast-forward wins its race.
        #
        # The record MUST stay on disk until this verification passes, so
        # the recovery preamble can reclaim the leaked state using the
        # recorded pre-attempt SHA. Clearing the record BEFORE
        # verification (the prior shape) discarded that metadata.
        post_attempt_verify(root, expected_head_sha=feature_sha, owns_resolution=False)
        if ok:
            _delete_rebase_backup_ref(root, backup_ref)
        if ok:
            _clear_record(root)

        record = _record_rebase_outcome(
            rebase_outcome=rebase_result.rebase_outcome,
            merge_attempted=rebase_result.merge_attempted,
            merge_outcome=rebase_result.merge_outcome,
            target=target,
        ).model_copy(
            update={
                "last_refresh": refresh_outcome,
                "reclaimed_worktree_path": (
                    reclamation.worktree_path if reclamation is not None else None
                ),
                "reclaim_snapshot_ref": reclamation.snapshot_ref
                if reclamation is not None
                else None,
                "reclaim_discarded_path_count": (
                    reclamation.discarded_path_count if reclamation is not None else 0
                ),
            }
        )
        if not ok:
            logger.critical(
                "CRITICAL: feature {} did NOT land on '{}': {}; committed work remains on the feature",
                feature_sha,
                target,
                skip_reason,
            )
            # Fast-forward skipped: reason is appended but we keep
            # the rebase/merged action as the headline so the log line
            # reflects what actually happened to the feature. Only an
            # explicit concurrent target move merits a bounded retry.
            record = with_skip_reason(
                record.model_copy(update={"last_reason": skip_reason, "fast_forwarded": False}),
                rebase_result.skip_reason,
            )
            return record, is_retryable_fast_forward_failure(skip_reason)
        # Successful fast-forward: the ``fast_forwarded`` boolean is
        # the headline signal, so any residual reason from the
        # rebase/merge phase (including benign rebase NoOp reasons) is
        # scrubbed here -- except a REGISTERED rebase-skip reason, the
        # only way the operator learns the rebase never ran.
        record = record.model_copy(update={"fast_forwarded": True})
        if publish:
            # Single publication entry point: the reconciler compares
            # local and remote SHAs, pushes when local is ahead, catches
            # up when remote is ahead, and reintegrates on divergence.
            # A non-fast-forward inside the reconciler is handled by
            # ``reconcile_after_rejected_push``. The caller's
            # ``fast_forwarded`` is carried through ``source_state`` so
            # the headline "did the local land" signal survives.
            record = reconcile_remote_publication(
                config,
                root,
                target,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=record,
            )
            if record.last_push_status == "non_fast_forward":
                # A non-fast-forward push is a remote race, not a failed local
                # landing. Re-integrate the feature after reconciling the target
                # before retrying publication, without recursively publishing.
                record = reconcile_after_rejected_push(
                    config,
                    root,
                    target,
                    record,
                    rebase_stop_resolver=rebase_stop_resolver,
                    reintegrate=lambda: _reintegrate_after_remote_reconcile(
                        config,
                        root,
                        target,
                        conflict_resolver,
                        refresh=refresh_outcome,
                        rebase_stop_resolver=rebase_stop_resolver,
                        display=display,
                    ),
                )
        # A skipped rebase is RECORDED: the registered reason text rides on
        # the state, so a prescribed skip is visible next to the fallback.
        record = with_skip_reason(record, rebase_result.skip_reason)
        return record, False
    except BaseException:
        raise


def _check_early_skips(
    ctx: tuple[Path, str | None, str | None, str | None] | None,
    *,
    missing_target: str,
) -> tuple[RebaseState | None, tuple[Path, str, str] | None]:
    """Return either a recorded skip or the narrowed integration context."""
    if ctx is None:
        # Disabled (AC-01) or env lookup failed: caller already
        # recorded the skip when applicable.
        return None, None
    root, current_branch, target, refresh = ctx
    if current_branch is None:
        # Detached HEAD: AC-02 requires this to be a RECORDED skip,
        # not a silent no-op. The crash record is never written in
        # this branch -- a detached HEAD is not an in-progress
        # integration.
        return record_refresh(_record_skip(reason="detached HEAD", target=target), refresh), None
    if target is None:
        reason = (
            f"local integration target branch does not exist: {missing_target}"
            if missing_target
            else "no integration target configured"
        )
        return _record_skip(reason=reason, target=missing_target or None), None
    skip = _auto_integrate_check_skip_conditions(root, current_branch, target)
    if skip is not None:
        # Every skip in that table is decided FROM the target pointer
        # the refresh above was meant to freshen, so the record carries
        # how fresh that pointer actually was.
        return record_refresh(skip, refresh), None
    # No skip: usable_ctx is the narrowed tuple with non-Optional
    # branch + target slots the orchestrator can unpack.
    return None, (root, current_branch, target)


def _auto_integrate_resolve_context(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
) -> tuple[Path, str | None, str | None, str | None] | None:
    """Resolve the (root, current_branch, target, refresh) context or short-circuit.

    Returns ``None`` ONLY when the integration step is a no-op
    (AC-01 disabled path -- byte-identical to the pre-feature run).
    When the environment lookup itself fails (not a git repo,
    transport error, etc.), the exception is allowed to propagate
    so :func:`auto_integrate_after_commit`'s outer try/except can
    record a skip ``RebaseState`` with the failure reason. A
    context-resolution failure must NEVER be silently swallowed to
    ``None`` -- that would make the run continue without any state
    record explaining WHY no integration ran.

    The current-branch slot is ``None`` when HEAD is detached
    (GitPython raises ``TypeError`` from ``repo.active_branch.name``
    in that case). The caller surfaces that as a recorded skip
    (``reason='detached HEAD'``) so the operator can see WHY no
    integration ran -- previously a detached HEAD silently fell into
    the broad ``except Exception`` branch and returned ``None``
    with no state recorded.
    """
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    enabled = bool(enabled_raw)
    if not enabled:
        return None
    root = Path(workspace_scope.root)
    # Typed-exception guard: detached HEAD surfaces as
    # ``current_branch is None`` (recorded skip) rather than being
    # absorbed into the broad ``except Exception`` above.
    current_branch: str | None = _current_branch_or_detached_marker(root)
    target: str | None = resolve_integration_target(config, root)
    refresh: str | None = None
    if target is not None and not remote_sync_enabled(config):
        # Local-only runs retain the historical observe-only refresh. The
        # opted-in path performs its one configured-remote fetch below, so it
        # never issues an extra origin probe before remote preflight.
        refresh = _refresh_target(config, root, target)

    return root, current_branch, target, refresh


def _auto_integrate_check_skip_conditions(
    root: Path,
    current_branch: str,
    target: str,
) -> RebaseState | None:
    """Apply the AC-02 / AC-03 / preconditions skip table; return skip or None.

    A failed HEAD read is recorded with the underlying
    :class:`GitOperationError` message rather than being allowed to
    reach the caller's broad handler, where it became an opaque
    ``unexpected failure`` that named neither the operation nor the
    repository.

    AC-07/R8 (reclaim-at-seam): a ``RebasePreconditionError`` that
    points at UNOWNED stale state (the ``rebase_in_progress`` /
    ``merge_in_progress`` family of precondition failures) on a
    clean tree is NOT a permanent disablement -- it is a recoverable
    block. The reclaim is delegated to
    :func:`_reclaim_and_retry_preconditions`, which re-runs the
    preconditions in the same seam when the reclaim succeeds and
    proceeds. A dirty tree with unmerged paths is preserved
    (operator-owned, AC-11 case 4).
    """
    if current_branch == target:
        return _record_skip(reason="on target branch", target=target)
    target_sha = branch_sha(root, target)
    try:
        head_sha = get_head_sha(root)
    except GitOperationError as exc:
        return _record_skip(reason=f"HEAD read failed: {exc}", target=target)
    if target_sha is not None and target_sha == head_sha:
        return _record_skip(reason="no commits beyond target", target=target)
    try:
        check_rebase_preconditions(root)
    except RebasePreconditionError as exc:
        return _handle_precondition_failure(root, target, exc)
    return None


__all__ = [
    "IntegrationRecord",
    "_create_rebase_backup_ref",
    "_delete_rebase_backup_ref",
    "auto_integrate_after_commit",
    "auto_integrate_on_phase_transition",
    "recover_incomplete_integration",
    "recovery_retained_record",
    "resolve_integration_target",
]

# AC-14 catalog evidence
# AC-14 rationale: B10
# ladder rung: 1
# AC-14 rationale: B11
# ladder rung: 1
# AC-14 rationale: E4
# ladder rung: 4
# AC-14 rationale: E5
# ladder rung: 1
