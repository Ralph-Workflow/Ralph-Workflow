"""Black-box tests for AgentUnavailabilityTracker."""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.unavailability_reason import UnavailabilityReason


class TestAgentUnavailabilityTracker:
    """Tests for AgentUnavailabilityTracker."""

    def test_mark_unavailable_out_of_credits(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
        )
        assert entry.base_backoff_ms == 60_000
        assert entry.max_backoff_ms == 1_800_000
        assert entry.attempt == 0

    def test_mark_unavailable_no_output_at_start(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry.base_backoff_ms == 5_000
        assert entry.max_backoff_ms == 30_000
        assert entry.attempt == 0

    def test_mark_unavailable_exponential_growth(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        entry1 = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry1.unavailable_until_ms == 5_000

        clock.advance(5)
        entry2 = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry2.attempt == 1
        assert entry2.unavailable_until_ms - entry1.unavailable_until_ms == 10_000

        clock.advance(10)
        entry3 = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry3.attempt == 2
        assert entry3.unavailable_until_ms - entry2.unavailable_until_ms == 20_000

    def test_mark_unavailable_caps_at_max(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 9:
                clock.advance(300)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["development:claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 30_000

    def test_out_of_credits_30min_cap(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(10):
            tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
            if i < 9:
                clock.advance(3000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["development:claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 1_800_000

    def test_stale_child_quiet_5min_cap(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.STALE_CHILD_QUIET
            )
            if i < 9:
                clock.advance(3000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["development:claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 300_000

    def test_is_available_after_timeout(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        assert tracker.is_available("development", "claude") is False

        clock.advance(6)
        assert tracker.is_available("development", "claude") is True

    def test_earliest_unavailable_wait_ms(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        tracker.mark_unavailable("development", "opencode", UnavailabilityReason.STALE_CHILD_QUIET)

        wait = tracker.earliest_unavailable_wait_ms("development", ["claude", "opencode"])
        assert wait > 0

    def test_reset_backoff(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        assert tracker.is_available("development", "claude") is False

        tracker.reset_backoff("development", "claude")
        assert tracker.is_available("development", "claude") is True

    def test_snapshot_returns_defensive_copy(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        snap1 = tracker.snapshot()
        snap2 = tracker.snapshot()

        assert snap1["unavailable_timeouts"] is not snap2["unavailable_timeouts"]

    def test_legacy_initial_timeouts_seam(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(
            clock=clock,
            initial_timeouts={"development:claude": 60_000},
        )
        assert tracker.is_available("development", "claude") is False

    def test_initial_entries_seam(self) -> None:
        clock = FakeClock(start=0.0)
        entry = UnavailabilityEntry(
            unavailable_until_ms=120_000,
            reason=UnavailabilityReason.OUT_OF_CREDITS,
            attempt=1,
            base_backoff_ms=60_000,
            max_backoff_ms=1_800_000,
        )
        tracker = AgentUnavailabilityTracker(
            clock=clock,
            initial_entries={"development:claude": entry},
        )
        snap = tracker.snapshot()
        assert snap["unavailable_timeouts"]["development:claude"] == 120_000

    def test_mark_unavailable_reason_none_uses_legacy_policy(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        entry = tracker.mark_unavailable("development", "claude", None)
        assert entry.base_backoff_ms == 5_000
        assert entry.max_backoff_ms == 300_000

    def test_unavailability_store_protocol_is_runtime_checkable(self) -> None:
        tracker = AgentUnavailabilityTracker()
        assert isinstance(tracker, UnavailabilityStore) is True

    def test_scope_defaults_to_session(self) -> None:
        tracker = AgentUnavailabilityTracker()
        assert tracker.scope == "session"
