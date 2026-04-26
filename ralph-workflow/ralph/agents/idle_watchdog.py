"""Idle watchdog for agent timeout policy enforcement.

IdleWatchdog owns the idle-deadline logic and exposes a single evaluate() method.
All wall-clock decisions go through the injected Clock so the watchdog is fully
testable without real sleeps (FakeClock) per CLAUDE.md test performance policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.timeout_clock import Clock

__all__ = [
    "IdleWatchdog",
    "WatchdogConfig",
    "WatchdogFireReason",
    "WatchdogVerdict",
]


class WatchdogVerdict(StrEnum):
    """Result of a watchdog evaluation cycle."""

    CONTINUE = "continue"
    WAITING_ON_CHILD = "waiting_on_child"
    FIRE = "fire"


class WatchdogFireReason(StrEnum):
    """Why the watchdog decided to fire."""

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"


@dataclass(frozen=True)
class WatchdogConfig:
    """Configuration for an IdleWatchdog instance.

    Attributes:
        idle_timeout_seconds: Maximum seconds without output before the watchdog
            may fire. None disables the watchdog entirely.
        drain_window_seconds: After a potential timeout, the watchdog enters a
            drain window of this duration to allow late output to flush.
        max_waiting_on_child_seconds: Hard ceiling on cumulative time the watchdog
            defers due to WAITING_ON_CHILD. Once exceeded, the watchdog fires even
            if children are still present, preventing indefinite deferral (the
            false-negative fix).
    """

    idle_timeout_seconds: float | None
    drain_window_seconds: float = 0.5
    max_waiting_on_child_seconds: float = 1800.0

    def __post_init__(self) -> None:
        if self.idle_timeout_seconds is not None and self.idle_timeout_seconds <= 0:
            msg = "idle_timeout_seconds must be positive"
            raise ValueError(msg)
        if self.drain_window_seconds < 0:
            msg = "drain_window_seconds must be >= 0"
            raise ValueError(msg)
        if (
            self.idle_timeout_seconds is not None
            and self.max_waiting_on_child_seconds < self.idle_timeout_seconds
        ):
            msg = "max_waiting_on_child_seconds must be >= idle_timeout_seconds when both set"
            raise ValueError(msg)


@dataclass
class IdleWatchdog:
    """Tracks agent idle time and decides when to fire the timeout.

    The watchdog owns the last_activity timestamp; the caller's loop must NEVER
    reset last_activity directly — that was the source of the false-negative bug
    where WAITING_ON_CHILD resets deferred the deadline forever.
    """

    _config: WatchdogConfig
    _clock: Clock
    _last_activity: float = field(init=False)
    _waiting_on_child_started_at: float | None = field(default=None, init=False)
    _cumulative_waiting_on_child_seconds: float = field(default=0.0, init=False)
    _in_drain_window: bool = field(default=False, init=False)
    _drain_started_at: float | None = field(default=None, init=False)
    _last_fire_reason: WatchdogFireReason | None = field(default=None, init=False)

    def __init__(self, config: WatchdogConfig, clock: Clock) -> None:
        self._config = config
        self._clock = clock
        self._last_activity = clock.monotonic()
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        """The reason the watchdog fired, or None if it hasn't fired yet."""
        return self._last_fire_reason

    def record_activity(self) -> None:
        """Record that the agent produced output; resets all idle/drain/child state."""
        self._last_activity = self._clock.monotonic()
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None

    def evaluate(  # noqa: PLR0911
        self,
        *,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Evaluate whether the watchdog should fire, wait, or continue.

        Args:
            classify_quiet: Called only when the idle deadline has elapsed; returns
                the current AgentExecutionState to distinguish child-wait from stall.

        Returns:
            CONTINUE: keep running normally.
            WAITING_ON_CHILD: idle deadline elapsed; children still active; last_activity not reset.
            FIRE: idle deadline elapsed with no valid deferral; caller must terminate.
        """
        from ralph.agents.execution_state import AgentExecutionState  # noqa: PLC0415

        if self._config.idle_timeout_seconds is None:
            return WatchdogVerdict.CONTINUE

        now = self._clock.monotonic()
        idle_elapsed = now - self._last_activity

        if idle_elapsed < self._config.idle_timeout_seconds:
            # Still within deadline — clear any lingering child-wait state
            self._waiting_on_child_started_at = None
            return WatchdogVerdict.CONTINUE

        # Idle deadline has elapsed.
        if self._in_drain_window:
            assert self._drain_started_at is not None
            drain_elapsed = now - self._drain_started_at
            if drain_elapsed < self._config.drain_window_seconds:
                return WatchdogVerdict.CONTINUE
            # Drain window exhausted — fire.
            self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
            return WatchdogVerdict.FIRE

        # Consult classify_quiet to determine whether to defer or fire.
        quiet_state = classify_quiet()

        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            # Accumulate deferred time.
            if self._waiting_on_child_started_at is None:
                self._waiting_on_child_started_at = now
            else:
                elapsed_since_start = now - self._waiting_on_child_started_at
                self._cumulative_waiting_on_child_seconds = elapsed_since_start

            if (
                self._cumulative_waiting_on_child_seconds
                >= self._config.max_waiting_on_child_seconds
            ):
                self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
                return WatchdogVerdict.FIRE

            # Defer without resetting last_activity.
            return WatchdogVerdict.WAITING_ON_CHILD

        # Agent appears active (no children visible) — enter drain window before firing.
        self._in_drain_window = True
        self._drain_started_at = now
        self._waiting_on_child_started_at = None
        return WatchdogVerdict.CONTINUE
