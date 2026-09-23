"""Black-box tests for AgentUnavailabilityTracker."""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


class TestAgentUnavailabilityTracker:
    """Tests for AgentUnavailabilityTracker."""

    def test_mark_unavailable_out_of_credits(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
        )
        assert entry.base_backoff_ms == 60_000
        # Default cap is the universal five-hour ceiling (18_000_000 ms).
        assert entry.max_backoff_ms == 18_000_000
        assert entry.attempt == 0

    def test_mark_unavailable_no_output_at_start(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)
        entry = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry.base_backoff_ms == 5_000
        # Default cap is the universal five-hour ceiling (18_000_000 ms).
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
        """Plan S-1: non-auth backoff belongs to the agent across phases."""
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

        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        assert tracker.is_available("review", "claude") is False
        assert tracker.earliest_unavailable_wait_ms("review", ["claude"]) == 5_000

    def test_reset_backoff_in_another_phase_clears_agent_history(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        tracker.reset_backoff("review", "claude")

        entry = tracker.mark_unavailable(
            "review", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )

        assert entry.attempt == 0
        assert entry.unavailable_until_ms == 5_000

    def test_mark_unavailable_caps_at_max_with_explicit_policy(self) -> None:
        """Custom policies with low max cap still cap at the configured value.

        Plan S-3 regression: this proves the cap is enforced as supplied,
        not only against the (now five-hour) default. Custom policies
        smaller than the five-hour default MUST continue to clamp at the
        caller-provided value so operators can keep tighter pacing.
        """
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
        """Custom OUT_OF_CREDITS policy with 30-minute max cap is enforced.

        Plan S-3 regression: custom caps smaller than the default
        five-hour ceiling MUST continue to clamp at the supplied value.
        """
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
        """Custom STALE_CHILD_QUIET policy with 5-minute cap is enforced.

        Plan S-3 regression: custom caps smaller than the default
        five-hour ceiling MUST continue to clamp at the supplied value.
        """
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

        # Seed entries with staggered cooldowns. claude's NO_OUTPUT
        # cooldown is 5s, so advancing to t=10 puts claude's entry
        # past its cooldown. opencode is added at t=10 (cooldown 10s,
        # expires at t=20).
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(10)
        tracker.mark_unavailable("development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START)
        # mark_unavailable on opencode triggered an opportunistic
        # prune, so claude was already swept from _entries. Verify
        # that the opportunistic path is correct, then advance
        # further and exercise the explicit prune_expired path.
        snap_after_opencode = tracker.snapshot()
        assert "claude" not in snap_after_opencode["unavailable_timeouts"]
        assert "opencode" in snap_after_opencode["unavailable_timeouts"]

        # Advance past opencode's cooldown and call prune_expired.
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

        # Advance far enough that all three cooldowns have elapsed.
        clock.advance(60)
        pruned = tracker.prune_expired()
        assert pruned == 3, f"expected 3 pruned entries, got {pruned}"

    def test_prune_expired_keeps_fresh_entries(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        # Don't advance the clock — the cooldown is still active.
        pruned = tracker.prune_expired()
        assert pruned == 0, (
            f"prune_expired MUST NOT remove entries whose cooldown is"
            f" still active, got pruned={pruned}"
        )
        snap = tracker.snapshot()
        assert "claude" in snap["unavailable_timeouts"]

    def test_prune_expired_preserves_backoff_attempts(self) -> None:
        """``prune_expired`` MUST NOT reset the exponential backoff counter.

        Without this invariant, pruning a stale entry would reset the
        agent to attempt=0 so a recovered agent would get fresh base
        backoff instead of the longer cooldown it had been earning.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        clock.advance(5)
        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
        # attempt is now 1.

        # Wait long enough for the entry to be expired.
        clock.advance(30)
        tracker.prune_expired()

        snap = tracker.snapshot()
        # Backoff attempts survives the prune so the next mark_unavailable
        # bumps it to attempt=2 (continuing exponential growth).
        assert snap["backoff_attempts"]["claude"] == 2

    def test_prune_expired_explicit_now_argument(self) -> None:
        """``prune_expired`` MUST honor an explicit ``now_ms`` argument.

        This supports callers that want to drive the prune from an
        external clock (e.g. test fixtures or a coordinator's tick)
        rather than the injected clock.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        tracker.mark_unavailable("development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START)
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

        tracker.mark_unavailable("development", "stale", UnavailabilityReason.NO_OUTPUT_AT_START)
        # Advance far past the stale cooldown.
        clock.advance(120)
        # A new mark_unavailable on a DIFFERENT key triggers the
        # opportunistic prune.
        tracker.mark_unavailable("development", "fresh", UnavailabilityReason.NO_OUTPUT_AT_START)

        snap = tracker.snapshot()
        # 'stale' was swept by the opportunistic prune.
        assert "stale" not in snap["unavailable_timeouts"]
        assert "fresh" in snap["unavailable_timeouts"]


    # -----------------------------------------------------------------------
    # Five-hour ceiling regressions (plan S-3)
    #
    # The default per-reason cap is the universal 5-hour ceiling
    # (18_000_000 ms). Repeated failures grow exponentially and saturate
    # at exactly 18_000_000 ms without constructing arbitrarily large
    # integers. Per-agent history is independent; success resets only the
    # affected agent's state.
    # -----------------------------------------------------------------------

    def test_default_out_of_credits_caps_at_five_hours(self) -> None:
        """Repeated OUT_OF_CREDITS failures saturate at 18_000_000 ms (5 hours).

        Base 60_000 with cap 18_000_000: 60_000 * 2^attempt. The
        saturation attempt is the smallest one where 60_000 * 2^attempt
        >= 18_000_000, i.e. 2^attempt >= 300, so attempt=9 (512).
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        # Run enough iterations to comfortably overshoot the cap.
        for i in range(15):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
            )
            if i < 14:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_default_no_output_at_start_caps_at_five_hours(self) -> None:
        """Repeated NO_OUTPUT_AT_START failures saturate at 18_000_000 ms.

        Base 5_000 with cap 18_000_000: 5_000 * 2^attempt. The
        saturation attempt is the smallest one where 5_000 * 2^attempt
        >= 18_000_000, i.e. 2^attempt >= 3600, so attempt=12 (4096).
        """
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
        """A custom policy with max > 18_000_000 still saturates at 18_000_000.

        The five-hour ceiling is universally enforced: caller-supplied
        policies larger than 18_000_000 MUST be clamped to the universal
        ceiling so no cooldown can grow past 5 hours regardless of
        operator policy override. This is the AC-03 contract.
        """
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
                base_backoff_ms=1_000, max_backoff_ms=10**12
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        # Drive enough failures to push past the universal ceiling.
        for i in range(50):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
            )
            if i < 49:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_saturation_does_not_construct_giant_integers(self) -> None:
        """After saturation, repeated failures do not construct huge integers.

        Plan S-3 contract: "stable saturation on later failures without
        giant exponent calculation". With a tiny base (1 ms) and the
        universal ceiling (18_000_000), the saturation attempt is large
        (2^attempt >= 18_000_000 / 1 = 18_000_000, so attempt ~= 25).
        Hundreds of further failures must stay at 18_000_000 and not
        blow up the attempt counter into arbitrarily large integers.
        """
        clock = FakeClock(start=0.0)
        policy = {
            UnavailabilityReason.OUT_OF_CREDITS: ReasonBackoffPolicy(
                base_backoff_ms=1, max_backoff_ms=18_000_000
            )
        }
        tracker = AgentUnavailabilityTracker(clock=clock, backoff_policy=policy)

        for i in range(200):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
            )
            if i < 199:
                clock.advance(60_000)

        snap = tracker.snapshot()
        timeout = snap["unavailable_timeouts"]["claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        remaining = timeout - current_time_ms
        assert remaining == 18_000_000

    def test_per_agent_history_is_independent(self) -> None:
        """Failure history for one agent does not affect another agent's cooldown.

        AC-03 contract: per-agent isolation. Repeatedly failing claude
        must NOT cause opencode's first failure to use anything other
        than opencode's reason-specific base cooldown.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(10):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 9:
                clock.advance(60_000)

        # opencode's first failure uses the reason base cooldown (5_000 ms).
        entry_opencode = tracker.mark_unavailable(
            "development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry_opencode.attempt == 0
        assert entry_opencode.unavailable_until_ms - int(clock.monotonic() * 1000) == 5_000

    def test_success_resets_history_to_reason_base_delay(self) -> None:
        """A successful invocation clears the agent's unavailable state and history.

        AC-04 contract: success clears only that agent's state. The
        next failure for the SAME agent uses the reason's base delay,
        not the prior accumulated cooldown.
        """
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        for i in range(5):
            tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
            )
            if i < 4:
                clock.advance(60_000)

        # Five failures should have driven the attempt counter up.
        snap_pre = tracker.snapshot()
        assert snap_pre["backoff_attempts"]["claude"] == 5

        # Simulate a successful run: the runner calls reset_backoff.
        tracker.reset_backoff("development", "claude")

        snap_post = tracker.snapshot()
        assert "claude" not in snap_post["unavailable_timeouts"]
        assert "claude" not in snap_post["backoff_attempts"]

        # Next failure uses the reason base delay (5_000 ms).
        entry_after = tracker.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        assert entry_after.attempt == 0
        assert entry_after.unavailable_until_ms - int(clock.monotonic() * 1000) == 5_000

