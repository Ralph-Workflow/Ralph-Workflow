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

    def test_prune_expired_removes_entries_past_cooldown(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        # Seed entries with staggered cooldowns. claude's NO_OUTPUT
        # cooldown is 5s, so advancing to t=10 puts claude's entry
        # past its cooldown. opencode is added at t=10 (cooldown 10s,
        # expires at t=20).
        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        clock.advance(10)
        tracker.mark_unavailable(
            "development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        # mark_unavailable on opencode triggered an opportunistic
        # prune, so claude was already swept from _entries. Verify
        # that the opportunistic path is correct, then advance
        # further and exercise the explicit prune_expired path.
        snap_after_opencode = tracker.snapshot()
        assert "development:claude" not in snap_after_opencode["unavailable_timeouts"]
        assert "development:opencode" in snap_after_opencode["unavailable_timeouts"]

        # Advance past opencode's cooldown and call prune_expired.
        clock.advance(20)
        pruned = tracker.prune_expired()
        assert pruned >= 1, (
            f"prune_expired should remove at least one entry after"
            f" cooldown elapses, got pruned={pruned}"
        )
        snap_final = tracker.snapshot()
        assert "development:opencode" not in snap_final["unavailable_timeouts"]

    def test_prune_expired_returns_count_of_pruned_entries(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "a", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        tracker.mark_unavailable(
            "development", "b", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        tracker.mark_unavailable(
            "development", "c", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        # Advance far enough that all three cooldowns have elapsed.
        clock.advance(60)
        pruned = tracker.prune_expired()
        assert pruned == 3, f"expected 3 pruned entries, got {pruned}"

    def test_prune_expired_keeps_fresh_entries(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        # Don't advance the clock — the cooldown is still active.
        pruned = tracker.prune_expired()
        assert pruned == 0, (
            f"prune_expired MUST NOT remove entries whose cooldown is"
            f" still active, got pruned={pruned}"
        )
        snap = tracker.snapshot()
        assert "development:claude" in snap["unavailable_timeouts"]

    def test_prune_expired_preserves_backoff_attempts(self) -> None:
        """``prune_expired`` MUST NOT reset the exponential backoff counter.

        Without this invariant, pruning a stale entry would reset the
        agent to attempt=0 so a recovered agent would get fresh base
        backoff instead of the longer cooldown it had been earning.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        clock.advance(5)
        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        # attempt is now 1.

        # Wait long enough for the entry to be expired.
        clock.advance(30)
        tracker.prune_expired()

        snap = tracker.snapshot()
        # Backoff attempts survives the prune so the next mark_unavailable
        # bumps it to attempt=2 (continuing exponential growth).
        assert snap["backoff_attempts"]["development:claude"] == 2

    def test_prune_expired_explicit_now_argument(self) -> None:
        """``prune_expired`` MUST honor an explicit ``now_ms`` argument.

        This supports callers that want to drive the prune from an
        external clock (e.g. test fixtures or a coordinator's tick)
        rather than the injected clock.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        # Advance tracker clock past the cooldown.
        clock.advance(100)

        # Explicit now_ms far in the future also prunes.
        pruned = tracker.prune_expired(now_ms=1_000_000_000)
        assert pruned == 1

    def test_prune_expired_idempotent_on_empty_state(self) -> None:
        """Calling ``prune_expired`` on an empty tracker is a no-op."""
        tracker = AgentUnavailabilityTracker()
        assert tracker.prune_expired() == 0

    def test_prune_expired_opportunistic_on_mark_unavailable(self) -> None:
        """``mark_unavailable`` MUST opportunistically prune expired entries.

        The hot path must keep the dict bounded without requiring an
        external caller to drive the prune. After a long clock advance,
        adding a new entry should silently sweep the expired ones.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "stale", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        # Advance far past the stale cooldown.
        clock.advance(120)
        # A new mark_unavailable on a DIFFERENT key triggers the
        # opportunistic prune.
        tracker.mark_unavailable(
            "development", "fresh", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        snap = tracker.snapshot()
        # 'stale' was swept by the opportunistic prune.
        assert "development:stale" not in snap["unavailable_timeouts"]
        assert "development:fresh" in snap["unavailable_timeouts"]

