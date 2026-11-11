from __future__ import annotations

import pytest

from ralph.timeout_defaults import (
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
)

_MEASURED_SILENT_STARTUP_SECONDS = 180.0


@pytest.mark.timeout_seconds(3)
def test_startup_grace_regression_outlasts_a_measured_silent_agent_startup() -> None:
    assert NO_OUTPUT_AT_START_SECONDS > _MEASURED_SILENT_STARTUP_SECONDS


@pytest.mark.timeout_seconds(3)
def test_startup_grace_regression_broken_agent_timer_also_clears_the_measurement() -> None:
    derived = max(BROKEN_AGENT_OUTPUT_GRACE_SECONDS, NO_OUTPUT_AT_START_SECONDS - 3.0)

    assert derived > _MEASURED_SILENT_STARTUP_SECONDS
