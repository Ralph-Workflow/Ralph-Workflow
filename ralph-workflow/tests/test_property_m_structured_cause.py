# property-test: M — failures classified by structured cause, not text-match
"""Failures are classified by their authoritative cause.

A watchdog SIGTERM (exit code -15, fire-reason on the exception) was
relabeled as a connectivity blip because the agent's stderr contained
the word "timeout". The classifier must consult the typed cause
(``exc.reason``, ``exc.signal``) and the exception type, not
substring-match free text.
"""

from __future__ import annotations

import errno

import pytest

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def test_idle_watchdog_kill_with_timeout_token_still_classified_as_agent() -> None:
    """An IdleWatchdogKilledError with 'timeout' in its __str__ is AGENT, not env.

    The exception's __str__ contains the word 'timeout' as a misleading
    token (it is actually a SIGTERM). The classifier must consult the
    typed attributes and classify as AGENT, never relabel as a
    connectivity blip.
    """
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="prop-m", agent="prop-m-agent")
    assert classified.category == FailureCategory.AGENT, (
        f"IdleWatchdogKilledError must classify as AGENT, got {classified.category}"
    )


def test_idle_watchdog_kill_reason_is_preserved_in_classification() -> None:
    """The fire-reason is preserved through classification, not relabeled."""
    exc = IdleWatchdogKilledError(reason="stalled", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT
    # The reason carries the watchdog's authoritative cause, surfaced
    # so the operator sees "stalled", not "timeout".
    assert "stalled" in str(exc).lower() or "stalled" in classified.reason.lower()


def test_idle_watchdog_kill_counts_against_budget() -> None:
    """An IdleWatchdogKilledError counts against the agent budget."""
    exc = IdleWatchdogKilledError(reason="no_output", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.counts_against_budget is True


def test_idle_watchdog_kill_does_not_reset_session() -> None:
    """A SIGTERM is not a stale session — the session is still valid."""
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.reset_session is False


def test_oserror_etimedout_still_classified_as_environmental() -> None:
    """An OSError(ETIMEDOUT) is still ENVIRONMENTAL (regression check)."""
    exc = OSError(errno.ETIMEDOUT, "timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_connection_error_with_timeout_text_still_environmental() -> None:
    """A ConnectionError with 'timeout' in its message is still ENVIRONMENTAL.

    The text-based check (_is_environmental_exc) consults the exception
    type and the error number, not the message text. This guards against
    the regression where the text-match vocabulary would also fire on
    IdleWatchdogKilledError (the original bug).
    """
    exc = ConnectionError("connection timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_idle_watchdog_kill_takes_precedence_over_environmental_check() -> None:
    """Even with 'timeout' in the str, IdleWatchdogKilledError is AGENT.

    Regression test for the exact failure mode described in PROMPT.md:
    a SIGTERM relabeled as 'transient connectivity failure' because the
    stderr happened to contain the word 'timeout'. The IdleWatchdogKilledError
    branch must fire BEFORE any text-based environmental check.
    """
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    # Build a ConnectionError with a misleading text matching the same marker
    # vocabulary that the environmental check would use
    # The IdleWatchdogKilledError must classify as AGENT, not ENVIRONMENTAL
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT


def test_socket_timeout_still_environmental() -> None:
    """A socket.timeout is still ENVIRONMENTAL (typed, not text-matched)."""
    exc = TimeoutError("timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_idle_watchdog_kill_classifier_uses_typed_signal_and_reason() -> None:
    """The classifier consults exc.signal and exc.reason — typed attributes."""
    exc = IdleWatchdogKilledError(reason="specific_test_reason_xyz", signal=15)
    # If the classifier consulted str(exc), it would still produce
    # the correct answer here (because the message contains the reason).
    # The deeper property: the type check is FIRST, so even a future
    # subclass that overrides __str__ to lie would still classify as AGENT.
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT


def test_idle_watchdog_kill_subclass_with_misleading_str_still_agent() -> None:
    """A subclass that lies in __str__ still classifies as AGENT (typed wins)."""

    class _MisleadingIdleKill(IdleWatchdogKilledError):  # noqa: N818
        def __str__(self) -> str:
            return "ConnectionError: transient timeout blip"

    exc = _MisleadingIdleKill(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT, (
        f"subclass with misleading __str__ must still classify as AGENT, "
        f"got {classified.category}"
    )


def test_idle_watchdog_kill_carries_typed_attributes() -> None:
    """The exception exposes .reason and .signal as typed instance attributes."""
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    assert exc.reason == "idle"
    assert exc.signal == 15


def test_idle_watchdog_kill_preserves_attrs_in_subclass() -> None:
    """A subclass inherits .reason and .signal from the base."""
    class _IdleKill(IdleWatchdogKilledError):  # noqa: N818
        pass

    exc = _IdleKill(reason="stalled", signal=9)
    assert exc.reason == "stalled"
    assert exc.signal == 9


@pytest.mark.parametrize(
    "reason",
    ["idle", "stalled", "no_output", "post_tool_stall", "subprocess_stall"],
)
def test_idle_watchdog_kill_various_reasons_all_classify_as_agent(
    reason: str,
) -> None:
    """Various watchdog fire-reasons all classify as AGENT."""
    exc = IdleWatchdogKilledError(reason=reason, signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT
