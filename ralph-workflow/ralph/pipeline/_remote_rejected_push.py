"""Bounded reconcile-then-push loop after non-fast-forward push rejection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git import remote_push as _remote_push_module
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import next_publication_action
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_NO_REMOTE,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.conflict_resolution import RebaseStopResolver

_MAX_REMOTE_SYNC_ATTEMPTS = 3
_FETCH_TIMEOUT_SECONDS = 10.0
_REMOTE_PULL_FAILED = "pull failed"
# Generic, configured-name-agnostic wording: the user-facing message
# built in :func:`_probe_refresh_outcome` always substitutes the actual
# remote name (passed in by the caller) so a configured `tacos` remote
# never reports as `origin unreachable`. The hard-coded `origin` literal
# that lived here before was an AC5 violation -- callers observed
# `push of release to tacos failed: origin unreachable` even when the
# configured remote was `tacos`.
_REF_REMOTE_UNREACHABLE_SUFFIX = "remote unreachable"


@dataclass
class _PushOutcome:
    """Per-attempt outcome of a push within the bounded reconcile cycle."""

    success: bool
    summary: str
    pushed: bool
    status: _remote_push_module.PushStatus | None = None
    created_remote_branch: bool = False


def reconcile_after_rejected_push(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    record: RebaseState,
    *,
    remote: str | None = None,
    max_attempts: int = _MAX_REMOTE_SYNC_ATTEMPTS,
    reintegrate: Callable[[], bool] | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    remote_client: RemoteClient | None = None,
) -> RebaseState:
    """Try a bounded reconcile-then-push cycle after a non-fast-forward.

    Per-seam cap is ``_MAX_REMOTE_SYNC_ATTEMPTS`` (3), matching the
    Part-A landing loop budget. Exhausting the cap is NOT a give-up:
    the pending push is carried forward on
    :attr:`RebaseState.last_remote_sync` as
    ``REMOTE_PUSH_REJECTED`` and retried on the next seam, so the
    fail-open contract holds for the whole run, not just one seam.
    """
    from ralph.pipeline.auto_integrate_remote_sync import (
        _post_push_publication_decision,
        _remote_sync_status,
        remote_sync_enabled,
        remote_target_name,
    )

    if not remote_sync_enabled(config):
        return record
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)

    final_record = record
    for _attempt in range(max_attempts):
        outcome = _attempt_reconcile_and_push(
            repo_root=repo_root,
            target=target,
            remote=chosen_remote,
            config=config,
            reintegrate=reintegrate,
            rebase_stop_resolver=rebase_stop_resolver,
            record=final_record,
            remote_client=remote_client,
        )
        decision = next_publication_action(
            final_record,
            record=None,
            remote_unavailable=outcome.status == _remote_push_module.PushStatus.UNREACHABLE,
            configured_remote=chosen_remote,
        )
        post_decision = (
            _post_push_publication_decision(
                _remote_push_module.PushResult(
                    outcome.status,
                    chosen_remote,
                    target,
                    detail=outcome.summary,
                )
            )
            if outcome.status is not None
            else decision
        )
        final_record = final_record.model_copy(
            update={
                "last_push": outcome.summary,
                "last_remote": chosen_remote,
                "last_push_status": outcome.status.value if outcome.status is not None else None,
                "last_remote_sync": (
                    _remote_sync_status(outcome.status)
                    if outcome.status is not None
                    else _REMOTE_PULL_FAILED
                ),
                "last_outcome": (
                    RebaseOutcomeKind.REMOTE_PUSHED
                    if outcome.success
                    else RebaseOutcomeKind.REMOTE_FAILED
                ),
            },
        ).set_publication_decision(post_decision)
        if outcome.success:
            return final_record
        if not outcome.pushed:
            break
    return final_record


def _probe_refresh_outcome(refresh: str, remote: str, target: str) -> _PushOutcome | None:
    if refresh == REFRESH_NO_REMOTE:
        return _PushOutcome(
            success=False,
            summary=f"remote '{remote}' not configured",
            pushed=False,
            status=_remote_push_module.PushStatus.MISSING_REMOTE,
        )
    if refresh == REFRESH_UNREACHABLE:
        # The remote name in the substituted string is the configured
        # ``remote`` argument, not a hard-coded literal. The previous
        # implementation appended ``origin unreachable`` regardless of
        # the configured name, which violated AC5 for any non-default
        # configuration (e.g. ``tacos``).
        return _PushOutcome(
            success=False,
            summary=(
                f"push of {target} to {remote} failed: {remote} {_REF_REMOTE_UNREACHABLE_SUFFIX}"
            ),
            pushed=True,
            status=_remote_push_module.PushStatus.UNREACHABLE,
        )
    return None


def _reconcile_in_progress_rebase(
    repo_root: Path,
    target: str,
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> _PushOutcome | None:
    if rebase_stop_resolver is not None:
        from ralph.pipeline.conflict_resolution import (
            resolution_session_from_config,
            resolve_rebase_in_progress,
        )

        session = resolution_session_from_config(config) if config is not None else None
        resolved = (
            resolve_rebase_in_progress(repo_root, target, rebase_stop_resolver, session=session)
            if session is not None
            else resolve_rebase_in_progress(repo_root, target, rebase_stop_resolver)
        )
        if not resolved:
            return _PushOutcome(
                success=False,
                summary=f"rebase in progress on {target} could not be resolved",
                pushed=False,
            )
        return None
    from ralph.pipeline.conflict_resolution.abort import (
        abort_rebase_discarding_progress,
    )

    abort_rebase_discarding_progress(repo_root)
    return _PushOutcome(
        success=False,
        summary=f"rebase in progress on {target} cleanly aborted without resolver",
        pushed=False,
        status=_remote_push_module.PushStatus.NON_FAST_FORWARD,
    )


def _align_target_with_remote(
    repo_root: Path,
    target: str,
    remote: str,
    refresh: str,
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    remote_client: RemoteClient | None = None,
) -> _PushOutcome | None:
    rebase_ongoing = False
    try:
        rebase_ongoing = rebase_in_progress(repo_root)
    except Exception:
        rebase_ongoing = False

    if rebase_ongoing:
        ongoing_outcome = _reconcile_in_progress_rebase(
            repo_root, target, config, rebase_stop_resolver
        )
        if ongoing_outcome is not None:
            return ongoing_outcome
    elif refresh == REFRESH_DIVERGED:
        from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote
        from ralph.pipeline.auto_integrate_remote_sync import reclaim_target_worktree_enabled

        reconciliation = reconcile_target_onto_remote(
            repo_root,
            target,
            remote,
            reclaim_target_worktree=reclaim_target_worktree_enabled(config),
            rebase_stop_resolver=rebase_stop_resolver,
            conflict_resolution_config=config,
        )
        if not reconciliation.reconciled:
            return _PushOutcome(
                success=False,
                summary=reconciliation.reason,
                pushed=False,
                status=(
                    _remote_push_module.PushStatus.NON_FAST_FORWARD
                    if reconciliation.cleanly_aborted
                    else None
                ),
            )
    elif refresh == REFRESH_ORIGIN_AHEAD:
        from ralph.pipeline.auto_integrate_catchup import attempt_catchup_fast_forward

        catchup_res = attempt_catchup_fast_forward(
            config,
            repo_root,
            target=target,
            remote=remote,
            remote_client=remote_client,
        )
        if catchup_res != "fast-forwarded":
            return _PushOutcome(
                success=False,
                summary="remote ahead but catch-up could not advance local target",
                pushed=False,
                status=_remote_push_module.PushStatus.NON_FAST_FORWARD,
            )
    return None


def _attempt_reconcile_and_push(
    *,
    repo_root: Path,
    target: str,
    remote: str,
    config: UnifiedConfig | None,
    reintegrate: Callable[[], bool] | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    record: RebaseState | None = None,
    remote_client: RemoteClient | None = None,
) -> _PushOutcome:
    """Run a single fetch -> rebase-target-onto-remote -> push cycle."""
    from ralph.pipeline.auto_integrate_remote_sync import (
        _DEFAULT_REMOTE_CLIENT,
        push_single_remote,
    )

    client = remote_client or _DEFAULT_REMOTE_CLIENT
    refresh = client.refresh_target(
        repo_root,
        target,
        timeout_seconds=_FETCH_TIMEOUT_SECONDS,
        remote=remote,
    )
    refresh_outcome = _probe_refresh_outcome(refresh, remote, target)
    if refresh_outcome is not None:
        return refresh_outcome

    align_outcome = _align_target_with_remote(
        repo_root=repo_root,
        target=target,
        remote=remote,
        refresh=refresh,
        config=config,
        rebase_stop_resolver=rebase_stop_resolver,
        remote_client=remote_client,
    )
    if align_outcome is not None:
        return align_outcome

    if reintegrate is not None and not reintegrate():
        return _PushOutcome(
            success=False,
            summary="feature re-integration after remote reconciliation did not land",
            pushed=False,
        )
    push_record = (
        record.model_copy(update={"freshness_verdict": "verified", "fast_forwarded": True})
        if record is not None
        else RebaseState(fast_forwarded=True, freshness_verdict="verified")
    )
    typed_result, _ = push_single_remote(
        repo_root,
        target,
        remote=remote,
        record=push_record,
        remote_client=remote_client,
    )
    if typed_result is None:
        return _PushOutcome(
            success=False,
            summary="publication suppressed by state machine",
            pushed=False,
        )
    return _PushOutcome(
        success=typed_result.success,
        summary=typed_result.summary,
        pushed=typed_result.status is not _remote_push_module.PushStatus.MISSING_REMOTE,
        status=typed_result.status,
        created_remote_branch=typed_result.status is _remote_push_module.PushStatus.CREATED,
    )
