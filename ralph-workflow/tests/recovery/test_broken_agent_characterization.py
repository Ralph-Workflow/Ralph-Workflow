"""Characterization of the fast broken-agent timeout contract."""

from __future__ import annotations

from ralph.timeout_defaults import (
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
)


def test_broken_agent_grace_is_fast_and_precedes_startup_watchdog() -> None:
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS == 30.0
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS < NO_OUTPUT_AT_START_SECONDS
    assert NO_OUTPUT_AT_START_SECONDS == 120.0
