"""Black-box tests for UnavailabilityReason classifier logic."""

from __future__ import annotations

from datetime import UTC, datetime

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
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
