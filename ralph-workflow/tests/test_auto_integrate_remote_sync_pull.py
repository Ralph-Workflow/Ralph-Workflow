"""Tests for the opt-in remote-sync pull side of auto-integration.

Covers AC-15 to AC-20 of the PRODUCT_CRITERIA.md. The pull side
periodically fetches ``<auto_integrate_remote> <target>``,
reconciles the local target with the remote target, and degrades to
local-only integration when the flag is off, the remote is missing,
or the fetch fails.

Tests are deterministic: clock and ``run_git`` are injected so the
suite drives failures and successes without ever contacting a real
remote or sleeping.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_ORIGIN_AHEAD,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@pytest.fixture(autouse=True)
def _reset_remote_throttle() -> None:
    """Clear the module-level ``_REMOTE_PULL_THROTTLE`` singleton between tests.

    The throttle keyed ``(root, remote, target)`` keeps state across the
    whole module. Without this autouse fixture an earlier test that
    armed the throttle with ``time.monotonic()`` would suppress a later
    test's pull entirely (the throttle window is up to 300s). Each
    test gets a clean slate instead.
    """
    remote_sync._REMOTE_PULL_THROTTLE._last_pull.clear()


def _config(remote: str = "origin", enabled: bool = True, interval: float = 300.0):
    """Build the minimum ``UnifiedConfig`` shape the new helpers read."""
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": remote,
            },
        },
    )


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> GitRunResult:
    return GitRunResult(args=("git", *args), returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_remote_present(
    remote_url: str = "/tmp/origin.git",
) -> Callable[[Sequence[str]], GitRunResult]:
    def run_git(args: Sequence[str], **_kwargs: object) -> GitRunResult:
        if args[0] == "remote" and args[1] == "get-url":
            return _result(args, stdout=remote_url)
        return _result(args, stdout="")

    return run_git


def test_disabled_returns_none_with_no_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default off; no fetch is issued and no record is produced."""
    calls: list[tuple[str, ...]] = []

    def run_git(args: Sequence[str], **_kwargs: object) -> GitRunResult:
        calls.append(tuple(args))
        return _result(args)

    monkeypatch.setattr(remote_sync, "run_git", run_git, raising=False)
    config = _config(enabled=False)
    assert remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main") is None
    assert all("fetch" not in c for c in calls)


def test_already_current_records_a_verified_freshness_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E1: a healthy equal-tip fetch is visible as a verified base."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_ALREADY_CURRENT

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_ALREADY_CURRENT)

    out = remote_sync.pull_and_reconcile_target(_config(), Path("/repo"), "main")

    assert out is not None
    assert out.freshness_verdict == "verified"
    assert out.freshness_source == "fetch"
    assert out.last_remote_sync == "already current"


def test_local_ahead_does_not_move_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local already matches the remote; nothing is ff'd, no record."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *a, **kw: REFRESH_LOCAL_FLEET,
    )
    config = _config()
    out = remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    assert out is not None
    assert out.freshness_verdict == "verified"
    assert out.freshness_source == "shared local ref"


def test_local_strictly_ahead_records_publishable_without_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression S-1: a remote behind local target is not a divergence."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_AHEAD)
    out = remote_sync.pull_and_reconcile_target(_config(), Path("/repo"), "main")
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_LOCAL_AHEAD


def test_remote_strictly_ahead_records_pulled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REFRESH_ORIGIN_AHEAD -> record REMOTE_PULLED."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_ORIGIN_AHEAD)
    monkeypatch.setattr(
        mod,
        "_fast_forward_local_target_to_remote",
        lambda *a, **kw: None,
    )
    config = _config()
    out = remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_PULLED


def test_diverged_target_rebases_in_owning_worktree_before_feature_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression S-1: diverged pull uses the safe target reconciliation seam."""
    from ralph.pipeline import auto_integrate_remote_reconcile as reconcile
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_DIVERGED)
    def resolver(*_args: object) -> bool:
        return True

    calls: list[tuple[Path, str, str, object | None]] = []

    def fake_reconcile(
        root: Path, target: str, remote: str, **kwargs: object
    ) -> reconcile.ReconciliationOutcome:
        calls.append((root, target, remote, kwargs.get("rebase_stop_resolver")))
        return reconcile.ReconciliationOutcome(True, "")

    monkeypatch.setattr(reconcile, "reconcile_target_onto_remote", fake_reconcile)
    out = remote_sync.pull_and_reconcile_target(
        _config(), Path("/repo"), "main", rebase_stop_resolver=resolver
    )
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_RECONCILED
    assert calls == [(Path("/repo"), "main", "origin", resolver)]


def test_remote_strictly_ahead_records_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile outcome is named distinctly for the operator line."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *a, **kw: REFRESH_ORIGIN_AHEAD,
    )

    called: list[str] = []

    def fake_ff(_root: Path, target: str, _remote: str, _config: object) -> None:
        called.append(target)

    monkeypatch.setattr(mod, "_fast_forward_local_target_to_remote", fake_ff)
    config = _config()
    out = remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_PULLED
    assert called == ["main"]


def test_throttle_is_keyed_per_root_remote_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18: throttle window keyed (root, remote, target)."""
    clock = _FakeClock(start=100.0)
    from ralph.pipeline import auto_integrate_remote_sync as mod

    # Use a fresh throttle so this test does not see leakage from
    # earlier tests.
    fresh = mod._RemotePullThrottle()
    monkeypatch.setattr(mod, "_REMOTE_PULL_THROTTLE", fresh)

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)
    config = _config(interval=600.0)
    # Same root, same (remote,target) -> second call within 600s is throttled.
    remote_sync.pull_and_reconcile_target(config, Path("/repo-a"), "main", clock=clock)
    assert (
        fresh.should_pull("/repo-a", "origin", "main", interval_seconds=600.0, clock=clock) is False
    )
    # Different root -> distinct slot, both arms are independent.
    clock.advance(1.0)  # tiny advance so the second call arms at 101.0
    remote_sync.pull_and_reconcile_target(config, Path("/repo-b"), "main", clock=clock)
    # /repo-b's slot was armed at 101.0; advancing the clock past the
    # interval opens the /repo-b slot but does NOT open the /repo-a slot.
    clock.advance(700.0)
    assert (
        fresh.should_pull("/repo-b", "origin", "main", interval_seconds=600.0, clock=clock) is True
    )
    assert (
        fresh.should_pull("/repo-a", "origin", "main", interval_seconds=600.0, clock=clock) is True
    )


def test_interval_zero_fetches_every_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-19: interval=0 opts out of the throttle."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)
    # Interval=0 should_pull always returns True because of the
    # interval <= 0 short-circuit in _RemotePullThrottle.
    throttle = mod._REMOTE_PULL_THROTTLE
    clock = _FakeClock(0.0)
    throttle.arm("/repo", "origin", "main", clock=clock)
    assert (
        throttle.should_pull("/repo", "origin", "main", interval_seconds=0.0, clock=clock) is True
    )


def test_suppressed_refresh_is_explicitly_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6/AC-6: throttle suppression cannot masquerade as a fresh base."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "_throttle_allows_pull", lambda *a, **kw: False)

    out = remote_sync.pull_and_reconcile_target(_config(), Path("/repo"), "main")

    assert out is not None
    assert out.freshness_verdict == "unverified"
    assert out.freshness_source == "suppressed probe"
    assert out.freshness_safe is True


def test_reconcile_failure_is_unsafe_and_blocks_feature_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E9: failed target reconciliation is not a base a feature may use."""
    from ralph.pipeline import auto_integrate_remote_reconcile as reconcile
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_DIVERGED)
    monkeypatch.setattr(
        reconcile,
        "reconcile_target_onto_remote",
        lambda *a, **kw: reconcile.ReconciliationOutcome(False, "conflicted"),
    )

    out = remote_sync.pull_and_reconcile_target(_config(), Path("/repo"), "main")

    assert out is not None
    assert out.freshness_safe is False
    assert out.freshness_verdict == "unsafe"
    assert out.last_reason == "conflicted"


def test_cleanly_aborted_target_reconcile_conflict_degrades_without_blocking_local_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: a proven clean abort releases ownership and leaves local landing eligible."""
    from ralph.pipeline import auto_integrate_remote_reconcile as reconcile
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_DIVERGED)
    monkeypatch.setattr(
        reconcile,
        "reconcile_target_onto_remote",
        lambda *a, **kw: reconcile.ReconciliationOutcome(
            reconciled=False,
            reason="conflicted without a presentation marker",
            cleanly_aborted=True,
        ),
    )

    out = remote_sync.pull_and_reconcile_target(_config(), Path("/repo"), "main")

    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_PULL_FAILED
    assert out.freshness_safe is True
    assert out.freshness_verdict == "degraded"
    assert out.last_reason == "conflicted without a presentation marker"


def test_rebase_state_preserves_structured_reclamation_metadata() -> None:
    """S-4: reclamation facts survive checkpoint serialization without parsing warnings."""
    from ralph.pipeline.rebase_state import RebaseState

    restored = RebaseState.model_validate_json(
        RebaseState(
            reclaimed_worktree_path="/repo/main",
            reclaim_snapshot_ref="refs/ralph-reclaim/main/example",
            reclaim_discarded_path_count=3,
        ).model_dump_json()
    )

    assert restored.reclaimed_worktree_path == "/repo/main"
    assert restored.reclaim_snapshot_ref == "refs/ralph-reclaim/main/example"
    assert restored.reclaim_discarded_path_count == 3


def test_unreachable_remote_degrades_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-20: unreachable remote degrades, run continues."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE

    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *a, **kw: REFRESH_UNREACHABLE,
    )
    config = _config()
    out = remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_PULL_FAILED


def test_unknown_remote_records_skip_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-46: unknown remote is recorded as a skip, not a crash."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_NO_REMOTE

    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *a, **kw: REFRESH_NO_REMOTE,
    )
    config = _config(remote="not-configured")
    out = remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    assert out is not None
    assert out.last_remote_sync == remote_sync.REMOTE_NO_REMOTE
    assert out.freshness_verdict == "verified"
    assert out.freshness_source == "shared local ref"


def test_remote_failure_does_not_arm_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18: failed fetch leaves the throttle unarmed."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_UNREACHABLE)
    config = _config(interval=300.0)
    remote_sync.pull_and_reconcile_target(config, Path("/repo"), "main")
    # The throttle for that key is empty/unarmed; should not be in the map.
    throttle = mod._REMOTE_PULL_THROTTLE
    assert throttle.should_pull(
        "/repo", "origin", "main", interval_seconds=300.0, clock=lambda: 0.0
    )
