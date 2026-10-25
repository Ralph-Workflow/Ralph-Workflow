"""Resolve the rebase in place before merge on unresolved conflict.

The single-attempt integration machinery (:func:`_integrate_once` and
:func:`_integrate_once_owned`, plus the post-reconciliation re-landing
helper) lives in the private companion module
:mod:`ralph.pipeline._auto_integrate_once` so this module stays under
the repo-structure file-size cap; the orchestration (retry loop,
recovery preamble, resolver suppression, skip table) stays here and
re-exports every moved name unchanged.
"""

from __future__ import annotations

import random
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

#: Maximum end-to-end integration attempts per commit. Attempt N+1 runs
#: only when attempt N completed a rebase/merge but the fast-forward did
#: not land (AC-08); the retry re-integrates onto the moved tip. The
#: exhausted budget is not silent: the record names it (see
#: :func:`_record_attempt_budget_spent`).
from ralph.pipeline._auto_integrate_attempt_loop import (
    MAX_INTEGRATION_ATTEMPTS as _MAX_INTEGRATION_ATTEMPTS,
)
from ralph.pipeline._auto_integrate_attempt_loop import (
    AttemptLoopSeams,
    run_attempt_loop,
)
from ralph.pipeline._auto_integrate_attempt_loop import (
    record_attempt_budget_spent as _record_attempt_budget_spent,
)
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._auto_integrate_once import (
    _integrate_once,
    _integrate_once_owned as _integrate_once_owned,
    _reintegrate_after_remote_reconcile as _reintegrate_after_remote_reconcile,
)
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_continuation import (
    recover_before_attempt,
)
from ralph.pipeline._integration_continuation import (
    recover_before_attempt as _recover_before_attempt,
)
from ralph.pipeline.auto_integrate_backoff import wait_before_retry
from ralph.pipeline.auto_integrate_boundary import (
    phase_boundary_outcome as _phase_boundary_outcome,
)
from ralph.pipeline.auto_integrate_budget_seam import (
    apply_completed_attempt_budget,
    carry_budget_through_skip,
    observe_conflict_identity,
)
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    resolve_failure_ledger,
    resolver_allowed,
)
from ralph.pipeline.auto_integrate_context import (
    current_branch_or_detached_marker,
    record_refresh,
)
from ralph.pipeline.auto_integrate_context import (
    current_branch_or_detached_marker as _current_branch_or_detached_marker,
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
    read_post_integration_head_sha,
)
from ralph.pipeline.auto_integrate_rebase_merge import (
    run_rebase_or_merge as _run_rebase_or_merge,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record_outcome,
    write_record,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as read_record,
)
from ralph.pipeline.auto_integrate_record import (
    write_record as _write_record,
)
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify as post_attempt_verify,
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_refresh import (
    refresh_target,
)
from ralph.pipeline.auto_integrate_refresh import (
    refresh_target as _refresh_target,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    RemoteSyncStatus,
    pull_and_reconcile_target,
    remote_sync_enabled,
)
from ralph.pipeline.auto_integrate_resolution_state import (
    CONFLICT_RESOLUTION_STRATEGIES,
    containment_release,
    preserve_unresolved_resolution_state,
)
from ralph.pipeline.auto_integrate_resolution_state import (
    contained_land as contained_land,
)
from ralph.pipeline.commit_executor import (
    reattach_head_if_detached as _reattach_head_if_detached,
)
from ralph.pipeline.integration_actions import create_backup_ref as _create_rebase_backup_ref
from ralph.pipeline.integration_actions import (
    current_clock,
    install_executors,
    reset_executors,
)
from ralph.pipeline.integration_actions import delete_backup_ref as _delete_rebase_backup_ref
from ralph.pipeline.integration_actions import (
    fast_forward_target as _fast_forward_target,
)
from ralph.pipeline.rebase_state import StartupRetentionReason

_random = (
    random.Random()
)  # module-local RNG: thread-safe jitter without mutating global random state


_DEFAULT_RECOVER_BEFORE_ATTEMPT = recover_before_attempt
_DEFAULT_WRITE_RECORD = write_record
_DEFAULT_REFRESH_TARGET = refresh_target
_DEFAULT_READ_POST_HEAD_SHA = read_post_integration_head_sha
_DEFAULT_CURRENT_BRANCH_MARKER = current_branch_or_detached_marker


def _recover_before_attempt_probe(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    record: RebaseState | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    fn = (
        recover_before_attempt
        if recover_before_attempt is not _DEFAULT_RECOVER_BEFORE_ATTEMPT
        else (
            _recover_before_attempt
            if _recover_before_attempt is not _DEFAULT_RECOVER_BEFORE_ATTEMPT
            else _DEFAULT_RECOVER_BEFORE_ATTEMPT
        )
    )
    return fn(
        config,
        workspace_scope,
        record,
        conflict_resolver,
        rebase_stop_resolver,
    )


def _write_record_probe(root: Path, attempt_record: IntegrationRecord) -> None:
    fn = (
        write_record
        if write_record is not _DEFAULT_WRITE_RECORD
        else (
            _write_record if _write_record is not _DEFAULT_WRITE_RECORD else _DEFAULT_WRITE_RECORD
        )
    )
    fn(root, attempt_record)


def _refresh_target_probe(config: UnifiedConfig, root: Path, target: str) -> str | None:
    fn = (
        refresh_target
        if refresh_target is not _DEFAULT_REFRESH_TARGET
        else (
            _refresh_target
            if _refresh_target is not _DEFAULT_REFRESH_TARGET
            else _DEFAULT_REFRESH_TARGET
        )
    )
    return fn(config, root, target)


def _read_post_integration_head_sha_probe(root: Path, target: str) -> str | None:
    fn = (
        read_post_integration_head_sha
        if read_post_integration_head_sha is not _DEFAULT_READ_POST_HEAD_SHA
        else (
            _read_post_integration_head_sha
            if _read_post_integration_head_sha is not _DEFAULT_READ_POST_HEAD_SHA
            else _DEFAULT_READ_POST_HEAD_SHA
        )
    )
    return fn(root, target)


def _current_branch_probe(root: Path) -> str | None:
    fn = (
        current_branch_or_detached_marker
        if current_branch_or_detached_marker is not _DEFAULT_CURRENT_BRANCH_MARKER
        else (
            _current_branch_or_detached_marker
            if _current_branch_or_detached_marker is not _DEFAULT_CURRENT_BRANCH_MARKER
            else _DEFAULT_CURRENT_BRANCH_MARKER
        )
    )
    return fn(root)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import AgentRunner, Clock, GitOps
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.workspace.scope import WorkspaceScope


def auto_integrate_after_commit(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> RebaseState | None:
    """Integrate after a commit, recording skips and terminal-state violations.

    The injected Git and agent seams own every mutation in this attempt.
    """
    executors_token = install_executors(git_ops, agent_runner)
    try:
        # An unresolved integration must not skip this recovery seam; the
        # evidence is protected on the way out by the state authority.
        from ralph.pipeline.integration_resolution import retained_integration_reason

        root = Path(workspace_scope.root)
        target = resolve_integration_target(config, root) if (root / ".git").exists() else None
        # Git is ground truth: release a contained integration before blocking.
        state = containment_release(root, target, state)
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
                )

        identity = observe_conflict_identity(root, target or "") if target else ConflictIdentity()
        effective_ledger = resolve_failure_ledger(ledger)
        allowed = (
            _configured_resolver_allowed(
                state,
                target,
                identity,
                ledger=effective_ledger,
                candidates=candidates,
            )
            if target
            else True
        )
        effective_resolver = conflict_resolver if allowed else None
        resolver_suppressed = conflict_resolver is not None and not allowed
        recovered = _recover_before_attempt_probe(
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
                ledger=effective_ledger,
                candidates=candidates,
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
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> RebaseState | None:
    """Keep a clean worktree synchronized at phase boundaries without hiding failures.

    The injected Git and agent seams own every mutation in this attempt.
    """
    executors_token = install_executors(git_ops, agent_runner)
    try:
        # An unresolved integration must not skip the seam that resolves it.
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
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
) -> RebaseState | None:
    try:
        root = Path(workspace_scope.root)
        if not (root / ".git").exists():
            return None
        recovered = _recover_before_attempt_probe(
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


def _attempt_loop_seams() -> AttemptLoopSeams:
    """Hand the extracted attempt loop this module's patchable seams.

    The snapshot is taken at CALL time, so a seam patched on this module
    stays in effect inside the extracted loop: the loop uses the seam the
    engine patched, not the one the loop module imported.
    """
    return AttemptLoopSeams(
        resolve_context=_auto_integrate_resolve_context,
        early_skips=_check_early_skips,
        observe_conflict_identity=observe_conflict_identity,
        configured_resolver_allowed=_configured_resolver_allowed,
        recover_before_attempt=_recover_before_attempt_probe,
        freshen_target=_freshen_attempt_target,
        integrate_once=_integrate_once,
        wait_before_retry=wait_before_retry,
        read_record_outcome=read_record_outcome,
        jitter_value=_random.random,
    )


def _auto_integrate_after_commit_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> RebaseState | None:
    """Resolve context, integrate, and retry bounded fast-forward races."""
    return run_attempt_loop(
        config,
        workspace_scope,
        state,
        conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
        sleep=sleep,
        jitter=jitter,
        ledger=ledger,
        candidates=candidates,
        seams=_attempt_loop_seams(),
    )


def _configured_resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity,
    *,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> bool:
    """Ask the failure ledger whether this attempt may invoke the resolver.

    Resolver eligibility is owned by ``AgentFailureLedger`` alone. There
    is no second opinion here: the stored conflict count never selects,
    suppresses or demotes an agent.
    """
    return resolver_allowed(
        state,
        target,
        identity,
        ledger=resolve_failure_ledger(ledger),
        candidates=candidates,
    )


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
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> tuple[RebaseState | None, str | None]:
    """Return the current remote verdict or local-fleet observation."""
    if not remote_sync_enabled(config):
        if attempt or force_refresh:
            return None, _refresh_target_probe(config, root, target)
        return None, initial_refresh
    record = pull_and_reconcile_target(
        config,
        root,
        target,
        rebase_stop_resolver=rebase_stop_resolver,
        prior=prior,
        ledger=ledger,
        candidates=candidates,
    )
    if record is None:
        return None, None
    refresh = record.last_refresh
    if record.last_remote_sync == RemoteSyncStatus.NO_REMOTE:
        refresh = _refresh_target_probe(config, root, target)
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
    current_branch: str | None = _current_branch_probe(root)
    target: str | None = resolve_integration_target(config, root)
    refresh: str | None = None
    if target is not None and not remote_sync_enabled(config):
        # Local-only runs retain the historical observe-only refresh. The
        # opted-in path performs its one configured-remote fetch below, so it
        # never issues an extra origin probe before remote preflight.
        refresh = _refresh_target_probe(config, root, target)

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
    "_MAX_INTEGRATION_ATTEMPTS",
    "IntegrationRecord",
    "_create_rebase_backup_ref",
    "_delete_rebase_backup_ref",
    "_record_attempt_budget_spent",
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
