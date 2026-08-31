"""The startup grace must outlast a real agent's silent initialization.

OpenCode's ``run --format json`` emits NOTHING until its first tool call. Measured
against the installed CLI (1.18.25) with a real Ralph-sized prompt, it produced no
frame at all within 180 seconds. The startup grace had been walked down 120s -> 30s
-> 15s chasing faster hang detection, so the pipeline killed every OpenCode run long
before it could speak, while the smoke harness silently rewrote the same setting to
its own 360s ceiling -- which is precisely why the smoke passed while the pipeline
failed, and why the breakage went unnoticed.

The broken-agent timer derives from the same value
(``_effective_broken_agent_grace_seconds`` = ``max(12, configured - 3)``), so both
kills move together and both are pinned here.
"""

from __future__ import annotations

import pytest

from ralph.timeout_defaults import (
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
)

#: Measured time-to-first-frame for `opencode run --format json` on a real
#: Ralph prompt: no output at all after 180s. The default must clear that with
#: room to spare rather than sit just above it.
_MEASURED_SILENT_STARTUP_SECONDS = 180.0


@pytest.mark.timeout_seconds(3)
def test_startup_grace_regression_outlasts_a_measured_silent_agent_startup() -> None:
    """A silent agent must not be killed before it has had a chance to speak."""
    assert NO_OUTPUT_AT_START_SECONDS > _MEASURED_SILENT_STARTUP_SECONDS


@pytest.mark.timeout_seconds(3)
def test_startup_grace_regression_broken_agent_timer_also_clears_the_measurement() -> None:
    """The broken-agent kill derives from the same grace and must clear it too.

    ``_effective_broken_agent_grace_seconds`` returns ``max(floor, configured - 3)``,
    so a startup grace that clears the measurement but leaves the derived value
    under it would simply move the premature kill to the other timer.
    """
    derived = max(BROKEN_AGENT_OUTPUT_GRACE_SECONDS, NO_OUTPUT_AT_START_SECONDS - 3.0)

    assert derived > _MEASURED_SILENT_STARTUP_SECONDS
