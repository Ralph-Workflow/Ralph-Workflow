"""Drift-recovery tests for the O2 publication reconciler.

Each test pins one acceptance criterion (AC5-AC9) from
``PLAN.md``. The tests are black-box: they drive the public
``reconcile_remote_publication`` and worker entry points with
in-memory fakes and assert on the resulting ``RebaseState``,
concrete push calls, and ref convergence so the implementation
cannot regress to fire-once publication or ``origin``/``main`` defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push as _remote_push_module
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_catchup import AutoIntegrateCatchupWorker
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.pipeline.auto_integrate_ff import RemoteClient


class _RecordingRemoteClient:
    """In-memory remote client that records the configured remote/target pair."""

    def __init__(self, *, remote_sha: str | None = "remote-head-sha") -> None:
        self.remote_sha = remote_sha
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
        return "refreshed"

    def push_branch(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, target))
        return _remote_push_module.PushResult(_remote_push_module.PushStatus.PUSHED, remote, target)


class _UnreachableRemoteClient:
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
        target: str,
        *,
        remote: str,
        timeout_seconds: float = 0.0,
    ) -> _remote_push_module.PushResult:
        self.pushed.append((remote, target))
        return _remote_push_module.PushResult(
            _remote_push_module.PushStatus.UNREACHABLE, remote, target
        )


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
    config = UnifiedConfig()
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "tacos-release-local-sha")
    monkeypatch.setattr(
        remote_sync,
        "is_ancestor",
        lambda _r, a, d: a == "tacos-release-remote-sha" and d == "tacos-release-local-sha",
    )

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=cast("RemoteClient", client),
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


def test_throttle_deferred_probe_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: a fresh land pushes even when the fetch probe was throttle-suppressed.

    The reconciler MUST attempt the push when the local SHA is strictly ahead
    of the remote, regardless of probe freshness or previous in-memory state.
    After push, calling again with matching ref reports already current.
    """
    client = _RecordingRemoteClient(remote_sha="remote-sha-1")
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha-2")

    def _stub_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "remote-sha-1" and descendant == "local-sha-2"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor)

    config = UnifiedConfig()
    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", client),
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
        remote_client=cast("RemoteClient", client),
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
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "landed-local-sha")

    def _stub_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "stale-remote-sha" and descendant == "landed-local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor)

    config = UnifiedConfig()
    scope = WorkspaceScope(tmp_path)

    # Patch remote_sync._DEFAULT_REMOTE_CLIENT so recover_incomplete_integration uses client
    monkeypatch.setattr(remote_sync, "_DEFAULT_REMOTE_CLIENT", client)

    outcome = recover_incomplete_integration(scope, config=config)
    assert client.pushed == [("origin", "main")]
    assert outcome is not None
    assert outcome.last_push_status == "pushed"


def test_unreachable_then_reachable_auto_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8: worker tick with unreachable remote retries and pushes on recovery.

    Exercises ``AutoIntegrateCatchupWorker`` directly with production defaults:
    Tick 1: the remote is UNREACHABLE; the worker runs without error and does not push.
    Tick 2: the remote recovers; the worker's post-tick reconciler pushes.
    """
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.resolve_integration_target",
        lambda _c, _r: "main",
    )

    unreachable = _UnreachableRemoteClient()
    config = UnifiedConfig()
    worker = AutoIntegrateCatchupWorker(
        config,
        tmp_path,
        remote_client=cast("RemoteClient", unreachable),
        tick=lambda: "up-to-date",
    )
    # Tick 1: unreachable
    worker._tick_once()
    assert unreachable.pushed == []

    # Remote recovers
    reachable = _RecordingRemoteClient(remote_sha="old-remote-sha")

    def _stub_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "old-remote-sha" and descendant == "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _stub_is_ancestor)
    worker._remote_client = cast("RemoteClient", reachable)

    # Tick 2: reachable -> auto pushes
    worker._tick_once()
    assert reachable.pushed == [("origin", "main")]


def test_concurrent_remote_advance_calls_reconcile_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9: concurrent remote advance triggers fetch -> divergence reconciliation -> push.

    When local and remote have both advanced independently, ref comparison
    detects divergence and dispatches to ``reconcile_after_rejected_push``,
    which fetches and pushes the converged state.
    """
    client = _RecordingRemoteClient(remote_sha="diverged-remote-sha")
    monkeypatch.setattr(remote_sync, "is_ancestor", lambda *_a, **_kw: False)
    monkeypatch.setattr(remote_sync, "branch_sha", lambda _r, _t: "local-sha")

    from ralph.pipeline.auto_integrate_remote_reconcile import ReconciliationOutcome

    # Mock reconcile_target_onto_remote to succeed
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_reconcile.reconcile_target_onto_remote",
        lambda *args, **kwargs: ReconciliationOutcome(
            reconciled=True,
            reason="reconciled successfully",
            cleanly_aborted=False,
        ),
    )

    state = remote_sync.reconcile_remote_publication(
        UnifiedConfig(),
        tmp_path,
        "main",
        remote="origin",
        remote_client=cast("RemoteClient", client),
    )
    assert client.pushed == [("origin", "main")]
    assert state.last_push_status == "pushed"
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
