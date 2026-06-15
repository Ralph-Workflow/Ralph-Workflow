"""Black-box tests for UnavailabilityReason classifier logic."""

from __future__ import annotations

from datetime import UTC, datetime

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.classifier import FailureClassifier
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.failure_classifier import _classify_unavailability_reason
from ralph.recovery.unavailability_reason import UnavailabilityReason


class TestUnavailabilityReasonClassifier:
    """Tests for the _classify_unavailability_reason helper."""

    def test_watchdog_reason_no_output_at_start(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason="no_output_at_start",
            detail_parts=[],
            raw_message="agent produced no output",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.NO_OUTPUT_AT_START

    def test_watchdog_reason_no_progress_quiet_maps_to_stale_child_quiet(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason="no_progress_quiet",
            detail_parts=[],
            raw_message="agent produced no output",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.STALE_CHILD_QUIET

    def test_watchdog_reason_children_persist_too_long_maps_to_suspicious_timeout(
        self,
    ) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason="children_persist_too_long",
            detail_parts=[],
            raw_message="agent produced no output",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT

    def test_subscription_limit_message_online(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=["You've hit your weekly limit"],
            raw_message="You've hit your weekly limit",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.OUT_OF_CREDITS

    def test_subscription_limit_message_offline_returns_none(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=["You've hit your weekly limit"],
            raw_message="You've hit your weekly limit",
            connectivity_state="offline",
        )
        assert result is None

    def test_suspicious_timeout_without_output_online(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=["timed out with no output"],
            raw_message="agent timed out with no output",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT

    def test_unavailable_agent_message_no_prior_activity(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=["agent produced no output"],
            raw_message="agent produced no output",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.NO_OUTPUT_AT_START

    def test_post_tool_empty_response(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=['{"type":"tool_result"}', "empty response with no tool calls"],
            raw_message="Model returned an empty response with no tool calls",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY

    def test_no_reason_returns_none(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason=None,
            detail_parts=["some random error"],
            raw_message="some random error",
            connectivity_state="online",
        )
        assert result is None

    def test_watchdog_reason_precedence_beats_text(self) -> None:
        result = _classify_unavailability_reason(
            watchdog_reason="no_progress_quiet",
            detail_parts=["You've hit your weekly limit"],
            raw_message="You've hit your weekly limit",
            connectivity_state="online",
        )
        assert result == UnavailabilityReason.STALE_CHILD_QUIET


class TestFailureClassifierUnavailabilityReasonIntegration:
    """Integration tests for unavailability_reason propagation through FailureClassifier."""

    def test_out_of_credits_sets_reason(self) -> None:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "You've hit your weekly limit",
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.is_unavailable is True
        assert failure.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    def test_no_output_at_start_watchdog_sets_reason(self) -> None:
        opts = InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            diagnostic={"invocation_elapsed": 60.0},
        )
        exc = AgentInactivityTimeoutError("claude", 60.0, opts=opts)
        classifier = FailureClassifier()
        failure = classifier.classify(
            exc,
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.is_unavailable is True
        assert failure.unavailability_reason == UnavailabilityReason.NO_OUTPUT_AT_START

    def test_no_progress_quiet_watchdog_sets_stale_child_quiet(self) -> None:
        opts = InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"alive_by": "os_descendant_only_stale_progress"},
        )
        exc = AgentInactivityTimeoutError("claude", 120.0, opts=opts)
        classifier = FailureClassifier()
        failure = classifier.classify(
            exc,
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.is_unavailable is True
        assert failure.unavailability_reason == UnavailabilityReason.STALE_CHILD_QUIET

    def test_children_persist_too_long_sets_suspicious_timeout(self) -> None:
        opts = InactivityTimeoutOpts(
            reason=WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            diagnostic={"cumulative": 600.0},
        )
        exc = AgentInactivityTimeoutError("claude", 600.0, opts=opts)
        classifier = FailureClassifier()
        failure = classifier.classify(
            exc,
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.is_unavailable is True
        assert failure.unavailability_reason == UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT

    def test_post_tool_empty_response_sets_no_output_after_activity(self) -> None:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "Model returned an empty response with no tool calls",
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.is_unavailable is True
        assert failure.unavailability_reason == UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY

    def test_non_agent_category_returns_none_reason(self) -> None:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "Connection reset by peer",
            phase="development",
            agent="claude",
            connectivity_state="online",
        )
        assert failure.category.value == "environmental"
        assert failure.unavailability_reason is None

    def test_offline_connectivity_returns_none_reason(self) -> None:
        classifier = FailureClassifier()
        failure = classifier.classify(
            "agent produced no output",
            phase="development",
            agent="claude",
            connectivity_state="offline",
        )
        assert failure.is_unavailable is False
        assert failure.unavailability_reason is None

    def test_out_of_credits_backoff_grows_exponentially_and_caps_at_thirty_minutes(self) -> None:
        clock = FakeClock(start=0.0)
        tracker = AgentUnavailabilityTracker(clock=clock)

        cooldowns = []
        for i in range(10):
            entry = tracker.mark_unavailable(
                "development", "claude", UnavailabilityReason.OUT_OF_CREDITS
            )
            if i == 0:
                assert entry.base_backoff_ms == 60_000
                assert entry.max_backoff_ms == 1_800_000

            cooldowns.append(entry.unavailable_until_ms - int(clock.monotonic() * 1000))
            clock.advance((entry.unavailable_until_ms - int(clock.monotonic() * 1000)) / 1000.0)

        expected_cooldowns = [
            60_000,
            120_000,
            240_000,
            480_000,
            960_000,
            1_800_000,
            1_800_000,
            1_800_000,
            1_800_000,
            1_800_000,
        ]
        assert cooldowns == expected_cooldowns
        assert cooldowns[-1] == 1_800_000

    def test_new_limit_substrings_classify_as_out_of_credits(self) -> None:
        classifier = FailureClassifier()
        new_substrings = [
            "daily limit exceeded",
            "weekly limit exceeded",
            "monthly limit exceeded",
            "insufficient_quota",
        ]
        for sub in new_substrings:
            failure = classifier.classify(
                f"Error: {sub} in api call",
                phase="development",
                agent="claude",
                connectivity_state="online",
            )
            assert failure.category.value == "agent"
            assert failure.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    def test_generic_throttling_does_not_classify_as_out_of_credits(self) -> None:
        """Generic throttling markers are NOT credit exhaustion.

        A bare ``rate_limited`` token is a transient rate-limit response
        that recovers in seconds-to-minutes, not a credit-exhausted state
        that needs a 60s->30min cooldown. The ``OUT_OF_CREDITS`` reason
        applies the long unavailable-agent cooldown, which is wrong for
        a generic throttle. The classifier must NOT match the bare
        ``rate_limited`` substring against the subscription-limit table.
        """
        classifier = FailureClassifier()
        generic_throttle_substrings = [
            "rate_limited",
            "Error: rate_limited in api call",
            "rate_limited: retry after 1s",
        ]
        for msg in generic_throttle_substrings:
            failure = classifier.classify(
                msg,
                phase="development",
                agent="claude",
                connectivity_state="online",
            )
            assert failure.unavailability_reason != UnavailabilityReason.OUT_OF_CREDITS, (
                f"generic throttle substring {msg!r} must NOT classify as OUT_OF_CREDITS; "
                f"got {failure.unavailability_reason!r}"
            )

    def test_offline_connectivity_does_not_match_credit_substrings(self) -> None:
        classifier = FailureClassifier()
        new_substrings = [
            "daily limit exceeded",
            "weekly limit exceeded",
            "monthly limit exceeded",
            "insufficient_quota",
        ]
        for sub in new_substrings:
            failure = classifier.classify(
                f"Error: {sub} in api call",
                phase="development",
                agent="claude",
                connectivity_state="offline",
            )
            assert failure.unavailability_reason is None
            assert failure.is_unavailable is False


class TestFailureEventUnavailabilityReason:
    """Tests for unavailability_reason propagation to FailureEvent."""

    def test_failure_event_carries_unavailability_reason(self) -> None:
        evt = FailureEvent(
            timestamp=datetime.now(UTC),
            phase="development",
            agent="claude",
            category="agent",
            reason="Agent fault: test",
            counted_against_budget=True,
            chain_capacity_remaining=1,
            recovery_cycle=0,
            retry_delay_ms=0,
            watchdog_reason="no_output_at_start",
            unavailability_reason="no_output_at_start",
        )
        assert evt.unavailability_reason == "no_output_at_start"


class TestFalloverEventUnavailabilityReason:
    """Tests for unavailability_reason propagation to FalloverEvent."""

    def test_fallover_event_carries_unavailability_reason(self) -> None:
        evt = FalloverEvent.now(
            phase="development",
            from_agent="claude",
            to_agent="opencode",
            reason="Agent unavailable",
            watchdog_reason="no_progress_quiet",
            unavailability_reason="stale_child_quiet",
        )
        assert evt.unavailability_reason == "stale_child_quiet"
