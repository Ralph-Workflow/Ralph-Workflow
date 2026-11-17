from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from ralph.mcp.server._startup_watch_verdict import StartupVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

STAGE_POLL_INTERVAL_S: Final[float] = 0.25


class StartupWatch:
    __slots__ = (
        "_next_stage_poll_at",
        "boot_allowance_s",
        "child_started_at",
        "clock",
        "envelope_s",
        "read_stage",
        "spawn_start",
        "stage_poll_interval_s",
    )

    def __init__(
        self,
        spawn_start: float,
        envelope_s: float,
        boot_allowance_s: float,
        read_stage: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        stage_poll_interval_s: float = STAGE_POLL_INTERVAL_S,
    ) -> None:
        self.spawn_start = spawn_start
        self.envelope_s = envelope_s
        self.boot_allowance_s = boot_allowance_s
        self.read_stage = read_stage
        self.clock = clock
        self.stage_poll_interval_s = stage_poll_interval_s
        self.child_started_at: float | None = None
        self._next_stage_poll_at = 0.0

    @property
    def split_phases(self) -> bool:
        return self.boot_allowance_s > 0

    @property
    def total_budget_s(self) -> float:
        return self.envelope_s + max(0.0, self.boot_allowance_s)

    def check(self) -> StartupVerdict:
        now = self.clock()
        if not self.split_phases:
            if now - self.spawn_start >= self.envelope_s:
                return StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED
            return StartupVerdict.WAITING

        if self.child_started_at is None and now >= self._next_stage_poll_at:
            self._next_stage_poll_at = now + self.stage_poll_interval_s
            if self.read_stage() is not None:
                self.child_started_at = now

        if self.child_started_at is None:
            if now - self.spawn_start >= self.boot_allowance_s:
                return StartupVerdict.BOOT_DEADLINE_EXCEEDED
            return StartupVerdict.WAITING

        if now - self.child_started_at >= self.envelope_s:
            return StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED
        return StartupVerdict.WAITING


__all__ = ["STAGE_POLL_INTERVAL_S", "StartupVerdict", "StartupWatch"]
