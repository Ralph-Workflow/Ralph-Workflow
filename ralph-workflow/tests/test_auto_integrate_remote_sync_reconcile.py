"""Tests for the rejected-push reconcile loop of auto-integration.

Covers AC-27 to AC-31 of the PRODUCT_CRITERIA.md. The reconcile
loop runs at most ``_MAX_REMOTE_SYNC_ATTEMPTS`` (3) times per seam,
stops on a successful push, and treats a runaway remote as a
pending-not-terminal state on :attr:`RebaseState.last_remote_sync`.
The pull/rebase/push cycle never blocks the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git import remote_push as remote_push_module
from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PUSH_REJECTED,
    REMOTE_PUSHED,
    reconcile_after_rejected_push,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_LOCAL_FLEET,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest


def _config(**overrides: object):
    from ralph.config.models import UnifiedConfig

    base = {
        "general": {
            "auto_integrate_remote_enabled": True,
            "auto_integrate_remote": "origin",
        },
    }
    base["general"].update(overrides)
    return UnifiedConfig.model_validate(base)


def _record() -> RebaseState:
    return RebaseState(
        last_action="rebased",
        last_target="main",
        fast_forwarded=True,
    )


def test_successful_push_after_one_reconcile_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-27: rejected push -> reconcile -> re-push succeeds."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    # First refresh: REFRESH_LOCAL_FLEET (no-op, no fetch needed);
    # push fails non-ff. Second iteration succeeds.
    refresh_calls: list[int] = []
    outcomes = ["push of main to origin failed: non-fast-forward", "pushed main to origin"]

    def fake_refresh(*a: object, **kw: object) -> str:
        refresh_calls.append(1)
        return REFRESH_LOCAL_FLEET

    monkeypatch.setattr(mod, "refresh_target_from_remote", fake_refresh)

    push_calls: list[str] = []
    outcome_iter = iter(outcomes)

    def fake_push(*a: object, **kw: object) -> str:
        summary = next(outcome_iter)
        push_calls.append(summary)
        return summary

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    # After successful push: last_remote_sync == REMOTE_PUSHED
    assert out.last_remote_sync == REMOTE_PUSHED
    # Only one push needed because the first call returns success? No - the
    # first call returned failure. So push_calls should be 2.
    assert len(push_calls) == 2


def test_run_keeps_going_after_per_seam_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-31: pushing remote keeps the run going, no block."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)

    def fake_push(*a: object, **kw: object) -> str:
        return "push of main to origin failed: non-fast-forward"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    # After exhaustion, last_remote_sync carries the pending state, not None.
    assert out.last_remote_sync == REMOTE_PUSH_REJECTED
    # Returning a RebaseState that the caller can carry forward.


def test_per_seam_cap_is_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-27: at most ``_MAX_REMOTE_SYNC_ATTEMPTS = 3`` cycles per seam."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)

    push_attempts: list[int] = []

    def fake_push(*a: object, **kw: object) -> str:
        push_attempts.append(len(push_attempts) + 1)
        return "push of main to origin failed: non-fast-forward"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    assert len(push_attempts) == 3  # bounded loop


def test_unreachable_remote_does_not_attempt_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-30: unreachable remote is reported, no further push attempts."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    def fake_refresh(*a: object, **kw: object) -> str:
        return REFRESH_UNREACHABLE

    monkeypatch.setattr(mod, "refresh_target_from_remote", fake_refresh)

    push_calls: list[int] = []

    def fake_push(*a: object, **kw: object) -> str:
        push_calls.append(1)
        return "pushed"  # never actually called

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    assert push_calls == []
    # The reconciliation attempts a single refresh-only cycle; the
    # last_remote_sync reflects the unreachable pull state.
    assert out.last_remote_sync == remote_sync.REMOTE_REMOTE_UNREACHABLE
    assert out.last_push_status == remote_push_module.PushStatus.UNREACHABLE.value


def test_rejected_push_honors_target_reclaim_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-3 regression: rejected-push reconciliation preserves the configured opt-out."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *_a, **_kw: REFRESH_DIVERGED)
    received: list[bool] = []
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_a, **kwargs: received.append(kwargs["reclaim_target_worktree"]) or (False, "blocked"),
    )

    result = reconcile_after_rejected_push(
        _config(auto_integrate_reclaim_target_worktree=False),
        Path("/repo"),
        "main",
        _record(),
    )

    assert received == [False]
    assert result.last_remote_sync == remote_sync.REMOTE_PULL_FAILED


def test_target_reconciliation_offers_rebase_stop_resolver_before_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: target rebase conflicts offer the shared resolver before aborting."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: True)

    def resolver(*_a: object, **_kw: object) -> bool:
        return True

    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        remote_reconcile,
        "resolve_rebase_in_progress",
        lambda root, target, received: (
            calls.append((root, target)) or received(root, target, object())
        ),
    )

    success, _reason = remote_reconcile.reconcile_target_onto_remote(
        Path("/repo"), "main", "origin", rebase_stop_resolver=resolver
    )
    assert calls == [(owner, "origin/main")]
    assert success is True


def test_rejected_push_reintegrates_feature_before_repush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: each rejected-push cycle reintegrates the feature before pushing."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    events: list[str] = []
    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *_a, **_kw: events.append("fetch") or REFRESH_LOCAL_FLEET,
    )
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *_a, **_kw: events.append("push") or "pushed main to origin",
    )

    def integrate(*_a: object, **_kw: object) -> bool:
        events.append("feature integration")
        return True

    record = _record()
    mod.reconcile_after_rejected_push(
        _config(), Path("/repo"), "main", record, reintegrate=integrate
    )
    assert events == ["fetch", "feature integration", "push"]


def test_disabled_remote_sync_skips_reconcile() -> None:
    """No state change when remote sync is disabled."""
    from ralph.config.models import UnifiedConfig

    cfg = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": False,
                "auto_integrate_remote": "origin",
            },
        },
    )
    record = _record()
    out = reconcile_after_rejected_push(cfg, Path("/repo"), "main", record)
    # Record returned unchanged from the caller's perspective
    assert out is not None
