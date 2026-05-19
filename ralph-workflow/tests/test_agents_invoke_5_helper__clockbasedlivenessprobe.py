from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import FakeClock


class _ClockBasedLivenessProbe:
    """Probe that reports children active until a fake-clock threshold is reached."""

    def __init__(self, clock: FakeClock, active_until: float) -> None:
        self._clock = clock
        self._active_until = active_until

    def any_agent_active(self, label_prefix: str) -> bool:
        return self._clock.monotonic() < self._active_until
