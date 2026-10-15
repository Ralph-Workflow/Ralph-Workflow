"""Tests verifying retry counters and cooldowns are durable, restart-surviving,
bounded, and respect clock boundaries.
"""

from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_backoff import (
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    compute_retry_deadline,
    compute_retry_delay,
)
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory


def _make_failure(
    *,
    category: FailureCategory = FailureCategory.AGENT,
    reason: str = "transient error",
    agent: str = "claude",
    phase: str = "development",
    counts: bool = True,
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        reason,
    )


class TestAutoIntegrateBackoffClockBoundaries:
    """auto_integrate_backoff respects clock boundaries and bounds."""

    def test_compute_retry_delay_bounds(self) -> None:
        assert compute_retry_delay(0) == 0.0
        d1 = compute_retry_delay(1, jitter=lambda: 1.0)
        assert d1 == RETRY_BASE_DELAY_SECONDS
        d2 = compute_retry_delay(2, jitter=lambda: 1.0)
        assert d2 == RETRY_BASE_DELAY_SECONDS * 2.0
        d_large = compute_retry_delay(10, jitter=lambda: 1.0)
        assert d_large == RETRY_MAX_DELAY_SECONDS

    def test_compute_retry_deadline_uses_injected_clock(self) -> None:
        clock = FakeClock(start=100.0)
        deadline = compute_retry_deadline(
            1,
            clock=clock.monotonic,
            jitter=lambda: 1.0,
        )
        assert deadline == 100.0 + RETRY_BASE_DELAY_SECONDS

    def test_compute_retry_deadline_clock_object(self) -> None:
        clock = FakeClock(start=50.0)
        deadline = compute_retry_deadline(
            2,
            clock=clock,
            cooldown_seconds=5.0,
        )
        assert deadline == 55.0

    def test_compute_retry_deadline_rejects_none_clock(self) -> None:
        with pytest.raises(TypeError, match="clock must not be None"):
            compute_retry_deadline(1, clock=None)
