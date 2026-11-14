from __future__ import annotations

from pathlib import Path

from ralph.git import remote_push
from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_LOCAL_AHEAD,
    REMOTE_NO_REMOTE,
    REMOTE_PULL_FAILED,
    REMOTE_PULLED,
    REMOTE_RECONCILED,
    pull_and_reconcile_target,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_REMOTE,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
)


def _config(*, enabled: bool = True, interval: float = 0.0):
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": "origin",
                "auto_integrate_remote_interval_seconds": interval,
            }
        }
    )


class _RemoteClient:
    def __init__(self, refresh: str) -> None:
        self.refresh = refresh
        self.refreshes: list[tuple[Path, str, str]] = []

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str | None = None,
    ) -> str:
        del timeout_seconds
        self.refreshes.append((repo_root, target, remote or ""))
        return self.refresh

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> remote_push.PushResult:
        del repo_root, timeout_seconds
        return remote_push.PushResult(
            remote_push.PushStatus.PUSHED,
            remote,
            branch,
            "pushed",
        )

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        del repo_root, target, remote
        return None


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_disabled_remote_sync_performs_no_refresh(tmp_path: Path) -> None:
    client = _RemoteClient(REFRESH_ALREADY_CURRENT)

    outcome = pull_and_reconcile_target(
        _config(enabled=False),
        tmp_path,
        "main",
        remote_client=client,
    )

    assert outcome is None
    assert client.refreshes == []


def test_already_current_records_verified_fetch_freshness(tmp_path: Path) -> None:
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_ALREADY_CURRENT),
    )

    assert outcome is not None
    assert outcome.freshness_verdict == "verified"
    assert outcome.freshness_source == "fetch"
    assert outcome.last_remote_sync == "already current"


def test_shared_local_fleet_is_a_verified_observation(tmp_path: Path) -> None:
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_LOCAL_FLEET),
    )

    assert outcome is not None
    assert outcome.freshness_verdict == "verified"
    assert outcome.freshness_source == "shared local ref"


def test_local_ahead_is_recorded_without_target_reconciliation(tmp_path: Path) -> None:
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_LOCAL_AHEAD),
    )

    assert outcome is not None
    assert outcome.last_remote_sync == REMOTE_LOCAL_AHEAD


def test_remote_ahead_fast_forwards_through_the_public_git_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    from ralph.pipeline import auto_integrate_remote_client

    calls: list[tuple[Path, str, str]] = []

    def fast_forward(
        root: Path,
        target: str,
        remote: str,
        *,
        reclaim_target_worktree: bool,
        remote_sha: str | None = None,
    ) -> tuple[bool, str]:
        del reclaim_target_worktree, remote_sha
        calls.append((root, target, remote))
        return True, ""

    monkeypatch.setattr(auto_integrate_remote_client, "fast_forward_target_to_remote", fast_forward)
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_ORIGIN_AHEAD),
    )

    assert outcome is not None
    assert outcome.last_remote_sync == REMOTE_PULLED
    assert calls == [(tmp_path, "main", "origin")]


def test_diverged_target_uses_public_reconciliation_and_threads_the_resolver(
    monkeypatch, tmp_path: Path
) -> None:
    def resolver(*_args: object) -> bool:
        return True

    calls: list[tuple[Path, str, str, object | None]] = []

    def reconcile(
        root: Path,
        target: str,
        remote: str,
        **kwargs: object,
    ) -> remote_reconcile.ReconciliationOutcome:
        calls.append((root, target, remote, kwargs.get("rebase_stop_resolver")))
        return remote_reconcile.ReconciliationOutcome(True, "")

    monkeypatch.setattr(remote_reconcile, "reconcile_target_onto_remote", reconcile)
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_DIVERGED),
        rebase_stop_resolver=resolver,
    )

    assert outcome is not None
    assert outcome.last_remote_sync == REMOTE_RECONCILED
    assert calls == [(tmp_path, "main", "origin", resolver)]


def test_throttle_is_observable_per_root_remote_and_target(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    client = _RemoteClient(REFRESH_LOCAL_FLEET)
    config = _config(interval=600.0)

    first = pull_and_reconcile_target(config, first_root, "main", clock=clock, remote_client=client)
    suppressed = pull_and_reconcile_target(
        config, first_root, "main", clock=clock, remote_client=client
    )
    second = pull_and_reconcile_target(
        config, second_root, "main", clock=clock, remote_client=client
    )

    assert first is not None and first.freshness_verdict == "verified"
    assert suppressed is not None and suppressed.freshness_verdict == "unverified"
    assert suppressed.freshness_source == "suppressed probe"
    assert second is not None and second.freshness_verdict == "verified"
    assert client.refreshes == [
        (first_root, "main", "origin"),
        (second_root, "main", "origin"),
    ]


def test_throttle_reopens_after_the_configured_interval(tmp_path: Path) -> None:
    clock = _Clock(0.0)
    client = _RemoteClient(REFRESH_LOCAL_FLEET)
    config = _config(interval=2.0)

    pull_and_reconcile_target(config, tmp_path, "main", clock=clock, remote_client=client)
    clock.advance(2.0)
    outcome = pull_and_reconcile_target(config, tmp_path, "main", clock=clock, remote_client=client)

    assert outcome is not None
    assert outcome.freshness_verdict == "verified"
    assert len(client.refreshes) == 2


def test_reconciliation_failure_is_unsafe_and_blocks_feature_integration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_args, **_kwargs: remote_reconcile.ReconciliationOutcome(False, "conflicted"),
    )

    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_DIVERGED),
    )

    assert outcome is not None
    assert outcome.freshness_safe is False
    assert outcome.freshness_verdict == "unsafe"
    assert outcome.last_reason == "conflicted"


def test_clean_abort_degrades_without_blocking_local_integration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_args, **_kwargs: remote_reconcile.ReconciliationOutcome(
            False,
            "conflicted without a presentation marker",
            cleanly_aborted=True,
        ),
    )

    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_DIVERGED),
    )

    assert outcome is not None
    assert outcome.last_remote_sync == REMOTE_PULL_FAILED
    assert outcome.freshness_safe is True
    assert outcome.freshness_verdict == "degraded"


def test_unreachable_remote_degrades_and_does_not_arm_throttle(tmp_path: Path) -> None:
    clock = _Clock(0.0)
    client = _RemoteClient(REFRESH_UNREACHABLE)
    config = _config(interval=300.0)

    first = pull_and_reconcile_target(config, tmp_path, "main", clock=clock, remote_client=client)
    second = pull_and_reconcile_target(config, tmp_path, "main", clock=clock, remote_client=client)

    assert first is not None and first.last_remote_sync == REMOTE_PULL_FAILED
    assert second is not None and second.last_remote_sync == REMOTE_PULL_FAILED
    assert len(client.refreshes) == 2


def test_missing_remote_records_a_verified_local_ref_skip(tmp_path: Path) -> None:
    outcome = pull_and_reconcile_target(
        _config(),
        tmp_path,
        "main",
        remote_client=_RemoteClient(REFRESH_NO_REMOTE),
    )

    assert outcome is not None
    assert outcome.last_remote_sync == REMOTE_NO_REMOTE
    assert outcome.freshness_verdict == "verified"
    assert outcome.freshness_source == "shared local ref"
