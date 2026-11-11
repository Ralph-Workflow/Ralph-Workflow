from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


class TestAgentUnavailabilityTracker:
    def test_mark_unavailable_out_of_credits(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
        )
        assert entry.base_backoff_ms == 60_000
        assert entry.max_backoff_ms == 18_000_000
        assert entry.attempt == 0

    def test_mark_unavailable_no_output_at_start(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry.base_backoff_ms == 5_000
        assert entry.max_backoff_ms == 18_000_000
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

    def test_non_auth_backoff_grows_across_phase_transition(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        first_entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert first_entry.unavailable_until_ms == 5_000

        clock.advance(5)
        second_entry = tracker.mark_unavailable(
            "review", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        assert second_entry.attempt == 1
        assert second_entry.unavailable_until_ms - int(clock.monotonic() * 1000) == 10_000

    def test_cross_phase_cooldown_blocks_availability_and_waits_by_agent(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)

        assert tracker.is_available("review", "claude") is False
        assert tracker.earliest_unavailable_wait_ms("review", ["claude"]) == 5_000

    def test_reset_backoff_in_another_phase_clears_agent_history(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        tracker.reset_backoff("review", "claude")

        entry = tracker.mark_unavailable(
            "review", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        assert entry.attempt == 0
        assert entry.unavailable_until_ms == 5_000

    def test_mark_unavailable_caps_at_max_with_explicit_policy(self) -> None:
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
                base_backoff_ms=5_000, max_backoff_ms=30_000
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 9:
                clock.advance(300)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 30_000

    def test_out_of_credits_30min_cap_with_explicit_policy(self) -> None:
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
                base_backoff_ms=60_000, max_backoff_ms=1_800_000
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(10):
            tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
            if i < 9:
                clock.advance(3000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 1_800_000

    def test_stale_child_quiet_5min_cap_with_explicit_policy(self) -> None:
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.STALE_CHILD_QUIET: ReasonBackoffPolicy(
                base_backoff_ms=15_000, max_backoff_ms=300_000
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.STALE_CHILD_QUIET
            )
            if i < 9:
                clock.advance(3000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
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
            initial_timeouts={"claude": 60_000},
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
            initial_entries={"claude": entry},
        )
        snap = tracker.snapshot()
        assert snap["unavailable_timeouts"]["claude"] == 120_000

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

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(10)
        tracker.mark_unavailable("development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START)
        snap_after_opencode = tracker.snapshot()
        assert "claude" not in snap_after_opencode["unavailable_timeouts"]
        assert "opencode" in snap_after_opencode["unavailable_timeouts"]

        clock.advance(20)
        pruned = tracker.prune_expired()
        assert pruned >= 1, (
            f"prune_expired should remove at least one entry after"
            f" cooldown elapses, got pruned={pruned}"
        )
        snap_final = tracker.snapshot()
        assert "opencode" not in snap_final["unavailable_timeouts"]

    def test_prune_expired_returns_count_of_pruned_entries(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "a", UnavailabilityReason.NO_OUTPUT_AT_START)
        tracker.mark_unavailable("development", "b", UnavailabilityReason.NO_OUTPUT_AT_START)
        tracker.mark_unavailable("development", "c", UnavailabilityReason.NO_OUTPUT_AT_START)

        clock.advance(60)
        pruned = tracker.prune_expired()
        assert pruned == 3, f"expected 3 pruned entries, got {pruned}"

    def test_prune_expired_keeps_fresh_entries(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        pruned = tracker.prune_expired()
        assert pruned == 0, (
            f"prune_expired MUST NOT remove entries whose cooldown is"
            f" still active, got pruned={pruned}"
        )
        snap = tracker.snapshot()
        assert "claude" in snap["unavailable_timeouts"]

    def test_prune_expired_preserves_backoff_attempts(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(5)
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)

        clock.advance(30)
        tracker.prune_expired()

        snap = tracker.snapshot()
        assert snap["backoff_attempts"]["claude"] == 2

    def test_prune_expired_explicit_now_argument(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(100)

        pruned = tracker.prune_expired(now_ms=1_000_000_000)
        assert pruned == 1

    def test_prune_expired_idempotent_on_empty_state(self) -> None:
        tracker = AgentUnavailabilityTracker()
        assert tracker.prune_expired() == 0

    def test_prune_expired_opportunistic_on_mark_unavailable(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "stale", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(120)
        tracker.mark_unavailable("development", "fresh", UnavailabilityReason.NO_OUTPUT_AT_START)

        snap = tracker.snapshot()
        assert "stale" not in snap["unavailable_timeouts"]
        assert "fresh" in snap["unavailable_timeouts"]

    def test_default_out_of_credits_caps_at_five_hours(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(15):
            tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
            if i < 14:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_default_no_output_at_start_caps_at_five_hours(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(20):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 19:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_oversized_custom_policy_caps_at_five_hours(self) -> None:
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
                base_backoff_ms=1_000, max_backoff_ms=10**12
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(50):
            tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
            if i < 49:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_saturation_does_not_construct_giant_integers(self) -> None:
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
                base_backoff_ms=1, max_backoff_ms=18_000_000
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(200):
            tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
            if i < 199:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_per_agent_history_is_independent(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 9:
                clock.advance(60_000)

        entry_opencode = tracker.mark_unavailable(
            "development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry_opencode.attempt == 0
        assert entry_opencode.unavailable_until_ms - int(clock.monotonic() * 1000) == 5_000

    def test_success_resets_history_to_reason_base_delay(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(5):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 4:
                clock.advance(60_000)

        snap_pre = tracker.snapshot()
        assert snap_pre["backoff_attempts"]["claude"] == 5

        tracker.reset_backoff("development", "claude")

        snap_post = tracker.snapshot()
        assert "claude" not in snap_post["unavailable_timeouts"]
        assert "claude" not in snap_post["backoff_attempts"]

        entry_after = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry_after.attempt == 0
        assert entry_after.unavailable_until_ms - int(clock.monotonic() * 1000) == 5_000


def test_all_default_reasons_saturate_at_five_hours() -> None:
    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    for reason in UnavailabilityReason:
        for i in range(50):
            tracker.mark_unavailable("development", f"agent_{reason.value}", reason)
            if i < 49:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"][f"agent_{reason.value}"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000, (
            f"reason={reason.value} expected ceiling 18_000_000 ms, got {remaining}"
        )


def test_per_agent_history_isolation_across_phases() -> None:
    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    for i in range(5):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        if i < 4:
            clock.advance(60_000)

    entry = tracker.mark_unavailable("review", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
    assert entry.attempt == 5
    expected_remaining = 5_000 * (2**5)
    assert entry.unavailable_until_ms - int(clock.monotonic() * 1000) == expected_remaining


def test_saturation_stable_across_many_failures() -> None:
    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    for i in range(500):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        if i < 499:
            clock.advance(60_000)

    snap = tracker.snapshot()
    timeout = snap["unavailable_timeouts"]["claude"]
    current_time_ms = int(clock.monotonic() * 1000)
    remaining = timeout - current_time_ms
    assert remaining == 18_000_000
    assert snap["backoff_attempts"]["claude"] <= 100


def test_custom_policy_with_lower_cap_clamps_at_custom_value() -> None:
    clock = FakeClock(start=0.0)
    policy = {
        UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
            base_backoff_ms=5_000, max_backoff_ms=30_000
        )
    }
    tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

    for i in range(15):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        if i < 14:
            clock.advance(60_000)

    snap = tracker.snapshot()
    timeout = snap["unavailable_timeouts"]["claude"]
    current_time_ms = int(clock.monotonic() * 1000)
    remaining = timeout - current_time_ms
    assert remaining == 30_000


def test_custom_policy_with_higher_cap_clamps_at_universal_ceiling() -> None:
    clock = FakeClock(start=0.0)
    policy = {
        UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
            base_backoff_ms=1_000, max_backoff_ms=10**12
        )
    }
    tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

    for i in range(100):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        if i < 99:
            clock.advance(60_000)

    snap = tracker.snapshot()
    timeout = snap["unavailable_timeouts"]["claude"]
    current_time_ms = int(clock.monotonic() * 1000)
    remaining = timeout - current_time_ms
    assert remaining == 18_000_000


def test_base_must_be_positive() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError, match="base_backoff_ms must be positive"):
        ReasonBackoffPolicy(base_backoff_ms=0, max_backoff_ms=60_000)


def test_max_must_be_strictly_greater_than_base() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError, match="must be strictly greater than base_backoff_ms"):
        ReasonBackoffPolicy(base_backoff_ms=5000, max_backoff_ms=5000)
    with _pytest.raises(ValueError, match="must be strictly greater than base_backoff_ms"):
        ReasonBackoffPolicy(base_backoff_ms=10_000, max_backoff_ms=5000)


def test_success_reset_only_clears_successful_agent_history() -> None:
    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    for i in range(5):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        if i < 4:
            clock.advance(60_000)
    clock.advance(60_000)

    for i in range(3):
        tracker.mark_unavailable("development", "opencode", UnavailabilityReason.OUT_OF_CREDITS)
        if i < 2:
            clock.advance(60_000)

    snap_pre = tracker.snapshot()
    assert snap_pre["backoff_attempts"]["claude"] == 5
    assert snap_pre["backoff_attempts"]["opencode"] == 3

    tracker.reset_backoff("development", "claude")

    snap_post = tracker.snapshot()
    assert "claude" not in snap_post["unavailable_timeouts"]
    assert "claude" not in snap_post["backoff_attempts"]
    assert "opencode" in snap_post["unavailable_timeouts"]
    assert snap_post["backoff_attempts"]["opencode"] == 3


def test_success_reset_next_failure_starts_at_base_delay() -> None:
    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    for i in range(5):
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
        if i < 4:
            clock.advance(60_000)

    tracker.reset_backoff("development", "claude")

    entry = tracker.mark_unavailable("development", "claude", UnavailabilityReason.OUT_OF_CREDITS)
    assert entry.attempt == 0
    assert entry.unavailable_until_ms - int(clock.monotonic() * 1000) == 60_000
