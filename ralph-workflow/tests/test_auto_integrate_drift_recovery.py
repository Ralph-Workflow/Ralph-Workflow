"""Drift-recovery tests for the O2 publication reconciler.

Each test pins one acceptance criterion (AC5-AC9) from
``PLAN.md``. The tests are black-box: they drive the public
``reconcile_remote_publication`` and worker entry points with
in-memory fakes and assert on the resulting ``RebaseState``,
concrete push calls, and ref convergence so the implementation
cannot regress to fire-once publication or ``origin``/``main`` defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.git import remote_push as _remote_push_module
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_catchup import AutoIntegrateCatchupWorker
from ralph.pipeline.auto_integrate_ff import RemoteClient
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_remote_reconcile import ReconciliationOutcome
from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED
from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


class _RecordingRemoteClient(RemoteClient):
    """In-memory remote client that records the configured remote/target pair."""

    def __init__(
        self,
        *,
        remote_sha: str | None = "remote-head-sha",
        refresh_status: str = "refreshed",
    ) -> None:
        self.remote_sha = remote_sha
        self.refresh_status = refresh_status
        self.observed: list[tuple[str, str, str]] = []
        self.pushed: list[tuple[str, str]] = []
        self.refresh_outcomes: list[tuple[str, str, str]] = []

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        self.observed.append((remote, target, str(repo_root)))
        return self.remote_sha

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> str:
        self.refresh_outcomes.append((remote, target, str(repo_root)))
        return self.refresh_status

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, branch))
        return _remote_push_module.PushResult(_remote_push_module.PushStatus.PUSHED, remote, branch)


class _UnreachableRemoteClient(RemoteClient):
    """In-memory remote client that reports UNREACHABLE for every push."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.observed: list[tuple[str, str, str]] = []

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        self.observed.append((remote, target, str(repo_root)))
        return None  # unreachable: no SHA

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> str:
        return "unreachable"

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, branch))
        return _remote_push_module.PushResult(
            _remote_push_module.PushStatus.UNREACHABLE, remote, branch
        )


def _dummy_resolver(_root: Path, _target: str, _stop: RebaseStop) -> bool:
    return True


def test_non_default_target_remote_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: target=release + remote=tacos converge; no origin/main literal.

    Uses the in-memory client to record every observed/pushed call.
    The recorded calls MUST non-trivially use ``tacos`` (not ``origin``)
    and the caller-supplied target ``release`` (not ``main``). The
    reconciler never reads ``origin`` or ``main`` defaults.
    """
    client = _RecordingRemoteClient(remote_sha="tacos-release-remote-sha")
    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
        )
    )

    def _branch_sha(_root: Path, _target: str) -> str:
        return "tacos-release-local-sha"

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "tacos-release-remote-sha" and descendant == "tacos-release-local-sha"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=client,
        rebase_stop_resolver=_dummy_resolver,
    )

    # 1. Non-vacuous execution: observations and push actually happened
    assert len(client.observed) > 0
    assert len(client.pushed) > 0

    # 2. Exact configured remote/target pairing throughout
    assert all(remote == "tacos" and target == "release" for remote, target, _ in client.observed)
    assert client.pushed == [("tacos", "release")]
    assert state.last_remote == "tacos"
    assert state.last_target == "release"

    # 3. No fallback leakage of origin or main
    assert "origin" not in str(client.observed)
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.observed)
    assert "main" not in str(client.pushed)

    # 4. Same holds when remote is omitted from function call: uses configured tacos
    client.pushed.clear()
    client.observed.clear()
    state_from_cfg = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote_client=client,
        rebase_stop_resolver=_dummy_resolver,
    )
    assert client.pushed == [("tacos", "release")]
    assert state_from_cfg.last_remote == "tacos"
    assert "origin" not in str(client.observed)
    assert "main" not in str(client.observed)


def test_throttle_deferred_probe_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: a fresh land pushes even when the fetch probe was throttle-suppressed.

    The reconciler MUST attempt the push when the local SHA is strictly ahead
    of the remote, regardless of probe freshness or previous in-memory state.
    After push, calling again with matching ref reports already current.
    """
    client = _RecordingRemoteClient(remote_sha="remote-sha-1")

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-sha-2"

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "remote-sha-1" and descendant == "local-sha-2"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)

    config = UnifiedConfig()
    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "main",
        remote="origin",
        remote_client=client,
    )
    assert client.pushed == [("origin", "main")]
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert state.last_push_status == "pushed"

    # Convergence proof: next reconcile when remote matches local reports convergence
    client.remote_sha = "local-sha-2"
    converged_state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "main",
        remote="origin",
        remote_client=client,
    )
    assert converged_state.last_remote_sync == remote_sync.REMOTE_ALREADY_CURRENT
    assert converged_state.fast_forwarded is True
    # No second push was needed
    assert len(client.pushed) == 1


def test_killed_between_landing_and_push_recovery_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7: a state with local ahead of remote still pushes on next startup in a worktree.

    Simulates a process killed between local landing and push (the integration
    record was cleared or not retained). On the next startup,
    ``recover_incomplete_integration`` runs its startup publication seam
    and converges local onto remote.
    """
    client = _RecordingRemoteClient(remote_sha="stale-remote-sha")

    def _branch_sha(_root: Path, _target: str) -> str:
        return "landed-local-sha"

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "stale-remote-sha" and descendant == "landed-local-sha"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
        )
    )
    scope = WorkspaceScope(tmp_path)

    # Patch remote_sync._DEFAULT_REMOTE_CLIENT so recover_incomplete_integration uses client
    monkeypatch.setattr(remote_sync, "_DEFAULT_REMOTE_CLIENT", client)

    outcome = recover_incomplete_integration(
        scope,
        config=config,
        rebase_stop_resolver=_dummy_resolver,
    )
    assert client.pushed == [("tacos", "release")]
    assert outcome is not None
    assert outcome.last_push_status == "pushed"
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_unreachable_then_reachable_auto_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8: worker tick with unreachable remote retries and pushes on recovery.

    Exercises ``AutoIntegrateCatchupWorker`` directly with configured remote/target:
    Tick 1: the remote is UNREACHABLE; the worker runs without error and does not push.
    Tick 2: the remote recovers; the worker's post-tick reconciler pushes.
    """

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-sha"

    def _resolve_target(_config: UnifiedConfig, _root: Path) -> str:
        return "release"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.resolve_integration_target",
        _resolve_target,
    )

    unreachable = _UnreachableRemoteClient()
    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
        )
    )

    def _tick() -> str:
        return "up-to-date"

    worker = AutoIntegrateCatchupWorker(
        config,
        tmp_path,
        remote_client=unreachable,
        rebase_stop_resolver=_dummy_resolver,
        tick=_tick,
    )
    # Tick 1: unreachable
    worker._tick_once()
    assert unreachable.pushed == []

    # Remote recovers
    reachable = _RecordingRemoteClient(remote_sha="old-remote-sha")

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "old-remote-sha" and descendant == "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)
    worker._remote_client = reachable

    # Tick 2: reachable -> auto pushes to tacos/release
    worker._tick_once()
    assert reachable.pushed == [("tacos", "release")]
    assert "origin" not in str(reachable.pushed)
    assert "main" not in str(reachable.pushed)


def test_concurrent_remote_advance_calls_reconcile_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9: concurrent remote advance triggers fetch -> divergence reconciliation -> push.

    When local and remote have both advanced independently and conflict,
    ref comparison detects divergence and passes the resolver to
    ``reconcile_after_rejected_push``, which reconciles and pushes the converged state.
    """
    client = _RecordingRemoteClient(
        remote_sha="diverged-remote-sha",
        refresh_status=REFRESH_DIVERGED,
    )

    def _is_ancestor(_root: Path, _ancestor: str, _descendant: str) -> bool:
        return False

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)
    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)

    resolver_invoked = False

    def _recording_resolver(_root: Path, _target: str, _stop: RebaseStop) -> bool:
        nonlocal resolver_invoked
        resolver_invoked = True
        return True

    received_resolver: RebaseStopResolver | None = None

    def _reconcile_target_mock(
        _repo_root: Path,
        _target: str,
        _remote: str,
        *,
        reclaim_target_worktree: bool = True,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        conflict_resolution_config: UnifiedConfig | None = None,
    ) -> ReconciliationOutcome:
        nonlocal received_resolver
        received_resolver = rebase_stop_resolver
        if rebase_stop_resolver is not None:
            # Simulate resolver being called during conflicted rebase
            fake_stop = RebaseStop(
                sha="dummy",
                subject="dummy",
                conflicted_files=(),
                stop_index=1,
                stop_cap=1,
            )
            rebase_stop_resolver(_repo_root, _target, fake_stop)
        return ReconciliationOutcome(
            reconciled=True,
            reason="reconciled successfully",
            cleanly_aborted=False,
        )

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_reconcile.reconcile_target_onto_remote",
        _reconcile_target_mock,
    )

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
        )
    )

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=client,
        rebase_stop_resolver=_recording_resolver,
    )
    assert received_resolver is not None
    assert resolver_invoked is True
    assert client.pushed == [("tacos", "release")]
    assert state.last_push_status == "pushed"
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_concurrent_remote_advance_without_resolver_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9: without a resolver, conflicting divergence cleanly aborts without push."""
    client = _RecordingRemoteClient(
        remote_sha="diverged-remote-sha",
        refresh_status=REFRESH_DIVERGED,
    )

    def _is_ancestor(_root: Path, _ancestor: str, _descendant: str) -> bool:
        return False

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)
    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)

    def _reconcile_conflicted_mock(
        _repo_root: Path,
        _target: str,
        _remote: str,
        *,
        reclaim_target_worktree: bool = True,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        conflict_resolution_config: UnifiedConfig | None = None,
    ) -> ReconciliationOutcome:
        return ReconciliationOutcome(
            reconciled=False,
            reason="cleanly aborted conflict: conflicted",
            cleanly_aborted=True,
        )

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_reconcile.reconcile_target_onto_remote",
        _reconcile_conflicted_mock,
    )

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
        )
    )

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=client,
        rebase_stop_resolver=None,
    )
    assert client.pushed == []
    assert state.last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
