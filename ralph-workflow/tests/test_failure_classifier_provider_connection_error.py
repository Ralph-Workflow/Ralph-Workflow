"""``Connection error.`` is a transport fault, not a code failure.

This is the literal string pi reports in ``message.errorMessage`` /
``auto_retry_end.finalError`` when its configured provider is
unreachable.  The marker set already covered "connection refused" /
"connection reset" / "connection timed out" but not this shape, so an
offline provider was classified as a generic agent failure and burned
the retry budget instead of routing to connectivity backoff.
"""

from __future__ import annotations

from ralph.recovery.failure_classifier import _message_looks_environmental


def test_pi_connection_error_is_environmental() -> None:
    assert _message_looks_environmental("Connection error.")


def test_provider_failure_frame_is_environmental() -> None:
    assert _message_looks_environmental(
        "pi agent provider failure (stopReason=error): Connection error."
    )


def test_ordinary_failure_is_not_environmental() -> None:
    assert not _message_looks_environmental("AssertionError: expected 3 got 4")
