"""Push / push-outcome / reconcile-after-rejected-push path split from
:mod:`ralph.pipeline.auto_integrate_remote_sync`.

Owns :func:`push_single_remote`, :func:`push_target_after_landing`,
:func:`reconcile_after_rejected_push`, the per-attempt
:class:`_PushOutcome` record, the
:func:`_attempt_reconcile_and_push` cycle body, the typed-outcome
projection helpers, the remote-SHA observer used by the
ref-comparison reconciler, and the canonical
:func:`_record_remote_state` builder.

The pull-side / throttle / :class:`_DefaultRemoteClient` lives in
:mod:`ralph.pipeline.auto_integrate_remote_client`; the orchestrator
(:mod:`ralph.pipeline.auto_integrate_remote_sync`) keeps the
ref-comparison reconciler and the closed :class:`RemoteSyncStatus`
vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git import remote_push as _remote_push_module
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import (
    PublicationDecision,
    next_publication_action,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_NO_REMOTE,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.auto_integrate_remote_client import (
        _DefaultRemoteClient,
    )
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


def _default_remote_client() -> _DefaultRemoteClient:
    """Lazy accessor for the shared default remote client.

    Top-level import would close the cycle
    ``client -> push -> client`` (client imports the push module's
    :func:`_record_remote_state`; the push module imports the client's
    ``_DEFAULT_REMOTE_CLIENT``). Resolving the symbol inside the
    function body means the lookup runs at call time, when both modules
    are fully loaded.
    """
    from ralph.pipeline.auto_integrate_remote_client import _DEFAULT_REMOTE_CLIENT

    return _DEFAULT_REMOTE_CLIENT


__all__ = [
    "_remote_push_module",
    "push_single_remote",
    "push_target_after_landing",
    "reconcile_after_rejected_push",
]


#: Per-seam remote reconcile cap; later seams always retry.
_MAX_REMOTE_SYNC_ATTEMPTS = 3


# ----- Push hook --------------------------------------------------------


def push_single_remote(
    repo_root: Path,
    target: str,
    *,
    remote: str,
    record: RebaseState,
    timeout_seconds: float = _remote_push_module.PUSH_TIMEOUT_SECONDS,
    remote_client: RemoteClient | None = None,
) -> tuple[_remote_push_module.PushResult | None, PublicationDecision]:
    """Ask next_publication_action before invoking remote push.

    If the publication decision is not PUBLISH_NOW, push_branch_to_single_remote
    is not invoked.
    """
    decision = next_publication_action(
        record,
        record=None,
        remote_unavailable=False,
        configured_remote=remote,
    )
    if decision is not PublicationDecision.PUBLISH_NOW:
        return None, decision

    client = remote_client or _default_remote_client()
    result = client.push_branch(
        repo_root,
        target,
        remote=remote,
        timeout_seconds=timeout_seconds,
    )
    typed_result = _coerce_push_result(result, remote=remote, target=target)
    is_unavailable = typed_result.status in {
        _remote_push_module.PushStatus.UNREACHABLE,
        _remote_push_module.PushStatus.TIMEOUT,
    }
    post_decision = next_publication_action(
        record,
        record=None,
        remote_unavailable=is_unavailable,
        configured_remote=remote,
    )
    return typed_result, post_decision


def push_target_after_landing(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    record: RebaseState,
    *,
    remote: str | None = None,
    remote_client: RemoteClient | None = None,
) -> RebaseState:
    """Push the local target to the configured remote after a successful land.

    Returns the ``RebaseState`` annotated with ``last_remote_sync`` and
    ``last_push``. Never raises; never reverts a local landing.
    """
    from ralph.pipeline.auto_integrate_remote_sync import (
        remote_sync_enabled,
        remote_target_name,
    )

    if not remote_sync_enabled(config):
        decision = next_publication_action(
            record,
            record=None,
            remote_unavailable=False,
            configured_remote=None,
        )
        return record.set_publication_decision(decision)
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)

    import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

    push_fn = _remote_sync.push_single_remote
    push_record = (
        record.model_copy(update={"freshness_verdict": "verified"})
        if record.fast_forwarded
        else record
    )
    typed_result, decision = push_fn(
        repo_root,
        target,
        remote=chosen_remote,
        record=push_record,
        remote_client=remote_client,
    )
    if typed_result is None:
        return record.set_publication_decision(decision)
    post_decision = _post_push_publication_decision(typed_result)
    outcome = (
        RebaseOutcomeKind.REMOTE_PUSHED if typed_result.success else RebaseOutcomeKind.REMOTE_FAILED
    )
    return record.set_publication_decision(post_decision).model_copy(
        update={
            "last_push": typed_result.summary,
            "last_remote": typed_result.remote,
            "last_push_status": typed_result.status.value,
            "last_remote_sync": _remote_sync_status(typed_result.status),
            "last_outcome": outcome,
            "last_reason": (
                None if typed_result.success else typed_result.detail or typed_result.status.value
            ),
        },
    )


def _post_push_publication_decision(
    typed_result: _remote_push_module.PushResult,
) -> PublicationDecision:
    """Translate a typed push outcome into the post-push publication decision."""
    terminal_statuses = {
        _remote_push_module.PushStatus.PUSHED,
        _remote_push_module.PushStatus.CREATED,
    }
    if typed_result.status in terminal_statuses:
        return PublicationDecision.PUBLICATION_TERMINAL
    if typed_result.status in {
        _remote_push_module.PushStatus.UNREACHABLE,
        _remote_push_module.PushStatus.TIMEOUT,
    }:
        return PublicationDecision.PUBLISH_DEFERRED_REMOTE
    if typed_result.status == _remote_push_module.PushStatus.NON_FAST_FORWARD:
        return PublicationDecision.PUBLISH_DEFERRED_FRESHNESS
    return PublicationDecision.PUBLICATION_BLOCKED


def _coerce_push_result(
    result: _remote_push_module.PushResult | str, *, remote: str, target: str
) -> _remote_push_module.PushResult:
    """Keep legacy test doubles at the public helper boundary during migration."""
    if isinstance(result, _remote_push_module.PushResult):
        return result
    if result == f"pushed {target} to {remote}":
        return _remote_push_module.PushResult(_remote_push_module.PushStatus.PUSHED, remote, target)
    if result == f"remote '{remote}' not configured":
        return _remote_push_module.PushResult(
            _remote_push_module.PushStatus.MISSING_REMOTE, remote, target
        )
    return _remote_push_module.PushResult(
        _remote_push_module.PushStatus.NON_FAST_FORWARD, remote, target, result
    )


def _remote_sync_status(status: _remote_push_module.PushStatus) -> str:
    """Project typed push facts into the durable remote-sync outcome."""
    from ralph.pipeline.auto_integrate_remote_sync import (
        REMOTE_AUTH_FAILED,
        REMOTE_CREATED,
        REMOTE_NO_REMOTE,
        REMOTE_PUSH_REJECTED,
        REMOTE_PUSHED,
        REMOTE_REJECTED_BY_HOOK,
        REMOTE_REMOTE_UNREACHABLE,
        REMOTE_TIMEOUT,
    )

    outcomes = {
        _remote_push_module.PushStatus.PUSHED: REMOTE_PUSHED,
        _remote_push_module.PushStatus.CREATED: REMOTE_CREATED,
        _remote_push_module.PushStatus.MISSING_REMOTE: REMOTE_NO_REMOTE,
        _remote_push_module.PushStatus.NON_FAST_FORWARD: REMOTE_PUSH_REJECTED,
        _remote_push_module.PushStatus.TIMEOUT: REMOTE_TIMEOUT,
        _remote_push_module.PushStatus.AUTH_FAILED: REMOTE_AUTH_FAILED,
        _remote_push_module.PushStatus.HOOK_REJECTED: REMOTE_REJECTED_BY_HOOK,
        _remote_push_module.PushStatus.UNREACHABLE: REMOTE_REMOTE_UNREACHABLE,
    }
    return outcomes[status]


# ----- Rejected-push reconcile loop -------------------------------------


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
        REMOTE_PULL_FAILED,
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
        # Use the post-push decision so successful pushes become
        # terminal and failed pushes remain non-terminal. ``decision``
        # above is the pre-push eligibility check; it is replaced
        # here by the actual outcome of the attempt.
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
                    else REMOTE_PULL_FAILED
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
        from ralph.pipeline.auto_integrate_remote_sync import REMOTE_REMOTE_UNREACHABLE

        return _PushOutcome(
            success=False,
            summary=f"push of {target} to {remote} failed: {REMOTE_REMOTE_UNREACHABLE}",
            pushed=True,
            status=_remote_push_module.PushStatus.UNREACHABLE,
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
        FETCH_TIMEOUT_SECONDS,
        reclaim_target_worktree_enabled,
    )

    client = remote_client or _default_remote_client()
    refresh = client.refresh_target(
        repo_root,
        target,
        timeout_seconds=FETCH_TIMEOUT_SECONDS,
        remote=remote,
    )
    refresh_outcome = _probe_refresh_outcome(refresh, remote, target)
    if refresh_outcome is not None:
        return refresh_outcome

    if refresh == REFRESH_DIVERGED:
        from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote

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
    import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

    push_fn = _remote_sync.push_single_remote
    typed_result, _ = push_fn(
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


# ----- State helpers ---------------------------------------------------
# The canonical ``_record_remote_state`` builder lives in the client
# module (it is the largest user). The orchestrator re-exports it from
# the client so the public surface still resolves here.
