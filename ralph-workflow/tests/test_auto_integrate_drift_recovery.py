from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.git import remote_push as _remote_push_module
from ralph.git.remote_push import PushStatus
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
    def __init__(
        self,
        *,
        remote_sha: str | None = "remote-head-sha",
        refresh_status: str = "refreshed",
        branch_sha_getter: Callable[[Path, str], str] | None = None,
    ) -> None:
        self.remote_sha = remote_sha
        self.refresh_status = refresh_status
        self.branch_sha_getter = branch_sha_getter
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
        if self.branch_sha_getter is not None:
            with contextlib.suppress(Exception):
                self.remote_sha = self.branch_sha_getter(repo_root, branch)
        return _remote_push_module.PushResult(_remote_push_module.PushStatus.PUSHED, remote, branch)


class _UnreachableRemoteClient(RemoteClient):
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.observed: list[tuple[str, str, str]] = []

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        self.observed.append((remote, target, str(repo_root)))
        return None

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

    assert len(client.observed) > 0
    assert len(client.pushed) > 0

    assert all(remote == "tacos" and target == "release" for remote, target, _ in client.observed)
    assert client.pushed == [("tacos", "release")]
    assert state.last_remote == "tacos"
    assert state.last_target == "release"

    assert "origin" not in str(client.observed)
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.observed)
    assert "main" not in str(client.pushed)

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
    assert len(client.pushed) == 1


def test_throttle_deferred_probe_publishes_to_configured_release_tacos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_interval_seconds=60.0,
        )
    )
    client = _RecordingRemoteClient(remote_sha="tacos-release-remote-sha")

    remote_sync._REMOTE_PULL_THROTTLE.arm(tmp_path, "tacos", "release", clock=time.monotonic)

    pull_record = remote_sync.pull_and_reconcile_target(
        config,
        tmp_path,
        "release",
        remote_client=client,
    )
    assert pull_record is not None
    assert pull_record.last_remote_sync == "refresh suppressed"
    assert pull_record.last_refresh == "refresh suppressed by throttle"
    assert len(client.refresh_outcomes) == 0

    local_sha = "tacos-release-local-sha"
    remote_sha = "tacos-release-remote-sha"

    def _branch_sha(_root: Path, _target: str) -> str:
        return local_sha

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == remote_sha and descendant == local_sha

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        source_state=pull_record,
        remote_client=client,
    )

    assert client.pushed == [("tacos", "release")]
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_remote == "tacos"
    assert state.last_target == "release"

    client.remote_sha = local_sha
    converged_state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote_client=client,
    )
    assert converged_state.last_remote_sync == remote_sync.REMOTE_ALREADY_CURRENT
    assert converged_state.fast_forwarded is True
    assert len(client.pushed) == 1

    local_target_ref = _branch_sha(tmp_path, "release")
    remote_target_ref = client.observe_remote_sha(tmp_path, "release", remote="tacos")
    assert local_target_ref == remote_target_ref == local_sha

    assert "origin" not in str(client.observed)
    assert "origin" not in str(client.pushed)
    assert "origin" not in str(client.refresh_outcomes)
    assert "main" not in str(client.observed)
    assert "main" not in str(client.pushed)
    assert "main" not in str(client.refresh_outcomes)


def test_killed_between_landing_and_push_recovery_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _branch_sha_getter(_root: Path, _target: str) -> str:
        return "landed-local-sha"

    client = _RecordingRemoteClient(
        remote_sha="stale-remote-sha",
        branch_sha_getter=_branch_sha_getter,
    )

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

    monkeypatch.setattr(remote_sync, "_DEFAULT_REMOTE_CLIENT", client)

    outcome = recover_incomplete_integration(
        scope,
        config=config,
        rebase_stop_resolver=_dummy_resolver,
    )
    assert client.pushed == [("tacos", "release")]
    assert client.remote_sha == "landed-local-sha", (
        f"local/remote refs must converge after the cross-worktree recovery; "
        f"remote_sha={client.remote_sha!r} (expected 'landed-local-sha')"
    )
    assert outcome is not None
    assert outcome.last_push_status == "pushed"
    assert outcome.last_remote_sync == "pushed"
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_unreachable_then_reachable_auto_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-sha"

    def _resolve_target(_config: UnifiedConfig, _root: Path) -> str:
        return "release"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.resolve_integration_target",
        _resolve_target,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.is_lease_held",
        lambda _root: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.try_acquire_lease",
        lambda _root, holder=None: object(),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_catchup.end_agent_lease",
        lambda _lease: None,
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
    worker._tick_once()
    assert unreachable.pushed == []

    reachable = _RecordingRemoteClient(remote_sha="old-remote-sha")

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "old-remote-sha" and descendant == "local-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)
    worker._remote_client = reachable

    worker._tick_once()
    assert reachable.pushed == [("tacos", "release")]
    assert "origin" not in str(reachable.pushed)
    assert "main" not in str(reachable.pushed)


def test_concurrent_remote_advance_calls_reconcile_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_local_sha = "local-sha"

    def _branch_sha(_root: Path, _target: str) -> str:
        return current_local_sha

    client = _RecordingRemoteClient(
        remote_sha="diverged-remote-sha",
        refresh_status=REFRESH_DIVERGED,
        branch_sha_getter=_branch_sha,
    )

    def _is_ancestor(_root: Path, _ancestor: str, _descendant: str) -> bool:
        return False

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
        nonlocal received_resolver, current_local_sha
        received_resolver = rebase_stop_resolver
        current_local_sha = "reconciled-sha"
        if rebase_stop_resolver is not None:
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

    assert _branch_sha(tmp_path, "release") != client.observe_remote_sha(
        tmp_path, "release", remote="tacos"
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
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)

    local_target_ref = _branch_sha(tmp_path, "release")
    remote_target_ref = client.observe_remote_sha(tmp_path, "release", remote="tacos")
    assert local_target_ref == remote_target_ref == "reconciled-sha"


def test_rejected_push_fetch_reintegrate_converges_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    push_attempts = 0
    current_local_sha = "local-pre-reconcile-sha"

    class _RejectOnceRemoteClient(_RecordingRemoteClient):
        def push_branch(
            self,
            repo_root: Path,
            branch: str,
            *,
            remote: str,
            timeout_seconds: float = 0.0,
        ) -> _remote_push_module.PushResult:
            nonlocal push_attempts
            push_attempts += 1
            self.pushed.append((remote, branch))
            if push_attempts == 1:
                return _remote_push_module.PushResult(
                    _remote_push_module.PushStatus.NON_FAST_FORWARD, remote, branch
                )
            self.remote_sha = current_local_sha
            return _remote_push_module.PushResult(
                _remote_push_module.PushStatus.PUSHED, remote, branch
            )

    client = _RejectOnceRemoteClient(
        remote_sha="old-remote-sha",
        refresh_status=REFRESH_DIVERGED,
    )

    def _branch_sha(_root: Path, _target: str) -> str:
        return current_local_sha

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "old-remote-sha" and descendant == "local-pre-reconcile-sha"

    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)
    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)

    def _reconcile_target_mock(
        _repo_root: Path,
        _target: str,
        _remote: str,
        *,
        reclaim_target_worktree: bool = True,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        conflict_resolution_config: UnifiedConfig | None = None,
    ) -> ReconciliationOutcome:
        nonlocal current_local_sha
        current_local_sha = "reconciled-post-push-sha"
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
    )

    assert push_attempts == 2
    assert client.pushed == [("tacos", "release"), ("tacos", "release")]

    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED

    local_target_ref = _branch_sha(tmp_path, "release")
    remote_target_ref = client.observe_remote_sha(tmp_path, "release", remote="tacos")
    assert local_target_ref == remote_target_ref == "reconciled-post-push-sha"

    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_concurrent_remote_advance_without_resolver_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
