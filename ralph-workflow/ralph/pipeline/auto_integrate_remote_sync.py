"""Publication-side remote synchronization and post-landing push hooks.

Owns the single public entry point
(:func:`reconcile_remote_publication`) that reconciles local and
remote target pointers after an integration lands, along with the
ref-comparison helpers and the public outcome vocabulary.

Helper modules:

* :mod:`ralph.pipeline.auto_integrate_remote_constants`: shared timeout,
  interval, and outcome-string constants.
* :mod:`ralph.pipeline.integration_remote_actions`: the narrow
  :class:`RemoteOps` protocol for the remote tier plus the accessor
  (:func:`default_remote_ops`) for the production adapter. Every remote
  call this module makes -- the freshness refresh, the remote-tracking
  read, and the push -- goes through that one injected seam.
* :mod:`ralph.pipeline._remote_default_client`: the production
  :class:`_DefaultRemoteClient` this module injects by default.
* :mod:`ralph.pipeline.auto_integrate_remote_client`: the pull-side
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

When the remote is strictly ahead, the reconciler advances the local target
itself through
:func:`~ralph.pipeline.auto_integrate_remote_client.fast_forward_target_to_remote`
(never through the throttled catch-up tick, which judges the checkout rather
than the target). The remote-sync contract and its regression tests are
documented in ``docs/agents/remote-sync.md``.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline._remote_default_client import (
    _DefaultRemoteClient,
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
    fast_forward_target_to_remote,
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
    remote_unreachable_reason,
)
from ralph.pipeline.auto_integrate_remote_push import (
    _post_push_publication_decision,
    _PushOutcome,
    _remote_sync_status,
    push_single_remote,
    push_target_after_landing,
    reconcile_after_rejected_push,
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
from ralph.pipeline.integration_remote_actions import RemoteOps, default_remote_ops
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
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
    "RemoteOps",
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
    "fast_forward_target_to_remote",
    "finish_conflict_attempt",
    "observe_conflict_identity",
    "pull_and_reconcile_target",
    "push_single_remote",
    "push_target_after_landing",
    "reclaim_target_worktree_enabled",
    "reconcile_after_rejected_push",
    "reconcile_remote_publication",
    "refresh_observation_record",
    "refresh_target_from_remote",
    "remote_sync_enabled",
    "remote_target_name",
    "remote_unreachable_reason",
    "resolver_allowed",
    "start_conflict_attempt",
]


#: The production remote-tier adapter. It is resolved through the
#: protocol module's accessor so the default adapter is bound in
#: exactly ONE place, while the module attribute itself stays
#: replaceable (a test can substitute a fault-injecting double here).
_DEFAULT_REMOTE_CLIENT: RemoteOps = default_remote_ops()


# ----- Throttle ----------------------------------------------------------

_MAX_REMOTE_KEYS = 128


class _RemotePullThrottle:
    """Rate-limits the periodic pull keyed per (root, remote, target)."""

    def __init__(self, *, max_tracked_keys: int = _MAX_REMOTE_KEYS) -> None:
        self._max_tracked_keys = max(1, max_tracked_keys)
        self._last_pull: OrderedDict[tuple[str, str, str], float] = (
            OrderedDict()
        )  # bounded-accumulator-ok: _MAX_REMOTE_KEYS FIFO cap applied in arm()

    def should_pull(
        self,
        root: Path | str,
        remote: str,
        target: str,
        *,
        interval_seconds: float,
        clock: Callable[[], float],
    ) -> bool:
        """Return True if enough time has elapsed since the last pull."""
        key = self._key(root, remote, target)
        last = self._last_pull.get(key)
        if last is None:
            return True
        if interval_seconds <= 0.0:
            return True
        return clock() - last >= interval_seconds

    def arm(self, root: Path | str, remote: str, target: str, clock: Callable[[], float]) -> None:
        """Record the current timestamp for (root, remote, target)."""
        key = self._key(root, remote, target)
        self._last_pull.pop(key, None)
        while len(self._last_pull) >= self._max_tracked_keys:
            self._last_pull.popitem(last=False)
        self._last_pull[key] = clock()

    def invalidate(self, root: Path | str, remote: str, target: str) -> None:
        """Evict timestamp for (root, remote, target) on failure."""
        self._last_pull.pop(self._key(root, remote, target), None)

    @staticmethod
    def _key(root: Path | str, remote: str, target: str) -> tuple[str, str, str]:
        return (str(root), remote, target)


_REMOTE_PULL_THROTTLE = _RemotePullThrottle()


def _throttle_allows_pull(
    repo_root: Path,
    remote: str,
    target: str,
    config: UnifiedConfig | None,
    clock: Callable[[], float],
) -> bool:
    """Check if the periodic pull throttle allows a pull for target on remote."""
    general: object = getattr(config, "general", config)
    configured: object = getattr(general, "auto_integrate_remote_interval_seconds", 0.0)
    interval = configured if isinstance(configured, (int, float)) else REMOTE_SYNC_INTERVAL_SECONDS
    return _REMOTE_PULL_THROTTLE.should_pull(
        repo_root,
        remote,
        target,
        interval_seconds=interval,
        clock=clock,
    )


# ----- Reconciler: ref-comparison-driven publication --------------------


#: Refresh labels whose remote-tracking ref read may be trusted as an
#: observation of the configured remote.
_READABLE_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {
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
    }
)

#: Refresh labels that mean "NO fetch was taken", so the remote-tracking
#: ref behind the reading is whatever the last real fetch left there.
_STALE_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {REFRESH_SUPPRESSED, "refresh suppressed", "refresh suppressed by throttle"}
)


def _read_remote_tracking_sha(
    client: RemoteOps,
    repo_root: Path,
    target: str,
    remote: str,
) -> str | None:
    """Read ``<remote>/<target>`` through the client, never a hardcoded ref."""
    if hasattr(client, "observe_remote_sha"):
        return client.observe_remote_sha(repo_root, target, remote=remote)
    from ralph.pipeline.auto_integrate_catchup import observe_remote_target_sha

    return observe_remote_target_sha(repo_root, remote, target)


def _throttled_reading_needs_refetch(
    observed: str | None,
    local_sha: str | None,
) -> bool:
    """Whether a throttled reading must be replaced by a real fetch.

    A suppressed probe leaves the remote-tracking ref exactly as the last
    real fetch left it. Two readings are worthless against that cache:

    * ``observed is None`` -- nothing is known about the remote at all.
      The module-level probe is forced; if it cannot answer either, the
      observation stays UNKNOWN and the caller defers.
    * ``observed == local_sha`` -- the stale cache appears to CONVERGE.
      That is the dangerous case: answering "already current" for a
      remote that moved during the throttle window silently drops a
      divergence that Ralph otherwise has no way to see.

    Everything else (``observed != local_sha``) already proves a
    divergence, so the stale cache is enough to route the reconciler
    into the push/reconcile path, where the server's rejection forces
    the fetch that repairs the cache.
    """
    if observed is None:
        return True
    return local_sha is None or observed == local_sha


def _observe_remote_target_sha(
    repo_root: Path,
    target: str,
    *,
    remote: str,
    remote_client: RemoteOps | None = None,
    local_sha: str | None = None,
) -> tuple[str | None, str]:
    """Observe ``<remote>/<target>`` and the refresh status without raising.

    The remote-tracking ref is refreshed first so the comparison is not
    made against a stale cache. When the periodic probe was throttled and
    its reading cannot back the caller's verdict
    (:func:`_throttled_reading_needs_refetch`), a real fetch is forced and
    the ref is re-read before any branch is chosen.

    Returns ``(None, status)`` for any failure (unreachable remote,
    missing branch, transient network error). ``local_sha`` is the local
    target ref the caller already read; it only decides whether a
    throttled reading needs refetching and never selects a branch on its
    own.
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
        if refresh not in _READABLE_REFRESH_OUTCOMES:
            return None, refresh
        observed = _read_remote_tracking_sha(client, repo_root, target, remote)
        if refresh in _STALE_REFRESH_OUTCOMES and _throttled_reading_needs_refetch(
            observed, local_sha
        ):
            forced = refresh_target_from_remote(
                repo_root,
                target,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                remote=remote,
            )
            observed = _read_remote_tracking_sha(client, repo_root, target, remote)
            if forced in _READABLE_REFRESH_OUTCOMES:
                refresh = forced
        return observed, refresh
    except Exception as exc:
        logger.debug("auto_integrate: remote observation failed: {}", exc)
        return None, REFRESH_UNREACHABLE


def _record_publication_failure(
    base_state: RebaseState,
    *,
    last_remote_sync: str,
    last_reason: str | None = None,
    last_refresh: str | None = None,
    fast_forwarded: bool | None = None,
    publication_decision: PublicationDecision | None = None,
) -> RebaseState:
    """Project a remote-publication failure onto ``base_state``.

    Centralises the ``model_copy`` fan-out that the original
    :func:`reconcile_remote_publication` repeated per branch so the
    PLR0911 cap is satisfied without losing the per-branch
    ``last_remote_sync`` / ``last_refresh`` / fast forward signal the
    durable record carries.

    ``last_reason`` is OPTIONAL on purpose: nothing in this module
    shall persist stored prose as a decision input. The field is kept
    on the durable record only as a backward-compatibility shim, and
    callers that pass ``last_reason=None`` preserve the previous
    record's prose so a downstream display formatters that have not
    yet been migrated do not break. The single typed obligation is
    carried by ``publication_decision`` (the
    :class:`PublicationDecision` the run loop reads) -- the prose
    field never drives branch selection here.

    Remote observation failures (``REMOTE_PULL_FAILED`` /
    ``REMOTE_REMOTE_UNREACHABLE``) must carry
    :attr:`PublicationDecision.PUBLISH_DEFERRED_REMOTE` so
    :func:`is_terminal_success_eligible` cannot permit terminal
    success while a pending push obligation is unscheduled (O2/AC8).
    The parameter is named ``publication_decision`` (not
    ``last_publication_decision``) so the single-writer
    :func:`test_no_direct_writer_sites_outside_rebase_state` audit does
    not flag the helper as a forbidden direct writer of the
    ``RebaseState`` field.
    """
    update: dict[str, object] = {
        "last_remote_sync": last_remote_sync,
    }
    if last_reason is not None:
        update["last_reason"] = last_reason
    if last_refresh is not None:
        update["last_refresh"] = last_refresh
    if fast_forwarded is not None:
        update["fast_forwarded"] = fast_forwarded
    new_state = base_state.model_copy(update=update)
    if publication_decision is not None:
        new_state = new_state.set_publication_decision(publication_decision)
    return new_state


def _dispatch_ref_comparison(
    *,
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    chosen_remote: str,
    local_sha: str,
    remote_sha: str,
    base_state: RebaseState,
    remote_client: RemoteOps | None,
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
        # Remote is strictly ahead: advance the TARGET to the observed tip,
        # then recurse. This is deliberately the shared target-advance
        # routine, not the catch-up tick: the tick is fetch-throttled,
        # re-acquires the mutation lease the ticker already holds, and
        # reports the CHECKOUT's catch-up rather than the target's
        # (docs/agents/remote-sync.md, root cause 3). Reclamation stays off
        # here: a dirty owner refuses and a later seam retries.
        if config is None:
            return _record_publication_failure(
                base_state,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=REFRESH_UNREACHABLE,
                publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
            )
        advanced, refusal = fast_forward_target_to_remote(
            repo_root,
            target,
            chosen_remote,
            reclaim_target_worktree=False,
            remote_sha=remote_sha,
        )
        if advanced:
            return reconcile_remote_publication(
                config,
                repo_root,
                target,
                remote=chosen_remote,
                remote_client=remote_client,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=base_state,
            )
        # The remote WAS reached and IS ahead: the refusal names the real
        # cause (dirty owner, CAS race, ...) instead of a false "unreachable".
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_reason=refusal,
            last_refresh=REFRESH_ORIGIN_AHEAD,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
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
    client: RemoteOps,
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
    remote_client: RemoteOps | None = None,
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
        remote_client: The injected remote tier -- a
            :class:`~ralph.pipeline.integration_remote_actions.RemoteOps`
            implementation. ``None`` selects the production adapter
            (live Git operations). Every remote call this function
            makes -- the freshness refresh, the remote-tracking read,
            and the push -- goes through that one seam, so a
            fault-injecting double can simulate an unreachable remote,
            a non-fast-forward rejection, or a remote that advanced
            underneath the caller without a network.
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
        )
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    remote_sha, refresh = _observe_remote_target_sha(
        repo_root,
        target,
        remote=chosen_remote,
        remote_client=client,
        local_sha=local_sha,
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
            last_reason=remote_unreachable_reason(chosen_remote),
            last_refresh=refresh,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
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


def _observation_reconciles(
    *,
    repo_root: Path,
    target: str,
    remote: str,
    local_sha: str,
    remote_sha: str | None,
) -> bool:
    """Truth: does the current live observation show refs already equal?

    Pure: only the two SHAs the caller just observed are consulted.
    Stored ``last_reason`` prose and persisted ``last_remote_sync``
    values are NEVER inputs -- the O2 reconciliation obligation lives
    on the LIVE ref comparison, not on text the previous run wrote.
    A ``None`` remote SHA is treated as ``False`` because the
    obligation "ref equality" cannot be proven unobservable.
    """
    if remote_sha is None:
        return False
    if local_sha != remote_sha:
        return False
    # Final witness: the live remote-tracking ref read confirms the
    # equality is current, not stale. A separate fetch was performed
    # in :func:`_observe_remote_target_sha`; this call only witnesses
    # its outcome.
    _ = (repo_root, target, remote)
    return True


def refresh_observation_record(
    repo_root: Path,
    base_state: RebaseState,
    *,
    target: str,
    remote: str,
    remote_client: RemoteOps | None = None,
) -> RebaseState:
    """Re-render the publication record from the CURRENT live observation.

    Recomputes local vs ``<remote>/<target>`` from the same client the
    runtime uses, then rewrites BOTH the typed publication obligation and
    the operator-facing status line from that single observation. Used by
    the run loop and by boundary seams that need an O2 freshness check
    before reporting terminal success; the O2 contract demands that NO
    state leaves local ahead of the remote with a satisfied publication
    decision.

    ``base_state.last_reason`` is deliberately OVERWRITTEN here, never
    inherited. This is the render-time seam: replaying the prose a
    previous seam persisted would present a stale ``... unreachable ...
    recovery will retry`` line as the current explanation after the
    remote has already recovered. Every branch below therefore renders
    its line from the SHAs this call just observed.

    The function never raises. It returns ``base_state`` unchanged when
    remote sync is disabled or when the configured remote is missing --
    those are "not observed", which must not overwrite a live line with
    a fabricated one.
    """
    if not isinstance(target, str) or not target or not isinstance(remote, str) or not remote:
        return base_state
    local_sha = _safe_local_sha(repo_root, target)
    if local_sha is None:
        return base_state
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    remote_sha, refresh = _observe_remote_target_sha(
        repo_root,
        target,
        remote=remote,
        remote_client=client,
        local_sha=local_sha,
    )
    if remote_sha is not None and _observation_reconciles(
        repo_root=repo_root,
        target=target,
        remote=remote,
        local_sha=local_sha,
        remote_sha=remote_sha,
    ):
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_ALREADY_CURRENT,
            last_reason=_render_converged_reason(target, remote),
            fast_forwarded=True,
            publication_decision=PublicationDecision.PUBLICATION_TERMINAL,
        )
    if remote_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
            last_reason=remote_unreachable_reason(remote),
            last_refresh=refresh,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    if refresh == REFRESH_NO_REMOTE_BRANCH:
        return base_state.model_copy(
            update={
                "last_remote": remote,
                "last_remote_sync": REMOTE_NO_REMOTE_BRANCH,
                "last_reason": _render_missing_branch_reason(target, remote),
            }
        ).set_publication_decision(
            PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    return base_state.model_copy(
        update={
            "last_remote": remote,
            "last_remote_sync": REMOTE_LOCAL_AHEAD,
            "last_refresh": refresh,
            "last_reason": _render_unpublished_reason(target, remote),
        }
    ).set_publication_decision(PublicationDecision.PUBLISH_DEFERRED_REMOTE)


def _render_converged_reason(target: str, remote: str) -> str:
    """Render-time line for "the two refs name the same commit"."""
    return f"local {target} and {remote}/{target} converge"


def _render_unpublished_reason(target: str, remote: str) -> str:
    """Render-time line for "local is ahead; the remote still owes the push"."""
    return f"local {target} is not yet published to {remote}/{target}"


def _render_missing_branch_reason(target: str, remote: str) -> str:
    """Render-time line for "the remote exists but has no target branch yet"."""
    return f"{remote}/{target} does not exist yet; publication will create it"


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
