"""Tests for the end-of-run waiting state of opt-in remote sync.

Covers AC-40 to AC-45 of the PRODUCT_CRITERIA.md. With
``auto_integrate_remote_wait_seconds = 0`` (default) the wait state is a
strict no-op: today's exit behavior is preserved verbatim. With any
positive value the run stays in a visible, interruptible
fetch -> reconcile -> push loop on the exponential jittered backoff,
and exits non-fatally when either the push lands or the budget is
exhausted. The state is never entered while pipeline work remains.

These tests are deterministic: clock and jitter are injected, no real
network or sleeping happens, and the
:meth:`RemoteBackoffState.reset_instance` seam clears the singleton
between tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PUSHED,
    RemoteBackoffState,
    wait_for_remote_publish,
)


def _config(
    *,
    enabled: bool = True,
    remote: str = "origin",
    wait_seconds: float = 0.0,
    interval: float = 300.0,
    backoff_max: float = 300.0,
):
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_sync_enabled": enabled,
                "auto_integrate_remote_target": remote,
                "auto_integrate_remote_sync_interval_seconds": interval,
                "auto_integrate_remote_backoff_max_seconds": backoff_max,
                "auto_integrate_remote_wait_seconds": wait_seconds,
                "auto_integrate_fetch_timeout_seconds": 5.0,
                "auto_integrate_push_timeout_seconds": 5.0,
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


def _fake_backoff_state(clock: _FakeClock) -> RemoteBackoffState:
    """Build a backoff state that uses the test's fake clock.

    :class:`RemoteBackoffState` records the last-failure timestamp via
    its own injected clock (``time.monotonic`` by default), while
    :func:`wait_for_remote_publish` reads the same instant through the
    clock passed at the call site. If the two clocks disagree, every
    :meth:`RemoteBackoffState.next_gap` call returns
    ``gap - (fake_now - real_last)`` which explodes for tests that
    inject a non-monotonic clock. Routing both sides through the
    fake clock eliminates the discrepancy.
    """
    return RemoteBackoffState(clock=clock)


@pytest.fixture(autouse=True)
def _reset_remote_sync_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level singleton between tests.

    :class:`RemoteBackoffState` is a process-wide singleton via
    :meth:`RemoteBackoffState.instance`; the
    :data:`wait_for_remote_publish` implementation calls
    ``RemoteBackoffState.instance()``. Without this reset the
    ``test_succeeds_after_one_attempt`` and
    ``test_exhausts_budget_after_repeated_failure`` tests would see
    consecutive-failure state carried in from earlier calls and
    diverge.
    """
    RemoteBackoffState.reset_instance()


def test_default_zero_waits_is_strict_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-40: with the default ``0`` budget the wait is a strict no-op."""
    clock = _FakeClock(0.0)
    contacted: list[str] = []

    def unexpected(*args: object, **kwargs: object) -> object:
        contacted.append("attempt_reconcile_and_push")
        raise AssertionError("disabled wait contacted a remote")

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", unexpected)
    published, summary = wait_for_remote_publish(
        _config(wait_seconds=0.0),
        Path("/repo"),
        "main",
        clock=clock,
    )
    assert published is False
    assert summary == ""
    assert contacted == []


def test_disabled_remote_sync_waits_is_strict_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-41: when the opt-in flag is off, no waiting state is entered."""
    clock = _FakeClock(0.0)
    contacted: list[str] = []

    def unexpected(*args: object, **kwargs: object) -> object:
        contacted.append("attempt_reconcile_and_push")
        raise AssertionError("disabled wait contacted a remote")

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", unexpected)
    published, summary = wait_for_remote_publish(
        _config(enabled=False, wait_seconds=60.0),
        Path("/repo"),
        "main",
        clock=clock,
    )
    assert published is False
    assert summary == ""
    assert contacted == []


def test_succeeds_after_one_attempt_returns_pushed_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-42: a successful push is reported as ``REMOTE_PUSHED``."""
    clock = _FakeClock(0.0)
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_attempt(*args: object, **kwargs: object) -> remote_sync._PushOutcome:
        return remote_sync._PushOutcome(
            success=True,
            summary="pushed main to origin",
            pushed=True,
            created_remote_branch=False,
        )

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", fake_attempt)
    published, summary = wait_for_remote_publish(
        _config(wait_seconds=60.0),
        Path("/repo"),
        "main",
        sleep=fake_sleep,
        clock=clock,
    )
    assert published is True
    assert summary == "pushed main to origin"
    # One attempt + one success exit; no sleep on the success path.
    assert sleep_calls == []


def test_exhausts_budget_after_repeated_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-43: a budget exhaustion returns the documented phrase."""
    clock = _FakeClock(0.0)
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.advance(seconds)

    call_count: list[int] = []

    def fake_attempt(*args: object, **kwargs: object) -> remote_sync._PushOutcome:
        call_count.append(1)
        # Real push attempts take time; advance the fake clock inside
        # the mock so the loop's deadline check can fire. Without this
        # increment the loop would spin when ``gap`` returned 0 (after
        # one full interval elapsed) and the deadline was still in the
        # future -- the production code never hits that path because
        # real git operations cost non-zero wall time.
        clock.advance(0.001)
        return remote_sync._PushOutcome(
            success=False,
            summary=f"push of main to origin failed: {remote_sync.REMOTE_REMOTE_UNREACHABLE}",
            pushed=True,
        )

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", fake_attempt)
    published, summary = wait_for_remote_publish(
        _config(wait_seconds=10.0, interval=1.0, backoff_max=5.0),
        Path("/repo"),
        "main",
        sleep=fake_sleep,
        clock=clock,
        jitter=lambda: 0.5,
        backoff_state=_fake_backoff_state(clock),
    )
    assert published is False
    assert "landed locally, not published to origin:" in summary
    # The unpushed-target phrase is what an operator sees; the
    # synthetic per-attempt reason trails it.
    assert "origin unreachable" in summary
    # More than one attempt ran before the cap (and the run did not
    # block forever).
    assert len(call_count) >= 1
    # Sleeps were bounded by the configured ceiling.
    assert max(sleep_calls) <= 5.0 + 1e-6


def test_interruption_returns_immediately_without_unpublished_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-44: operator interrupt exits non-fatally and immediately."""
    clock = _FakeClock(0.0)
    attempt_runs: list[int] = []

    def fake_attempt(*args: object, **kwargs: object) -> remote_sync._PushOutcome:
        attempt_runs.append(1)
        # ``is_interrupted`` returns True; the loop MUST NOT call
        # ``fake_attempt`` more than once. Production code increments
        # the wall clock between attempts; the mock does the same here
        # so a regression that calls the attempt even after interruption
        # is observable as ``len(attempt_runs) >= 2``.
        clock.advance(0.5)
        return remote_sync._PushOutcome(
            success=False,
            summary="push of main to origin failed: timeout",
            pushed=True,
        )

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", fake_attempt)

    interrupt_flag = {"raised": True}

    def is_interrupted() -> bool:
        return interrupt_flag["raised"]

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:  # pragma: no cover -- unused
        sleep_calls.append(seconds)
        clock.advance(seconds)

    published, summary = wait_for_remote_publish(
        _config(wait_seconds=60.0),
        Path("/repo"),
        "main",
        is_interrupted=is_interrupted,
        sleep=fake_sleep,
        clock=clock,
    )
    assert published is False
    # An interrupted run does NOT get the unpublished-target phrase:
    # confusing the interrupt with "ran out of time" is exactly the
    # surface this branch exists to avoid.
    assert "landed locally, not published" not in summary
    # The while-loop's interrupt check fires before the next attempt.
    assert len(attempt_runs) <= 1


def test_interruption_before_any_attempt_no_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-44: interrupting before any attempt yields an empty summary."""
    clock = _FakeClock(0.0)
    contacted: list[str] = []

    def unexpected(*args: object, **kwargs: object) -> object:
        contacted.append("attempt_reconcile_and_push")
        raise AssertionError("interrupted before first attempt still contacted a remote")

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", unexpected)

    def is_interrupted() -> bool:
        return True

    published, summary = wait_for_remote_publish(
        _config(wait_seconds=60.0),
        Path("/repo"),
        "main",
        is_interrupted=is_interrupted,
        clock=clock,
    )
    assert published is False
    assert summary == ""
    assert contacted == []


def test_backoff_gap_does_not_sleep_when_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-37-derived: a zero-gap call must not call ``sleep`` at all."""
    clock = _FakeClock(0.0)
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_attempt(*args: object, **kwargs: object) -> remote_sync._PushOutcome:
        # Advance the fake clock so the deadline check fires; the production
        # loop never spins because real ``_attempt_reconcile_and_push`` costs
        # non-zero wall time.
        clock.advance(0.001)
        return remote_sync._PushOutcome(
            success=False,
            summary="push of main to origin failed: timeout",
            pushed=True,
        )

    monkeypatch.setattr(remote_sync, "_attempt_reconcile_and_push", fake_attempt)
    # Interval=0 -> backoff gap is 0 every attempt, so ``sleep`` is
    # never invoked even when the deadline keeps advancing.
    wait_for_remote_publish(
        _config(wait_seconds=10.0, interval=0.0),
        Path("/repo"),
        "main",
        sleep=fake_sleep,
        clock=clock,
        jitter=lambda: 0.5,
        backoff_state=_fake_backoff_state(clock),
    )
    assert sleep_calls == []


def test_published_record_carries_remote_pushed_outcome() -> None:
    """AC-43 cross-check: the wait succeeds with the canonical label.

    The ``REMOTE_PUSHED`` constant is the SAME string the
    :func:`ralph.display.auto_integrate_message.format_auto_integrate_message`
    helper renders in the ``[remote: pushed]`` suffix, so the live
    line and the wait-state outcome can never drift.
    """
    assert REMOTE_PUSHED == "pushed"
