"""Configured non-main auto-integration target contracts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.git import remote_push as _remote_push_module
from ralph.git.rebase.rebase import RebaseSuccess
from ralph.pipeline import auto_integrate, auto_integrate_catchup
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_ff import RemoteClient
from ralph.pipeline.auto_integrate_rebase_merge import RebaseRunResult
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.auto_integrate_remote_reconcile import ReconciliationOutcome
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver

if TYPE_CHECKING:
    import pytest


def _config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


class _RecordingRemoteClient(RemoteClient):
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
        self.refresh_calls: list[tuple[str, str, float]] = []

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
        self.refresh_calls.append((remote, target, timeout_seconds))
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


def test_configured_non_main_local_target_is_honored_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured local target is authoritative without fallback detection."""

    def _fake_observe_branch_sha(_root: Path, _target: str) -> tuple[str, bool]:
        return ("target-sha", True)

    monkeypatch.setattr(
        auto_integrate_catchup,
        "observe_branch_sha",
        _fake_observe_branch_sha,
    )
    for target in ("develop", "unstable", "operator-named-integration"):
        assert auto_integrate.resolve_integration_target(_config(target), Path("/repo")) == target


def test_post_landing_seam_forwards_rebase_stop_resolver_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5, AC9: post-landing seam forwards rebase_stop_resolver to reconciler and converges.

    Configured target is 'release' and remote is 'tacos'. When the feature lands
    locally and remote has diverged, the post-landing seam in _integrate_once forwards
    rebase_stop_resolver to reconcile_remote_publication. The resolver resolves the
    divergence conflict, the target is pushed to tacos/release, and no origin/main
    is touched.
    """
    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )

    received_resolver: RebaseStopResolver | None = None
    resolver_invoked = False

    def my_resolver(_root: Path, _target: str, _stop: RebaseStop) -> bool:
        nonlocal resolver_invoked
        resolver_invoked = True
        return True

    client = _RecordingRemoteClient(
        remote_sha="diverged-tacos-sha",
        refresh_status=REFRESH_DIVERGED,
    )

    def _mock_reconcile_target(
        _root: Path,
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
            stop = RebaseStop(
                sha="conflict-sha",
                subject="conflict",
                conflicted_files=("file.txt",),
                stop_index=1,
                stop_cap=5,
            )
            rebase_stop_resolver(_root, _target, stop)
        return ReconciliationOutcome(reconciled=True, reason="reconciled cleanly")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_reconcile.reconcile_target_onto_remote",
        _mock_reconcile_target,
    )

    def _fake_head_sha(_r: Path) -> str:
        return "feature-sha"

    def _fake_branch_sha(_r: Path, _t: str) -> str:
        return "local-release-sha"

    def _fake_is_ancestor(_r: Path, _a: str, _d: str) -> bool:
        return False

    def _fake_read_record(_r: Path) -> None:
        return None

    def _fake_read_record_outcome(_r: Path) -> RecordReadOutcome:
        return RecordReadOutcome(disposition=RecordReadOutcomeDisposition.ABSENT)

    def _fake_write_record(_r: Path, _rec: object) -> None:
        return None

    def _fake_create_rebase_backup_ref(_r: Path, _t: str, _s: str) -> str:
        return "backup-ref"

    def _fake_delete_rebase_backup_ref(_r: Path, _b: str) -> None:
        return None

    def _fake_clear_record(_r: Path) -> None:
        return None

    def _fake_post_attempt_verify(*_args: object, **_kwargs: object) -> None:
        return None

    def _fake_run_rebase(*_args: object, **_kwargs: object) -> RebaseRunResult:
        return RebaseRunResult(RebaseSuccess(), False, None, None)

    def _fake_read_post_head(_r: Path, _t: str) -> str:
        return "feature-sha"

    def _fake_fast_forward(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        return (True, "")

    monkeypatch.setattr(remote_sync, "_DEFAULT_REMOTE_CLIENT", client)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.get_head_sha", _fake_head_sha)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.branch_sha", _fake_branch_sha)
    monkeypatch.setattr(remote_sync, "branch_sha", _fake_branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _fake_is_ancestor)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.read_record", _fake_read_record)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record_outcome",
        _fake_read_record_outcome,
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate._write_record", _fake_write_record)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._create_rebase_backup_ref", _fake_create_rebase_backup_ref
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._delete_rebase_backup_ref", _fake_delete_rebase_backup_ref
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate._clear_record", _fake_clear_record)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.post_attempt_verify", _fake_post_attempt_verify
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate._run_rebase_or_merge", _fake_run_rebase)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha", _fake_read_post_head
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate._fast_forward_target", _fake_fast_forward)

    state, retry = auto_integrate._integrate_once(
        config,
        tmp_path,
        "release",
        conflict_resolver=None,
        rebase_stop_resolver=my_resolver,
        publish=True,
    )

    assert state is not None
    assert retry is False
    # Verify the resolver was received through the post-landing seam
    assert received_resolver is my_resolver
    assert resolver_invoked is True
    # Verify push to configured remote and target
    assert client.pushed == [("tacos", "release")]
    assert state.last_remote == "tacos"
    assert state.last_target == "release"
    assert state.last_push_status == "pushed"
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    # Verify no hardcoded origin or main
    assert "origin" not in str(client.pushed)
    assert "origin" not in str(client.observed)
    assert "main" not in str(client.pushed)
    assert "main" not in str(client.observed)


def test_landing_while_fetch_throttle_suppresses_probe_publishes_to_configured_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6, AC5: probe suppressed by throttle during landing still converges configured names.

    When fetch throttle suppresses probes (refresh returns REFRESH_SUPPRESSED),
    reconcile_remote_publication still observes remote-tracking ref and pushes
    when local is ahead. Operates on tacos/release without reading origin/main.
    """
    client = _RecordingRemoteClient(
        remote_sha="remote-tacos-sha-1",
        refresh_status=REFRESH_SUPPRESSED,
    )

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-release-sha-2"

    def _is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == "remote-tacos-sha-1" and descendant == "local-release-sha-2"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _is_ancestor)

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=client,
    )

    assert client.pushed == [("tacos", "release")]
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert state.last_push_status == "pushed"
    assert state.last_remote == "tacos"
    assert state.last_target == "release"
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_unreachable_then_recovering_remote_publishes_to_configured_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8, AC5: remote unreachable at landing time is retried and pushes on recovery.

    First call finds tacos remote unreachable; returns scheduled retry state.
    Second call finds tacos reachable; pushes release to tacos/release and converges.
    """

    def _branch_sha(_root: Path, _target: str) -> str:
        return "local-release-sha"

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )

    # 1. Unreachable remote
    unreachable_client = _RecordingRemoteClient(
        remote_sha=None,
        refresh_status=REFRESH_UNREACHABLE,
    )
    state1 = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=unreachable_client,
    )
    assert unreachable_client.pushed == []
    assert state1.last_remote_sync in (
        remote_sync.REMOTE_PULL_FAILED,
        remote_sync.REMOTE_REMOTE_UNREACHABLE,
    )

    # 2. Remote recovers
    recovering_client = _RecordingRemoteClient(
        remote_sha="old-remote-sha",
        refresh_status="refreshed",
    )

    def _fake_is_ancestor_recovery(_root: Path, anc: str, desc: str) -> bool:
        return anc == "old-remote-sha" and desc == "local-release-sha"

    monkeypatch.setattr(
        remote_sync,
        "is_ancestor",
        _fake_is_ancestor_recovery,
    )
    state2 = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=recovering_client,
    )
    assert recovering_client.pushed == [("tacos", "release")]
    assert state2.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert state2.last_push_status == "pushed"
    assert "origin" not in str(recovering_client.pushed)
    assert "main" not in str(recovering_client.pushed)


def test_concurrent_remote_advance_with_configured_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC9, AC5: concurrent remote advance under release/tacos catches up / reintegrates and pushes."""
    current_local_sha = "local-release-sha"

    def _branch_sha(_root: Path, _target: str) -> str:
        return current_local_sha

    def _fake_is_ancestor_advance(_r: Path, _a: str, _d: str) -> bool:
        return False

    monkeypatch.setattr(remote_sync, "branch_sha", _branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", _fake_is_ancestor_advance)

    client = _RecordingRemoteClient(
        remote_sha="remote-advance-sha",
        refresh_status=REFRESH_DIVERGED,
    )

    def _mock_reconcile(
        _repo_root: Path,
        _target: str,
        _remote: str,
        *,
        reclaim_target_worktree: bool = True,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        conflict_resolution_config: UnifiedConfig | None = None,
    ) -> ReconciliationOutcome:
        nonlocal current_local_sha
        current_local_sha = "reconciled-release-sha"
        return ReconciliationOutcome(reconciled=True, reason="ok")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_reconcile.reconcile_target_onto_remote",
        _mock_reconcile,
    )

    config = UnifiedConfig(
        general=GeneralConfig(
            auto_integrate_enabled=True,
            auto_integrate_target="release",
            auto_integrate_remote="tacos",
            auto_integrate_remote_enabled=True,
        )
    )

    state = remote_sync.reconcile_remote_publication(
        config,
        tmp_path,
        "release",
        remote="tacos",
        remote_client=client,
    )

    assert client.pushed == [("tacos", "release")]
    assert state.last_push_status == "pushed"
    assert state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert "origin" not in str(client.pushed)
    assert "main" not in str(client.pushed)


def test_unreachable_remote_message_uses_configured_remote_name() -> None:
    """AC5: the unreachable-remote message must use the configured remote name.

    The previous implementation hard-coded the ``origin unreachable``
    suffix regardless of which remote was actually configured. With a
    configured ``tacos`` remote, the user-facing summary now reads
    ``push of release to tacos failed: tacos remote unreachable``
    (configured-name-agnostic wording, no literal ``origin``).
    """
    from ralph.git.remote_push import PushStatus
    from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE

    # 1. Configured ``tacos`` remote produces the configured-name-agnostic
    # wording; the literal ``origin`` suffix no longer appears in the summary.
    unreachable_client = _RecordingRemoteClient(
        remote_sha=None,
        refresh_status=REFRESH_UNREACHABLE,
    )
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        tmp_path,
        "release",
        remote="tacos",
        remote_client=unreachable_client,
    )
    assert unreachable_client.pushed == []
    assert state.last_push_status == PushStatus.UNREACHABLE
    assert state.last_remote_sync is not None
    assert "origin" not in state.last_remote_sync
    assert "tacos" in state.last_remote_sync
    assert "release" in state.last_remote_sync

    # 2. Sanity: when the configured remote is ``origin``, the wording
    # carries ``origin`` (not the hard-coded literal from the legacy
    # implementation).
    origin_client = _RecordingRemoteClient(
        remote_sha=None,
        refresh_status=REFRESH_UNREACHABLE,
    )
    state_origin = remote_sync.reconcile_remote_publication(
        _build_config(),
        tmp_path,
        "main",
        remote="origin",
        remote_client=origin_client,
    )
    assert origin_client.pushed == []
    assert state_origin.last_push_status == PushStatus.UNREACHABLE
    assert state_origin.last_remote_sync is not None
    assert "origin unreachable" in state_origin.last_remote_sync
    assert "main to origin" in state_origin.last_remote_sync
