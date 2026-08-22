"""Fail-open remote freshness, reconciliation, publication, and retries.

Configured remotes are checked by default; absent or unreachable remotes record
local-fleet or degraded provenance without interrupting local integration.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.pipeline.auto_integrate_sync import (
    DEFAULT_REFRESH_REMOTE,
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_LOCAL_BRANCH,
    REFRESH_NO_REMOTE,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


# -- Public outcome strings carried on RebaseState.last_remote_sync -----

#: The pull fetched and the local target was fast-forwarded to the
#: remote tip. Recorded so the operator line can name what happened.
REMOTE_PULLED = "pulled"
#: The pull fetched and the local target was rebased onto the remote
#: target. The remote's existing commits were NEVER rewritten;
#: only the unpublished local commits on top of ``refs/heads/<target>``
#: were replayed on top of ``<remote>/<target>``.
REMOTE_RECONCILED = "reconciled"
#: The pull fetched, ``<remote>/<target>`` did not exist, the push
#: created it. Distinct outcome so the operator sees a remote branch
#: appear rather than discovering it later.
REMOTE_CREATED = "created"
#: The push succeeded after a normal landing (or after one or more
#: reconcile cycles on a non-fast-forward rejection).
REMOTE_PUSHED = "pushed"
#: The push was rejected non-fast-forward and a reconcile cycle
#: succeeded; next attempt will retry the push with the reconciled
#: target.
REMOTE_PUSH_RECONCILED = "push reconciled"
#: The push was rejected and the bounded reconcile loop exhausted
#: without landing. Recorded, not terminal -- the next seam retries.
REMOTE_PUSH_REJECTED = "push rejected"
#: The pull failed for a transient reason (unreachable, timeout,
#: auth). Local integration proceeds untouched. The reason is recorded.
REMOTE_PULL_FAILED = "pull failed"
#: The remote named in config is not configured in this repository;
#: the step degrades to local-only with a recorded skip.
REMOTE_NO_REMOTE = "no remote"
#: ``<remote>/<target>`` does not exist; nothing to pull. The local
#: integration proceeds untouched.
REMOTE_NO_REMOTE_BRANCH = "no remote branch"
#: The local target is strictly ahead of the remote. No remote-side
#: motion needed; the next landing pushes the local state to the
#: remote.
REMOTE_LOCAL_AHEAD = "local ahead"
REMOTE_ALREADY_CURRENT = "already current"
#: Failure classification -- the recorded reason the run did not
#: publish, used both for the operator line and the waiting-state
#: end-of-run summary.
REMOTE_REMOTE_UNREACHABLE = "origin unreachable"
REMOTE_AUTH_FAILED = "auth failed"
REMOTE_REJECTED_BY_HOOK = "rejected by remote hook"
REMOTE_NON_FAST_FORWARD = "non-fast-forward"
REMOTE_TIMEOUT = "timeout"

FETCH_TIMEOUT_SECONDS = 10.0
REMOTE_SYNC_INTERVAL_SECONDS = 300.0
REMOTE_BACKOFF_MAX_SECONDS = 300.0

#: Per-seam remote reconcile cap; later seams always retry.
_MAX_REMOTE_SYNC_ATTEMPTS = 3
_MAX_REMOTE_KEYS = 128


# ----- Helpers ---------------------------------------------------------


def remote_sync_enabled(config: object | None) -> bool:
    """Whether configured-remote synchronization is enabled by policy."""
    if config is None:
        return False
    general: object = getattr(config, "general", config)
    enabled: object = getattr(general, "auto_integrate_remote_enabled", True)
    return isinstance(enabled, bool) and enabled


def remote_target_name(config: object, *, default: str = DEFAULT_REFRESH_REMOTE) -> str:
    """Configured remote name, or the default ``origin``."""
    general: object = getattr(config, "general", config)
    remote: object = getattr(general, "auto_integrate_remote", default)
    return remote.strip() if isinstance(remote, str) and remote.strip() else default


def reclaim_target_worktree_enabled(config: object | None) -> bool:
    """Return the target-owner reclamation policy, defaulting safely to enabled."""
    general: object = getattr(config, "general", config)
    configured: object = getattr(general, "auto_integrate_reclaim_target_worktree", True)
    return configured is not False


# ----- Pull-side reconcile --------------------------------------------


def pull_and_reconcile_target(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    *,
    remote: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    """Run the throttled pull side; reconcile the local target from the remote.

    Uses the configured remote when synchronization is enabled; otherwise it
    performs no remote operation.

    The throttle is keyed ``(root, remote, target)`` and armed only
    by a HEALTHY fetch: a transient blip cannot buy a whole window
    of unrefreshed probes. Interval zero = every seam (AC-19).

    Reconcile outcomes:

    * **Local ahead** -- no local ref moves on the pull side; the
      next landing pushes the local state.
    * **Remote strictly ahead** -- ``refs/heads/<target>`` is
      advanced to ``<remote>/<target>`` via the same landing
      machinery as Part A (owning worktree's ``merge --ff-only``
      first, atomic CAS fallback, never a force move). Recorded as
      ``REMOTE_PULLED``.
    * **Diverged** -- the local target is rebased onto the remote
      target through the existing rebase engine; the operator-visible
      outcome is ``REMOTE_RECONCILED``. Remote's commits are never
      rewritten (same SHA before and after).
    * **Pull failure** -- logged and recorded as
      ``REMOTE_PULL_FAILED`` with the recorded reason so the run
      can render it on the operator line.

    Args:
        config: Unified run configuration; ``None`` skips every
            remote call. The enablement flag and configured remote
            are read here so the helper is the single source of
            truth for the gated-on check.
        repo_root: Repository root in which to run the probe.
        target: Branch whose freshness to observe and reconcile.
        remote: Override the configured remote (test-only seam).
        clock: Monotonic time source for the throttle, injected so
            the test suite advances time without sleeping.

    Returns:
        :class:`RebaseState` annotated with ``last_remote_sync`` and
        ``last_refresh``; or ``None`` when the call is a no-op
        (disabled, throttled, or no remote configured).
    """
    if not remote_sync_enabled(config):
        # AC-13 byte-identical contract: with the flag false this
        # helper does NOTHING.
        return None

    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    if not _throttle_allows_pull(repo_root, chosen_remote, target, config, clock):
        return _record_remote_state(
            target,
            last_remote_sync="refresh suppressed",
            last_refresh="refresh suppressed by throttle",
            reason="remote freshness probe suppressed by configured interval",
            freshness_verdict="unverified",
            freshness_source="suppressed probe",
        )

    refresh = refresh_target_from_remote(
        repo_root,
        target,
        timeout_seconds=FETCH_TIMEOUT_SECONDS,
        remote=chosen_remote,
    )
    state = _dispatch_pull_outcome(
        refresh, repo_root, target, chosen_remote, clock, config, rebase_stop_resolver
    )
    if state is None:
        return None
    from ralph.git.merge import branch_sha

    try:
        target_sha = branch_sha(repo_root, target)
    except OSError:
        # Test/dry-run callers can provide an unmaterialized path; freshness
        # classification remains valid but no target SHA may be claimed.
        target_sha = None
    return state.model_copy(
        update={"last_remote": chosen_remote, "freshness_target_sha": target_sha}
    )


def _dispatch_pull_outcome(
    refresh: str,
    repo_root: Path,
    target: str,
    chosen_remote: str,
    clock: Callable[[], float],
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    """Translate a pull refresh outcome into the recorded ``RebaseState`` (AC-15..20).

    Each refresh label maps to one branch of the byte-identical contract:

    * ``REFRESH_LOCAL_FLEET`` -- the local ref store already mirrors the
      remote; arm the throttle and report no operator-visible change.
    * ``REFRESH_UNREACHABLE`` -- fetch failed; recorded as
      ``REMOTE_PULL_FAILED`` with the standard unreachable reason.
    * ``REFRESH_NO_REMOTE`` / ``REFRESH_NO_REMOTE_BRANCH`` -- the remote
      or its target branch is missing; recorded as ``REMOTE_NO_REMOTE``
      (resp. ``REMOTE_NO_REMOTE_BRANCH``) and the throttle is NOT armed.
    * ``REFRESH_NO_LOCAL_BRANCH`` -- the local target is missing; the
      local-only contract takes over and the caller's skip reason
      applies (``None`` returned).
    * ``REFRESH_ORIGIN_AHEAD`` -- strictly ahead; the local target is
      fast-forwarded to the remote tip via Part-A's landing table.
      Recorded as ``REMOTE_PULLED``.
    * Anything else -- already current OR an unknown (future-proof)
      outcome; the throttle is armed so a healthy "nothing changed"
      read still counts as a fresh observation.
    """
    state: RebaseState | None = None
    if refresh == REFRESH_LOCAL_FLEET:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync="local fleet",
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="shared local ref",
        )
    elif refresh == REFRESH_UNREACHABLE:
        logger.debug("auto_integrate: remote sync pull unreachable on '{}'", target)
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
        state = _record_remote_state(
            target,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_refresh=refresh,
            reason=REMOTE_REMOTE_UNREACHABLE,
            freshness_verdict="degraded",
            freshness_source="fetch",
        )
    elif refresh in (REFRESH_NO_REMOTE, REFRESH_NO_REMOTE_BRANCH):
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
        state = _record_remote_state(
            target,
            last_remote_sync=(
                REMOTE_NO_REMOTE_BRANCH if refresh == REFRESH_NO_REMOTE_BRANCH else REMOTE_NO_REMOTE
            ),
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified" if refresh == REFRESH_NO_REMOTE else "degraded",
            freshness_source="shared local ref" if refresh == REFRESH_NO_REMOTE else "fetch",
        )
    elif refresh == REFRESH_NO_LOCAL_BRANCH:
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
    elif refresh == REFRESH_ORIGIN_AHEAD:
        try:
            _fast_forward_local_target_to_remote(repo_root, target, chosen_remote, config)
        except Exception as exc:
            logger.warning("auto_integrate: pull-side ff failed: {}", exc)
            _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=refresh,
                reason=str(exc),
                freshness_verdict="unsafe",
                freshness_source="fetch",
                freshness_safe=False,
            )
        else:
            _arm_throttle(repo_root, chosen_remote, target, clock)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULLED,
                last_refresh=refresh,
                reason=None,
                freshness_verdict="verified",
                freshness_source="fetch",
            )
    elif refresh == REFRESH_LOCAL_AHEAD:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync=REMOTE_LOCAL_AHEAD,
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="fetch",
        )
    elif refresh == REFRESH_DIVERGED:
        from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote

        reconciliation = reconcile_target_onto_remote(
            repo_root,
            target,
            chosen_remote,
            reclaim_target_worktree=reclaim_target_worktree_enabled(config),
            rebase_stop_resolver=rebase_stop_resolver,
            conflict_resolution_config=config,
        )
        if reconciliation.reconciled:
            _arm_throttle(repo_root, chosen_remote, target, clock)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_RECONCILED,
                last_refresh=refresh,
                reason=None,
                freshness_verdict="verified",
                freshness_source="fetch",
            )
        else:
            _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=refresh,
                reason=reconciliation.reason,
                freshness_verdict="degraded" if reconciliation.cleanly_aborted else "unsafe",
                freshness_source="fetch",
                freshness_safe=reconciliation.cleanly_aborted,
            )
    else:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync=REMOTE_ALREADY_CURRENT,
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="fetch",
        )
    return state


def _fast_forward_local_target_to_remote(
    repo_root: Path,
    target: str,
    remote: str,
    config: UnifiedConfig | None,
) -> None:
    """Move ``refs/heads/<target>`` to ``refs/remotes/<remote>/<target>``.

    Uses the existing Part-A fast-forward helpers so the same
    worktree/CAS semantics apply: the target's owning worktree's
    ``merge --ff-only`` runs first; the atomic CAS is the fallback
    when the target has no live checkout. Never force-moves -- a
    non-fast-forward raises and the caller records the failure.
    """
    from ralph.git.merge import branch_sha, is_ancestor
    from ralph.pipeline.auto_integrate_ff import fast_forward_target

    remote_sha = _remote_tracking_sha(repo_root, target, remote)
    if remote_sha is None:
        raise RuntimeError(f"{remote}/{target} missing for ff")
    local_sha = branch_sha(repo_root, target)
    if local_sha is None or remote_sha == local_sha:
        return
    if not is_ancestor(repo_root, local_sha, remote_sha):
        # Diverged -- the pull side alone cannot reconcile; the
        # caller handles divergence through the dedicated reconcile
        # loop. Surface as a clean RuntimeError so the caller's
        # except can record a no-op.
        raise RuntimeError(f"local/{target} diverged from {remote}/{target}")
    ok, reason = fast_forward_target(
        repo_root,
        target,
        remote_sha,
        reclaim_target_worktree=reclaim_target_worktree_enabled(config),
    )
    if not ok:
        raise RuntimeError(reason)


def _remote_tracking_sha(repo_root: Path, target: str, remote: str) -> str | None:
    """``refs/remotes/<remote>/<target>`` SHA, or ``None`` when absent."""
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"),
        cwd=repo_root,
        label=f"git-remote-tracking-sha-pull-{remote}",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# ----- Throttle ----------------------------------------------------------


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
        key = self._key(root, remote, target)
        last = self._last_pull.get(key)
        if last is None:
            return True
        if interval_seconds <= 0.0:
            return True
        return clock() - last >= interval_seconds

    def arm(self, root: Path | str, remote: str, target: str, clock: Callable[[], float]) -> None:
        key = self._key(root, remote, target)
        self._last_pull.pop(key, None)
        while len(self._last_pull) >= self._max_tracked_keys:
            self._last_pull.popitem(last=False)
        self._last_pull[key] = clock()

    def invalidate(self, root: Path | str, remote: str, target: str) -> None:
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


def _arm_throttle(
    repo_root: Path,
    remote: str,
    target: str,
    clock: Callable[[], float],
) -> None:
    _REMOTE_PULL_THROTTLE.arm(repo_root, remote, target, clock)


def _consume_throttle_signal_unhealthy(repo_root: Path, remote: str, target: str) -> None:
    """A failed fetch must NOT arm the next window."""
    _REMOTE_PULL_THROTTLE.invalidate(repo_root, remote, target)


# ----- Push hook --------------------------------------------------------


def push_target_after_landing(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    record: RebaseState,
    *,
    remote: str | None = None,
) -> RebaseState:
    """Push the local target to the configured remote after a successful land.

    Returns the ``RebaseState`` annotated with ``last_remote_sync`` and
    ``last_push``. Never raises; never reverts a local landing. The
    """
    if not remote_sync_enabled(config):
        return record
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)

    # Resolve dynamically so test monkeypatch on ralph.git.remote_push
    # is observed by both production code and tests.
    result = _remote_push_module.push_branch_to_single_remote(
        repo_root,
        target,
        remote=chosen_remote,
        timeout_seconds=_remote_push_module.PUSH_TIMEOUT_SECONDS,
    )
    typed_result = _coerce_push_result(result, remote=chosen_remote, target=target)
    return record.model_copy(
        update={
            "last_push": typed_result.summary,
            "last_remote": typed_result.remote,
            "last_push_status": typed_result.status.value,
            "last_remote_sync": _remote_sync_status(typed_result.status),
            "last_reason": (
                None if typed_result.success else typed_result.detail or typed_result.status.value
            ),
        },
    )


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
) -> RebaseState:
    """Try a bounded reconcile-then-push cycle after a non-fast-forward.

    Per-seam cap is ``_MAX_REMOTE_SYNC_ATTEMPTS`` (3), matching the
    Part-A landing loop budget. Exhausting the cap is NOT a give-up:
    the pending push is carried forward on
    :attr:`RebaseState.last_remote_sync` as
    ``REMOTE_PUSH_REJECTED`` and retried on the next seam, so the
    fail-open contract holds for the whole run, not just one seam.
    """
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
        )
        if outcome.success:
            return final_record
        if not outcome.pushed:
            break
    return final_record


def _attempt_reconcile_and_push(
    *,
    repo_root: Path,
    target: str,
    remote: str,
    config: UnifiedConfig | None,
    reintegrate: Callable[[], bool] | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> _PushOutcome:
    """Run a single fetch -> rebase-target-onto-remote -> push cycle."""
    refresh = refresh_target_from_remote(
        repo_root,
        target,
        timeout_seconds=FETCH_TIMEOUT_SECONDS,
        remote=remote,
    )
    if refresh == REFRESH_NO_REMOTE:
        return _PushOutcome(
            success=False,
            summary=f"remote '{remote}' not configured",
            pushed=False,
            status=_remote_push_module.PushStatus.MISSING_REMOTE,
        )
    if refresh == REFRESH_UNREACHABLE:
        return _PushOutcome(
            success=False,
            summary=f"push of {target} to {remote} failed: {REMOTE_REMOTE_UNREACHABLE}",
            pushed=True,
            status=_remote_push_module.PushStatus.UNREACHABLE,
        )
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
    result = _remote_push_module.push_branch_to_single_remote(
        repo_root,
        target,
        remote=remote,
        timeout_seconds=_remote_push_module.PUSH_TIMEOUT_SECONDS,
    )
    typed_result = _coerce_push_result(result, remote=remote, target=target)
    return _PushOutcome(
        success=typed_result.success,
        summary=typed_result.summary,
        pushed=typed_result.status is not _remote_push_module.PushStatus.MISSING_REMOTE,
        status=typed_result.status,
        created_remote_branch=typed_result.status is _remote_push_module.PushStatus.CREATED,
    )


# ----- State helpers ---------------------------------------------------


def _record_remote_state(
    target: str,
    *,
    last_remote_sync: str,
    last_refresh: str | None,
    reason: str | None,
    freshness_verdict: str | None = None,
    freshness_source: str | None = None,
    freshness_safe: bool = True,
) -> RebaseState:
    """Build the canonical ``RebaseState`` annotation for a remote sync outcome."""
    return RebaseState(
        last_action="skipped",
        last_reason=reason,
        last_target=target,
        last_remote_sync=last_remote_sync,
        last_refresh=last_refresh,
        freshness_verdict=freshness_verdict,
        freshness_source=freshness_source,
        freshness_safe=freshness_safe,
    )


__all__ = [
    "FETCH_TIMEOUT_SECONDS",
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
    "pull_and_reconcile_target",
    "push_target_after_landing",
    "reconcile_after_rejected_push",
    "remote_sync_enabled",
    "remote_target_name",
]


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
