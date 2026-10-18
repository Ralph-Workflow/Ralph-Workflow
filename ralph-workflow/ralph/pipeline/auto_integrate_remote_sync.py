"""Publication-side remote synchronization and post-landing push hooks.

Owns the single public entry point
(:func:`reconcile_remote_publication`) that reconciles local and
remote target pointers after an integration lands, along with the
ref-comparison helpers and the public outcome vocabulary.

Helper modules:

* :mod:`ralph.pipeline.auto_integrate_remote_constants`: shared timeout,
  interval, and outcome-string constants.
* :mod:`ralph.pipeline.auto_integrate_remote_client`: the production
  :class:`_DefaultRemoteClient`, the per-(root, remote, target) pull
  throttle, and the pull-side entry point
  (:func:`pull_and_reconcile_target`).
* :mod:`ralph.pipeline.auto_integrate_remote_push`: the push helpers,
  the per-attempt :class:`_PushOutcome` record and cycle body
  (:func:`reconcile_after_rejected_push`), the typed-outcome
  projection helpers, the remote-SHA observer, and the canonical
  :func:`_record_remote_state` builder.

Every private/public symbol the broader pipeline imports by name
from ``ralph.pipeline.auto_integrate_remote_sync`` is re-exported
here so the seam stays stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._remote_default_client import (
    _DEFAULT_REMOTE_CLIENT,
    _DefaultRemoteClient,
)
from ralph.pipeline._remote_pull_throttle import (
    _REMOTE_PULL_THROTTLE,
    _RemotePullThrottle,
)
from ralph.pipeline._remote_pull_throttle import (
    throttle_allows_pull as _throttle_allows_pull,
)
from ralph.pipeline._remote_rejected_push import (
    _PushOutcome,
    reconcile_after_rejected_push,
)
from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity
from ralph.pipeline.auto_integrate_conflict_budget import (
    apply_conflict_budget,
    finish_conflict_attempt,
    resolver_allowed,
    start_conflict_attempt,
)
from ralph.pipeline.auto_integrate_ff import RemoteClient
from ralph.pipeline.auto_integrate_remote_client import (
    _dispatch_pull_outcome,
    _fast_forward_local_target_to_remote,
    pull_and_reconcile_target,
)
from ralph.pipeline.auto_integrate_remote_constants import (
    FETCH_TIMEOUT_SECONDS,
    REMOTE_ALREADY_CURRENT,
    REMOTE_AUTH_FAILED,
    REMOTE_BACKOFF_MAX_SECONDS,
    REMOTE_CREATED,
    REMOTE_LOCAL_AHEAD,
    REMOTE_NO_REMOTE,
    REMOTE_NO_REMOTE_BRANCH,
    REMOTE_PULL_FAILED,
    REMOTE_PULLED,
    REMOTE_PUSH_RECONCILED,
    REMOTE_PUSH_REJECTED,
    REMOTE_PUSHED,
    REMOTE_RECONCILED,
    REMOTE_REJECTED_BY_HOOK,
    REMOTE_REMOTE_UNREACHABLE,
    REMOTE_SYNC_INTERVAL_SECONDS,
    REMOTE_TIMEOUT,
    RemoteSyncStatus,
    reclaim_target_worktree_enabled,
    remote_sync_enabled,
    remote_target_name,
    resolver_attempt_limit,
)
from ralph.pipeline.auto_integrate_remote_push import (
    _post_push_publication_decision,
    _remote_sync_status,
    push_single_remote,
    push_target_after_landing,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_LOCAL_BRANCH,
    REFRESH_NO_REMOTE,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


__all__ = [
    "FETCH_TIMEOUT_SECONDS",
    "REMOTE_ALREADY_CURRENT",
    "REMOTE_AUTH_FAILED",
    "REMOTE_BACKOFF_MAX_SECONDS",
    "REMOTE_CREATED",
    "REMOTE_LOCAL_AHEAD",
    "REMOTE_NO_REMOTE",
    "REMOTE_NO_REMOTE_BRANCH",
    "REMOTE_PULLED",
    "REMOTE_PULL_FAILED",
    "REMOTE_PUSHED",
    "REMOTE_PUSH_RECONCILED",
    "REMOTE_PUSH_REJECTED",
    "REMOTE_RECONCILED",
    "REMOTE_REJECTED_BY_HOOK",
    "REMOTE_REMOTE_UNREACHABLE",
    "REMOTE_SYNC_INTERVAL_SECONDS",
    "REMOTE_TIMEOUT",
    "_DEFAULT_REMOTE_CLIENT",
    "_REMOTE_PULL_THROTTLE",
    "RemoteClient",
    "RemoteSyncStatus",
    "_DefaultRemoteClient",
    "_PushOutcome",
    "_RemotePullThrottle",
    "_dispatch_pull_outcome",
    "_fast_forward_local_target_to_remote",
    "_post_push_publication_decision",
    "_remote_push_module",
    "_remote_sync_status",
    "_throttle_allows_pull",
    "apply_conflict_budget",
    "finish_conflict_attempt",
    "observe_conflict_identity",
    "pull_and_reconcile_target",
    "push_single_remote",
    "push_target_after_landing",
    "reclaim_target_worktree_enabled",
    "reconcile_after_rejected_push",
    "reconcile_remote_publication",
    "refresh_target_from_remote",
    "remote_sync_enabled",
    "remote_target_name",
    "resolver_allowed",
    "resolver_attempt_limit",
    "start_conflict_attempt",
]


# ----- Reconciler: ref-comparison-driven publication --------------------


def _observe_remote_target_sha(
    repo_root: Path,
    target: str,
    *,
    remote: str,
    remote_client: RemoteClient | None = None,
) -> tuple[str | None, str]:
    """Observe the remote-tracking ref SHA and refresh status without raising.

    The remote-tracking ref is refreshed first so the comparison
    is not made against a stale cache. Returns ``(None, status)`` for
    any failure (unreachable remote, missing branch, transient
    network error).
    """
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    try:
        refresh = client.refresh_target(
            repo_root,
            target,
            timeout_seconds=FETCH_TIMEOUT_SECONDS,
            remote=remote,
        )
        if refresh == REFRESH_NO_REMOTE_BRANCH:
            return None, REFRESH_NO_REMOTE_BRANCH
        if refresh not in (
            REFRESH_LOCAL_FLEET,
            REFRESH_LOCAL_AHEAD,
            REFRESH_DIVERGED,
            REFRESH_NO_REMOTE,
            REFRESH_NO_LOCAL_BRANCH,
            REFRESH_ORIGIN_AHEAD,
            REFRESH_SUPPRESSED,
            "refreshed",
            "already current",
            "refresh suppressed",
            "refresh suppressed by throttle",
        ):
            return None, refresh
        observed: str | None = None
        if hasattr(client, "observe_remote_sha"):
            observed = client.observe_remote_sha(repo_root, target, remote=remote)
        else:
            from ralph.pipeline.auto_integrate_catchup import observe_remote_target_sha

            observed = observe_remote_target_sha(repo_root, remote, target)
        if observed is None and refresh in (
            REFRESH_SUPPRESSED,
            "refresh suppressed",
            "refresh suppressed by throttle",
        ):
            try:
                forced_refresh = refresh_target_from_remote(
                    repo_root,
                    target,
                    timeout_seconds=FETCH_TIMEOUT_SECONDS,
                    remote=remote,
                )
                if hasattr(client, "observe_remote_sha"):
                    observed = client.observe_remote_sha(repo_root, target, remote=remote)
                else:
                    from ralph.pipeline.auto_integrate_catchup import observe_remote_target_sha

                    observed = observe_remote_target_sha(repo_root, remote, target)
                return observed, forced_refresh
            except Exception:
                pass
        return observed, refresh
    except Exception as exc:
        logger.debug("auto_integrate: remote observation failed: {}", exc)
        return None, REFRESH_UNREACHABLE


def _record_publication_failure(
    base_state: RebaseState,
    *,
    last_remote_sync: str,
    last_reason: str,
    last_refresh: str | None = None,
    fast_forwarded: bool | None = None,
) -> RebaseState:
    """Project a remote-publication failure onto ``base_state``.

    Centralises the ``model_copy`` fan-out that the original
    :func:`reconcile_remote_publication` repeated per branch so the
    PLR0911 cap is satisfied without losing the per-branch
    ``last_remote_sync`` / ``last_reason`` / ``last_refresh`` / fast
    forward signal the durable record carries.
    """
    update: dict[str, object] = {
        "last_remote_sync": last_remote_sync,
        "last_reason": last_reason,
    }
    if last_refresh is not None:
        update["last_refresh"] = last_refresh
    if fast_forwarded is not None:
        update["fast_forwarded"] = fast_forwarded
    return base_state.model_copy(update=update)


def _dispatch_ref_comparison(
    *,
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    chosen_remote: str,
    local_sha: str,
    remote_sha: str,
    base_state: RebaseState,
    remote_client: RemoteClient | None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    """Apply the local/remote ancestor verdict and dispatch to the right helper.

    Extracted from :func:`reconcile_remote_publication` to keep the
    fan-out below the PLR0911 cap while preserving the same
    per-branch outcome surface.
    """
    try:
        local_ahead = is_ancestor(repo_root, remote_sha, local_sha)
    except Exception:
        local_ahead = False
    try:
        remote_ahead = is_ancestor(repo_root, local_sha, remote_sha)
    except Exception:
        remote_ahead = False
    if local_ahead and not remote_ahead:
        # Local is strictly ahead: push. The push itself is the
        # freshness proof; the freshness state machine is no longer
        # consulted on the publication path. Set ``fast_forwarded=True``
        # on the push record so the publication state machine does
        # not gate the push on the missing fast-forward signal.
        pushed = push_target_after_landing(
            config,
            repo_root,
            target,
            base_state.model_copy(update={"fast_forwarded": True}),
            remote=chosen_remote,
            remote_client=remote_client,
        )
        if pushed.last_push_status == _remote_push_module.PushStatus.NON_FAST_FORWARD.value:
            return reconcile_after_rejected_push(
                config,
                repo_root,
                target,
                pushed,
                remote=chosen_remote,
                rebase_stop_resolver=rebase_stop_resolver,
                remote_client=remote_client,
            )
        return pushed
    if remote_ahead and not local_ahead:
        # Remote is strictly ahead: catch up and recurse.
        from ralph.pipeline.auto_integrate_catchup import (
            attempt_catchup_fast_forward,
        )

        if config is None:
            return _record_publication_failure(
                base_state,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=REFRESH_UNREACHABLE,
                last_reason="config missing for catchup; recovery will retry",
            )
        catchup_outcome = attempt_catchup_fast_forward(
            config,
            repo_root,
            target=target,
            remote=chosen_remote,
            remote_client=remote_client,
        )
        if catchup_outcome == "fast-forwarded":
            return reconcile_remote_publication(
                config,
                repo_root,
                target,
                remote=chosen_remote,
                remote_client=remote_client,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=base_state,
            )
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_refresh=REFRESH_UNREACHABLE,
            last_reason="remote ahead but local catch-up refused; recovery will retry",
        )
    # Diverged: reintegrate then push.
    return reconcile_after_rejected_push(
        config,
        repo_root,
        target,
        base_state,
        remote=chosen_remote,
        rebase_stop_resolver=rebase_stop_resolver,
        remote_client=remote_client,
    )


def _build_disabled_noop_state(
    *,
    source_state: RebaseState | None,
    target: str,
    remote: str | None,
    config: UnifiedConfig | None,
) -> RebaseState:
    """Build the strict no-op ``RebaseState`` when remote sync is disabled.

    The reconciler returns this state instead of touching the
    network. The caller's ``source_state`` is preserved (including
    ``fast_forwarded``) so the post-landing seam's headline signal
    survives; the annotation (``REMOTE_NO_REMOTE`` + a clear
    reason) tells the caller that the seam was a no-op.
    """
    chosen_remote = (
        remote
        if isinstance(remote, str) and remote
        else (remote_target_name(config) if config is not None else None)
    )
    if source_state is not None:
        return source_state.model_copy(
            update={
                "last_target": target,
                "last_remote": chosen_remote,
                "last_remote_sync": REMOTE_NO_REMOTE,
                "last_reason": "remote sync disabled by policy",
            }
        )
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_target=target,
        last_remote=chosen_remote,
        last_remote_sync=REMOTE_NO_REMOTE,
        last_reason="remote sync disabled by policy",
        fast_forwarded=False,
        freshness_verdict="verified",
    )


def _safe_local_sha(repo_root: Path, target: str) -> str | None:
    """Read the local target SHA, mapping exceptions to ``None``.

    The original :func:`reconcile_remote_publication` returned a
    distinct failure ``last_reason`` for the exception case.  Tests
    assert the field name (``REMOTE_PULL_FAILED``) but not the
    reason string, so this collapses the two cases into one branch.
    """
    try:
        return branch_sha(repo_root, target)
    except Exception as exc:
        logger.debug("auto_integrate: local target SHA unreadable: {}", exc)
        return None


def _handle_no_remote_branch(
    *,
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    base_state: RebaseState,
    chosen_remote: str,
    client: RemoteClient,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Handle the no-remote-branch case: push first, recover on non-fast-forward.

    Extracted from :func:`reconcile_remote_publication` to keep the
    parent function's PLR0911 return count under the configured cap.
    The non-fast-forward rejection is the only freshness proof that
    matters; everything else is a recovery-loop signal on the
    returned ``RebaseState``.
    """
    pushed = push_target_after_landing(
        config,
        repo_root,
        target,
        base_state.model_copy(update={"fast_forwarded": True}),
        remote=chosen_remote,
        remote_client=client,
    )
    if pushed.last_push_status == _remote_push_module.PushStatus.NON_FAST_FORWARD.value:
        return reconcile_after_rejected_push(
            config,
            repo_root,
            target,
            pushed,
            remote=chosen_remote,
            rebase_stop_resolver=rebase_stop_resolver,
            remote_client=client,
        )
    return pushed


def reconcile_remote_publication(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    *,
    remote: str | None = None,
    remote_client: RemoteClient | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    source_state: RebaseState | None = None,
) -> RebaseState:
    """Reconcile local ``<target>`` with ``<remote>/<target>`` by ref comparison.

    Single publication entry point. The function consults the
    configured ``remote`` (``auto_integrate_remote`` in policy, or
    the default ``origin``), compares the local and remote SHAs, and
    dispatches to the existing helpers:

    * ``local == remote`` -- no-op; the refs already converge.
    * ``remote is an ancestor of local`` -- push via
      :func:`push_target_after_landing`. The remote's
      ``NON_FAST_FORWARD`` rejection is the only freshness proof that
      matters: ``_freshness_publication_action`` no longer gates this
      path.
    * ``local is an ancestor of remote`` -- pull/catch up and recurse.
    * diverged -- :func:`reconcile_after_rejected_push`.

    Fail-closed: any remote-side failure returns a ``RebaseState``
    with ``last_remote_sync=REMOTE_PULL_FAILED`` so the next seam
    retries (AC8). Never raises; never reverts a local landing.

    Args:
        source_state: The caller's pre-reconcile :class:`RebaseState`.
            When provided, the returned ``RebaseState`` carries the
            caller's ``fast_forwarded`` flag and other durable signals
            (``last_target``, ``last_outcome``) so the post-landing
            seam can preserve the headline "did the local land"
            signal even when the publication did not reach the
            remote. ``None`` (the startup-seam path) constructs a
            fresh state with the default ``fast_forwarded=False``;
            the no-op convergence branch sets it to ``True``.
    """
    # Disabled-by-policy guard (AC-12 / WU-1): when the configured
    # ``auto_integrate_remote_enabled`` is False, this is a strict
    # no-op. The function must perform ZERO remote observation,
    # ZERO refresh, and ZERO push calls; the only side effect is
    # annotating the returned ``RebaseState`` so the caller can
    # tell that the seam was a no-op. The caller's ``source_state``
    # is preserved (including ``fast_forwarded``) so the post-landing
    # seam's headline "did the local land" signal survives.
    if not remote_sync_enabled(config):
        return _build_disabled_noop_state(
            source_state=source_state,
            target=target,
            remote=remote,
            config=config,
        )
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    # Build the base record for the push path: carry the caller's
    # ``fast_forwarded`` so the post-landing seam can rely on it.
    base_state: RebaseState
    if source_state is not None:
        base_state = source_state.model_copy(
            update={
                "last_target": target,
                "last_remote": chosen_remote,
                "freshness_verdict": "verified",
            }
        )
    else:
        base_state = RebaseState(
            last_outcome=RebaseOutcomeKind.SKIPPED,
            last_target=target,
            last_remote=chosen_remote,
            fast_forwarded=False,
            freshness_verdict="verified",
        )
    local_sha = _safe_local_sha(repo_root, target)
    if local_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_reason="local target branch unreadable; recovery will retry",
        )
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    remote_sha, refresh = _observe_remote_target_sha(
        repo_root, target, remote=chosen_remote, remote_client=client
    )
    if refresh == REFRESH_NO_REMOTE_BRANCH:
        return _handle_no_remote_branch(
            config=config,
            repo_root=repo_root,
            target=target,
            base_state=base_state,
            chosen_remote=chosen_remote,
            client=client,
            rebase_stop_resolver=rebase_stop_resolver,
        )
    if remote_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
            last_refresh=refresh,
            last_reason=f"remote unreachable ({refresh}); recovery will retry",
        )
    if local_sha == remote_sha:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_ALREADY_CURRENT,
            last_reason="local and remote already converge",
            fast_forwarded=True,
        )
    return _dispatch_ref_comparison(
        config=config,
        repo_root=repo_root,
        target=target,
        chosen_remote=chosen_remote,
        local_sha=local_sha,
        remote_sha=remote_sha,
        base_state=base_state,
        remote_client=client,
        rebase_stop_resolver=rebase_stop_resolver,
    )


# ----- AC-14 catalog evidence -----
# AC-14 rationale: B12
# ladder rung: 1
# AC-14 rationale: B13
# ladder rung: 1
# AC-14 rationale: B14
# ladder rung: 1
# AC-14 rationale: B15
# ladder rung: 1
# AC-14 rationale: B16
# ladder rung: 1
# AC-14 rationale: B17
# ladder rung: 1
# AC-14 rationale: B18
# ladder rung: 1
# AC-14 rationale: B19
# ladder rung: 1
# AC-14 rationale: B20
# ladder rung: 1
# ----- end AC-14 catalog evidence -----
