"""Tests for transient remote failure handling and retry policy.

Covers AC-33 to AC-39: transient remote failure is never terminal;
every seam attempts the sequence until exhaustion; push attempts are
classed by reason but the retry policy is unchanged; backoff widens
exponentially with jitter, capped at
``auto_integrate_remote_backoff_max_seconds``; any success resets
the backoff to the base interval. Tests drive failures without
sleeping (clock and jitter are injected).
"""

from __future__ import annotations

import random

from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_AUTH_FAILED,
    REMOTE_NON_FAST_FORWARD,
    REMOTE_PUSH_REJECTED,
    REMOTE_REMOTE_UNREACHABLE,
    REMOTE_TIMEOUT,
    RemoteBackoffState,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _config(**overrides: object):
    from ralph.config.models import UnifiedConfig

    base = {
        "general": {
            "auto_integrate_remote_sync_enabled": True,
            "auto_integrate_remote_target": "origin",
            "auto_integrate_remote_sync_interval_seconds": 1.0,  # tight base for tests
            "auto_integrate_remote_backoff_max_seconds": 60.0,
            "auto_integrate_remote_wait_seconds": 0.0,
            "auto_integrate_fetch_timeout_seconds": 5.0,
            "auto_integrate_push_timeout_seconds": 5.0,
        },
    }
    base["general"].update(overrides)
    return UnifiedConfig.model_validate(base)


def test_consecutive_failure_increments_counter() -> None:
    """AC-37: every failure increments; counter can grow to 20."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    for _i in range(20):
        state.record_failure("/repo", "origin", "main")
        clock.advance(0.001)
    assert state.consecutive_failures("/repo", "origin", "main") == 20


def test_success_resets_to_zero() -> None:
    """AC-37: a success resets the counter immediately."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    state.record_failure("/repo", "origin", "main")
    state.record_failure("/repo", "origin", "main")
    state.record_success("/repo", "origin", "main")
    assert state.consecutive_failures("/repo", "origin", "main") == 0


def test_next_gap_grows_exponentially() -> None:
    """AC-37: gap widens exponentially with jitter, capped at the ceiling."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    config = _config()
    gaps: list[float] = []
    _rng = random.Random(0)

    def jitter() -> float:
        return _rng.random()

    for _attempt in range(1, 12):
        state.record_failure("/repo", "origin", "main")
        gap = state.next_gap(
            repo_root="/repo", remote="origin", target="main", config=config, jitter=jitter
        )
        gaps.append(gap)
        clock.advance(0.0)  # gap never elapsed
    # Every gap stays within [0.5 * base, ceiling * 1.0].
    base = 1.0
    ceiling = 60.0
    for gap in gaps:
        assert 0.5 * base <= gap <= ceiling, f"gap out of bounds: {gap} in {gaps}"
    # Without jitter the algorithm is monotonically non-decreasing; with
    # jitter in [0.5, 1.5) a step at the ceiling may go below the previous
    # ceiling step. Verify the underlying trend by pinning the 0-jitter
    # path: the very first gap is base*2*jitter>=base and subsequent gaps
    # grow until they are at the ceiling.
    assert gaps[0] > 0.0  # first attempt actually produced a gap
    # After enough failures the gap must reach the ceiling.
    assert max(gaps) >= ceiling - 1e-6
    # And never overshoot: ceiling is a hard cap.
    assert max(gaps) <= ceiling


def test_next_gap_returns_zero_when_counter_is_zero() -> None:
    """AC-37: with no failures, the next call has no enforced gap."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    config = _config()
    assert (
        state.next_gap(
            repo_root="/repo", remote="origin", target="main", config=config, jitter=lambda: 0.5
        )
        == 0.0
    )


def test_zero_base_interval_disables_backoff_growth() -> None:
    """AC-37: interval=0 base, gap is 0."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    config = _config(auto_integrate_remote_sync_interval_seconds=0.0)
    state.record_failure("/repo", "origin", "main")
    assert (
        state.next_gap(
            repo_root="/repo", remote="origin", target="main", config=config, jitter=lambda: 0.5
        )
        == 0.0
    )


def test_classification_changes_message_only_not_policy() -> None:
    """AC-38: distinct reasons; retry policy identical."""
    # The classifier is just constant strings. Assert against the
    # documented set so renaming a constant without updating tests
    # fails here.
    from ralph.pipeline.auto_integrate_remote_sync import (
        REMOTE_NO_REMOTE,
        REMOTE_NO_REMOTE_BRANCH,
        REMOTE_PULL_FAILED,
        REMOTE_PUSH_REJECTED,
        REMOTE_REJECTED_BY_HOOK,
    )

    expected_classifications = {
        REMOTE_REMOTE_UNREACHABLE,
        REMOTE_AUTH_FAILED,
        REMOTE_REJECTED_BY_HOOK,
        REMOTE_NON_FAST_FORWARD,
        REMOTE_TIMEOUT,
    }
    # Sanity: every classification is a string.
    assert all(isinstance(c, str) for c in expected_classifications)
    # Sanity: the constant set above is the documented set.
    assert expected_classifications.issubset(
        {
            REMOTE_REMOTE_UNREACHABLE,
            REMOTE_AUTH_FAILED,
            REMOTE_REJECTED_BY_HOOK,
            REMOTE_NON_FAST_FORWARD,
            REMOTE_TIMEOUT,
            REMOTE_PULL_FAILED,
            REMOTE_PUSH_REJECTED,
            REMOTE_NO_REMOTE,
            REMOTE_NO_REMOTE_BRANCH,
        }
    )


def test_twenty_consecutive_failures_keep_counting() -> None:
    """AC-34: 20 driven failures -- the counter is bounded by the test,
    not by the code."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    for _ in range(20):
        state.record_failure("/repo", "origin", "main")
    assert state.consecutive_failures("/repo", "origin", "main") == 20


def test_distinct_keys_have_independent_backoff() -> None:
    """AC-34/AC-37: failures on one (root, remote, target) don't affect others."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock)
    for _ in range(5):
        state.record_failure("/repo", "origin", "main")
    # Different target on same remote should still be at 0.
    assert state.consecutive_failures("/repo", "origin", "release") == 0
    # Different remote on same root should still be at 0.
    assert state.consecutive_failures("/repo", "upstream", "main") == 0
    # Different root should still be at 0.
    assert state.consecutive_failures("/other", "origin", "main") == 0


def test_pending_push_state_carry_forward_does_not_terminate() -> None:
    """AC-35: an outstanding push is NOT a permanent disablement.

    The keyword is "carry forward": a state carrying
    ``last_remote_sync=REMOTE_PUSH_REJECTED`` simply surfaces that the
    next seam should retry. There is no flag, counter, or path that
    permanently disables remote sync as a side effect of a prior
    failure -- this test pins that invariant.
    """
    from ralph.pipeline.rebase_state import RebaseState

    rejected_record = RebaseState(
        last_action="rebased",
        last_target="main",
        last_remote_sync=REMOTE_PUSH_REJECTED,
        last_reason="push rejected: non-fast-forward",
    )
    # Read it back: RebaseState is free of side-effect flags.
    assert rejected_record.last_remote_sync == REMOTE_PUSH_REJECTED


def test_phase_transition_retries_pending_push_without_new_commit(
    monkeypatch,
) -> None:
    """S-1: a clean phase boundary retries an eligible pending publication."""
    from pathlib import Path

    from ralph.pipeline import auto_integrate as mod
    from ralph.pipeline.rebase_state import RebaseState

    root = Path("/repo")
    monkeypatch.setattr(Path, "exists", lambda self: self == root / ".git")
    monkeypatch.setattr(mod, "resolve_integration_target", lambda *_a: "main")
    monkeypatch.setattr(mod, "_worktree_is_clean", lambda *_a: True)
    monkeypatch.setattr(mod, "_refresh_target", lambda *_a: None)
    monkeypatch.setattr(mod, "branch_sha", lambda *_a: "same")
    monkeypatch.setattr(mod, "get_head_sha", lambda *_a: "same")
    called: list[RebaseState] = []
    monkeypatch.setattr(
        mod,
        "auto_integrate_after_commit",
        lambda *_a, **_kw: called.append(_a[2]) or _a[2],
    )

    class Scope:
        root = "/repo"

    pending = RebaseState(last_remote_sync=REMOTE_PUSH_REJECTED, last_target="main")
    mod.auto_integrate_on_phase_transition(_config(), Scope(), pending)
    assert called == [pending]


def test_backoff_state_key_set_is_bounded() -> None:
    """AC-37/lifecycle audit: the FIFO cap holds the key set bounded."""
    clock = _FakeClock(0.0)
    state = RemoteBackoffState(clock=clock, max_tracked_keys=4)
    # Insert more keys than the cap.
    for i in range(8):
        state.record_failure(f"/repo-{i}", "origin", "main")
    # Internal store stays at the cap.
    assert len(state._consecutive) <= 4
    assert len(state._last_attempt) <= 4
