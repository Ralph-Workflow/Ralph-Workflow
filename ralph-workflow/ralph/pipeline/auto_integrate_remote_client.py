"""Remote-client / throttle path split from :mod:`ralph.pipeline.auto_integrate_remote_sync`.

Owns the default production :class:`_DefaultRemoteClient` (and the
shared instance), the per-(root, remote, target) pull throttle, the
remote-tracking-SHA reader, and the pull-side
:func:`pull_and_reconcile_target` entry point plus its refresh-label
dispatcher. Push-side and publication helpers live next door in
:mod:`ralph.pipeline.auto_integrate_remote_push`; the orchestrator
(:mod:`ralph.pipeline.auto_integrate_remote_sync`) keeps the
ref-comparison reconciler and the closed :class:`RemoteSyncStatus`
vocabulary.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
)
from ralph.pipeline.auto_integrate_remote_constants import (
    FETCH_TIMEOUT_SECONDS,
    REMOTE_LOCAL_AHEAD,
    REMOTE_NO_REMOTE,
    REMOTE_NO_REMOTE_BRANCH,
    REMOTE_PULL_FAILED,
    REMOTE_PULLED,
    REMOTE_RECONCILED,
    reclaim_target_worktree_enabled,
    remote_sync_enabled,
    remote_target_name,
    remote_unreachable_reason,
)
from ralph.pipeline.auto_integrate_sync import (
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
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger


__all__ = [
    "_dispatch_pull_outcome",
    "pull_and_reconcile_target",
    "refresh_target_from_remote",
]


class _DefaultRemoteClient:
    """Default production implementation delegating to live Git operations."""

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        try:
            import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

            return _remote_sync.refresh_target_from_remote(
                repo_root,
                target,
                timeout_seconds=timeout_seconds,
                remote=remote,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote refresh query failed: {}", exc)
            return REFRESH_UNREACHABLE

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> _remote_push_module.PushResult:
        try:
            return _remote_push_module.push_branch_to_single_remote(
                repo_root,
                branch,
                remote=remote,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote push failed: {}", exc)
            return _remote_push_module.PushResult(
                _remote_push_module.PushStatus.UNREACHABLE,
                remote,
                branch,
                detail=str(exc),
            )

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        return _remote_tracking_sha(repo_root, target, remote)


_DEFAULT_REMOTE_CLIENT = _DefaultRemoteClient()


def _resolve_effective_ledger(
    ledger: AgentFailureLedger | None,
) -> AgentFailureLedger | None:
    from ralph.pipeline.auto_integrate_conflict_budget import resolve_failure_ledger

    return resolve_failure_ledger(ledger)


def _prepare_effective_resolver(
    rebase_stop_resolver: RebaseStopResolver | None,
    prior_state: RebaseState,
    target: str,
    identity: ConflictIdentity,
    ledger: AgentFailureLedger | None,
    candidates: Sequence[str],
) -> tuple[RebaseStopResolver | None, bool]:
    if rebase_stop_resolver is None:
        return None, False
    from ralph.pipeline.auto_integrate_remote_sync import (
        resolver_allowed as _resolver_allowed,
    )
    from ralph.pipeline.auto_integrate_remote_sync import (
        start_conflict_attempt as _start_conflict_attempt,
    )

    if not _resolver_allowed(
        prior_state,
        target,
        identity,
        ledger=ledger,
        candidates=candidates,
    ) or not _start_conflict_attempt(identity):
        return None, False
    return rebase_stop_resolver, True


def pull_and_reconcile_target(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    *,
    remote: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    prior: RebaseState | None = None,
    remote_client: RemoteClient | None = None,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> RebaseState | None:
    """Run the throttled pull side; reconcile the local target from the remote.

    Uses the configured remote when synchronization is enabled; otherwise it
    performs no remote operation.
    """
    if config is None or not remote_sync_enabled(config):
        return None

    client = remote_client or _DEFAULT_REMOTE_CLIENT
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    try:
        # Look up via the orchestrator so a test monkeypatch on
        # ``ralph.pipeline.auto_integrate_remote_sync.observe_conflict_identity``
        # reaches the same binding this client uses.
        from ralph.pipeline.auto_integrate_remote_sync import observe_conflict_identity

        identity = observe_conflict_identity(repo_root, target, scope="remote")
    except Exception:
        identity = ConflictIdentity(scope="remote")
    prior_state = prior or RebaseState()
    effective_ledger = _resolve_effective_ledger(ledger)
    effective_resolver, booked = _prepare_effective_resolver(
        rebase_stop_resolver,
        prior_state,
        target,
        identity,
        effective_ledger,
        candidates,
    )
    try:
        import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

        if not _remote_sync._throttle_allows_pull(repo_root, chosen_remote, target, config, clock):
            state = _record_remote_state(
                target,
                last_remote_sync="refresh suppressed",
                last_refresh="refresh suppressed by throttle",
                reason="remote freshness probe suppressed by configured interval",
                freshness_verdict="unverified",
                freshness_source="suppressed probe",
            )
        else:
            refresh = client.refresh_target(
                repo_root,
                target,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                remote=chosen_remote,
            )
            pulled = _remote_sync._dispatch_pull_outcome(
                refresh, repo_root, target, chosen_remote, clock, config, effective_resolver
            )
            if pulled is None:
                state = prior_state
            else:
                from ralph.git.merge import branch_sha

                try:
                    target_sha = branch_sha(repo_root, target)
                except OSError:
                    target_sha = None
                state = pulled.model_copy(
                    update={"last_remote": chosen_remote, "freshness_target_sha": target_sha}
                )
        from ralph.pipeline.auto_integrate_remote_sync import apply_conflict_budget

        return apply_conflict_budget(
            state,
            prior=prior_state,
            target=target,
            resolver_suppressed=rebase_stop_resolver is not None and effective_resolver is None,
            identity=identity,
        )
    finally:
        if booked:
            from ralph.pipeline.auto_integrate_remote_sync import (
                finish_conflict_attempt as _finish_cb,
            )

            _finish_cb(identity)


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
      ``REMOTE_PULL_FAILED`` with an operator line naming the
      CONFIGURED remote. The line is rendered from this observation
      and is never read back as a decision input.
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
            reason=remote_unreachable_reason(chosen_remote),
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
            import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

            _remote_sync._fast_forward_local_target_to_remote(
                repo_root, target, chosen_remote, config
            )
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
            last_remote_sync="already current",
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


def _arm_throttle(
    repo_root: Path,
    remote: str,
    target: str,
    clock: Callable[[], float],
) -> None:
    from ralph.pipeline.auto_integrate_remote_sync import _REMOTE_PULL_THROTTLE

    _REMOTE_PULL_THROTTLE.arm(repo_root, remote, target, clock)


def _consume_throttle_signal_unhealthy(repo_root: Path, remote: str, target: str) -> None:
    """A failed fetch must NOT arm the next window."""
    from ralph.pipeline.auto_integrate_remote_sync import _REMOTE_PULL_THROTTLE

    _REMOTE_PULL_THROTTLE.invalidate(repo_root, remote, target)


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
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_reason=reason,
        last_target=target,
        last_remote_sync=last_remote_sync,
        last_refresh=last_refresh,
        freshness_verdict=freshness_verdict,
        freshness_source=freshness_source,
        freshness_safe=freshness_safe,
    )
