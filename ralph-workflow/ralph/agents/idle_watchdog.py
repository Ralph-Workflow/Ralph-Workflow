"""Idle watchdog for agent timeout policy enforcement.

IdleWatchdog owns the in-stream idle/deadline logic and exposes a single evaluate()
method.  All wall-clock decisions go through the injected Clock so the watchdog is
fully testable without real sleeps (FakeClock) per CLAUDE.md test performance policy.

This module is the counterpart to ralph.agents.post_exit_watchdog.PostExitWatchdog,
which owns post-exit (post-EOF) wall-clock timeouts.  Together these two watchdogs
cover every wall-clock timeout fire path in the agent invocation system; no ad-hoc
clock.monotonic()/clock.sleep() loops are allowed in invoke.py.

IdleWatchdog owns fire reasons: SESSION_CEILING_EXCEEDED, NO_OUTPUT_DEADLINE,
and CHILDREN_PERSIST_TOO_LONG.  PostExitWatchdog owns: PROCESS_EXIT_HANG and
DESCENDANT_HANG.  See ralph.agents.post_exit_watchdog for the post-exit family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.timeout_clock import Clock

__all__ = [
    "IdleWatchdog",
    "TimeoutPolicy",
    "WatchdogFireReason",
    "WatchdogVerdict",
]


class WatchdogVerdict(StrEnum):
    """Result of a watchdog evaluation cycle."""

    CONTINUE = "continue"
    WAITING_ON_CHILD = "waiting_on_child"
    FIRE = "fire"


class WatchdogFireReason(StrEnum):
    """Why the watchdog decided to fire.

    IdleWatchdog reasons (in-stream):
      NO_OUTPUT_DEADLINE, CHILDREN_PERSIST_TOO_LONG, SESSION_CEILING_EXCEEDED.
    PostExitWatchdog reasons (post-exit):
      PROCESS_EXIT_HANG, DESCENDANT_HANG.
    """

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"
    SESSION_CEILING_EXCEEDED = "session_ceiling_exceeded"
    PROCESS_EXIT_HANG = "process_exit_hang"
    DESCENDANT_HANG = "descendant_hang"


@dataclass(frozen=True)
class TimeoutPolicy:
    """Consolidated timeout configuration for all agent timeout dimensions.

    All timeout constants that previously appeared as module-level magic numbers
    in invoke.py are consolidated here so a single config-built TimeoutPolicy
    governs every timeout decision.

    Precedence of fire conditions (in evaluation order):

    1. SESSION_CEILING_EXCEEDED — absolute wall-clock cap; activity cannot reset it.
    2. NO_OUTPUT_DEADLINE (+ drain window) — idle deadline since last output.
    3. CHILDREN_PERSIST_TOO_LONG — cumulative WAITING_ON_CHILD ceiling; this is an
       absolute ceiling that survives heartbeat activity. Cumulative is only reset
       after a sustained-active interval (>= drain_window_seconds without WAITING).
    4. PROCESS_EXIT_HANG — subprocess closed stdout but did not exit within budget.
    5. DESCENDANT_HANG — descendant-wait deadline elapsed with persistent WAITING_ON_CHILD
       (post-exit only, owned by PostExitWatchdog).

    Attributes:
        idle_timeout_seconds: Maximum seconds without output before watchdog may fire.
            None disables the idle-timeout watchdog entirely.
        drain_window_seconds: After a potential timeout, the watchdog enters a drain
            window of this duration to allow late output to flush.
        max_waiting_on_child_seconds: Hard ceiling on cumulative time the watchdog
            defers due to WAITING_ON_CHILD. Once exceeded, fires even if children present.
        max_session_seconds: Absolute wall-clock ceiling for the entire session.
            Activity cannot reset this ceiling. None means no ceiling (opt-in).
            When set, must be >= idle_timeout_seconds.
        idle_poll_interval_seconds: How often the read loop polls for new lines.
            Values < 0.01s are intended for tests only.
        parent_exit_grace_seconds: Grace window after parent rc=0 exit during which
            we poll for late completion signals or appearing children.
        descendant_wait_timeout_seconds: Maximum time to wait for descendant processes
            to finish before declaring failure.
        descendant_wait_poll_seconds: Poll interval for descendant-wait and
            process-exit-wait loops. Values < 0.01s are intended for tests only.
        process_exit_wait_seconds: Maximum time to wait for a subprocess to exit after
            its stdout closes. Prevents hanging on subprocesses that close stdout but
            never call exit().
    """

    idle_timeout_seconds: float | None
    drain_window_seconds: float = 0.5
    max_waiting_on_child_seconds: float = 1800.0
    max_session_seconds: float | None = None
    idle_poll_interval_seconds: float = 0.05
    parent_exit_grace_seconds: float = 5.0
    descendant_wait_timeout_seconds: float = 30.0
    descendant_wait_poll_seconds: float = 0.5
    process_exit_wait_seconds: float = 30.0

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
        if self.max_session_seconds is not None and self.max_session_seconds <= 0:
            msg = "max_session_seconds must be positive"
            raise ValueError(msg)
        if (
            self.max_session_seconds is not None
            and self.idle_timeout_seconds is not None
            and self.max_session_seconds < self.idle_timeout_seconds
        ):
            msg = "max_session_seconds must be >= idle_timeout_seconds"
            raise ValueError(msg)
        if self.idle_poll_interval_seconds <= 0:
            msg = "idle_poll_interval_seconds must be positive"
            raise ValueError(msg)
        if self.parent_exit_grace_seconds < 0:
            msg = "parent_exit_grace_seconds must be >= 0"
            raise ValueError(msg)
        if self.descendant_wait_timeout_seconds < 0:
            msg = "descendant_wait_timeout_seconds must be >= 0"
            raise ValueError(msg)
        if self.descendant_wait_poll_seconds <= 0:
            msg = "descendant_wait_poll_seconds must be positive"
            raise ValueError(msg)
        if self.process_exit_wait_seconds < 0:
            msg = "process_exit_wait_seconds must be >= 0"
            raise ValueError(msg)


@dataclass
class IdleWatchdog:
    """Tracks agent idle time and decides when to fire the timeout.

    The watchdog owns the last_activity timestamp; the caller's loop must NEVER
    reset last_activity directly — that was the source of the false-negative bug
    where WAITING_ON_CHILD resets deferred the deadline forever.

    Cumulative WAITING_ON_CHILD time is preserved across heartbeat-style activity
    bursts so the max_waiting_on_child_seconds ceiling cannot be defeated by a
    process that alternates between producing output and waiting on children.
    Cumulative is only reset after a sustained-active interval (>= drain_window_seconds
    without a WAITING transition); see _decay_waiting_cumulative_if_quiet().

    The session ceiling (max_session_seconds) is checked first on every evaluate()
    call and cannot be defeated by activity — record_activity() does not reset it.
    """

    _config: TimeoutPolicy
    _clock: Clock
    _last_activity: float = field(init=False)
    _session_started_at: float = field(init=False)
    _waiting_on_child_started_at: float | None = field(default=None, init=False)
    _cumulative_waiting_on_child_seconds: float = field(default=0.0, init=False)
    _in_drain_window: bool = field(default=False, init=False)
    _drain_started_at: float | None = field(default=None, init=False)
    _last_fire_reason: WatchdogFireReason | None = field(default=None, init=False)
    _last_activity_decay_anchor: float | None = field(default=None, init=False)

    def __init__(self, config: TimeoutPolicy, clock: Clock) -> None:
        self._config = config
        self._clock = clock
        now = clock.monotonic()
        self._last_activity = now
        self._session_started_at = now
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None
        self._last_activity_decay_anchor = None
        self._log = logger.bind(component="idle_watchdog")

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        """The reason the watchdog fired, or None if it hasn't fired yet."""
        return self._last_fire_reason

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        """Cumulative seconds spent in WAITING_ON_CHILD state across all runs."""
        return self._cumulative_waiting_on_child_seconds

    def record_activity(self) -> None:
        """Record that the agent produced output; resets idle/drain/child state.

        Does NOT reset _session_started_at — the session ceiling is absolute and
        cannot be defeated by heartbeat activity.

        Does NOT reset _cumulative_waiting_on_child_seconds — cumulative is an
        absolute ceiling that survives heartbeats. It is only reset after a
        sustained-active interval (>= drain_window_seconds without WAITING); see
        _decay_waiting_cumulative_if_quiet() called from the CONTINUE path of evaluate().
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._last_activity_decay_anchor = now
        self._in_drain_window = False
        self._drain_started_at = None

    def _accumulate_waiting_run(self, now: float) -> None:
        """Add elapsed time from the current WAITING run to the cumulative total.

        Called on every transition OUT of the WAITING_ON_CHILD state so the
        cumulative total is preserved across WAITING<->ACTIVE oscillation.
        Double-counting is prevented by only calling this on transitions (not on
        consecutive WAITING evaluations).
        """
        if self._waiting_on_child_started_at is not None:
            elapsed = now - self._waiting_on_child_started_at
            self._cumulative_waiting_on_child_seconds += max(0.0, elapsed)
            self._waiting_on_child_started_at = None

    def _decay_waiting_cumulative_if_quiet(self, now: float) -> None:
        """Reset cumulative WAITING after a sustained active interval.

        Called only from the CONTINUE path (idle_elapsed < idle_timeout).
        Resets cumulative to 0 once the most-recent record_activity() anchor is
        at least drain_window_seconds old without any WAITING transitions.
        This allows genuinely-progressing agents to start each WAITING run fresh.
        """
        if self._last_activity_decay_anchor is None:
            return
        if now - self._last_activity_decay_anchor >= self._config.drain_window_seconds:
            self._cumulative_waiting_on_child_seconds = 0.0
            self._last_activity_decay_anchor = None

    def evaluate(
        self,
        *,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Evaluate whether the watchdog should fire, wait, or continue.

        The session ceiling is checked first (before idle deadline) because it
        is absolute and activity cannot reset it.

        Args:
            classify_quiet: Called only when the idle deadline has elapsed; returns
                the current AgentExecutionState to distinguish child-wait from stall.
                Also called on every drain-window tick to detect newly appearing
                children (which abort the drain and resume deferral).

        Returns:
            CONTINUE: keep running normally.
            WAITING_ON_CHILD: idle deadline elapsed; children still active; last_activity not reset.
            FIRE: idle deadline elapsed with no valid deferral; caller must terminate.
        """
        from ralph.agents.execution_state import AgentExecutionState  # noqa: PLC0415

        now = self._clock.monotonic()

        # Session ceiling check FIRST — activity cannot reset this.
        if self._config.max_session_seconds is not None:
            session_elapsed = now - self._session_started_at
            if session_elapsed >= self._config.max_session_seconds:
                self._last_fire_reason = WatchdogFireReason.SESSION_CEILING_EXCEEDED
                idle_elapsed = now - self._last_activity
                self._log.warning(
                    "idle watchdog: FIRE reason={} session_elapsed={}s"
                    " idle_elapsed={}s cumulative_waiting={}s",
                    WatchdogFireReason.SESSION_CEILING_EXCEEDED,
                    round(session_elapsed, 1),
                    round(idle_elapsed, 1),
                    round(self._cumulative_waiting_on_child_seconds, 1),
                )
                return WatchdogVerdict.FIRE

        if self._config.idle_timeout_seconds is None:
            return WatchdogVerdict.CONTINUE

        idle_elapsed = now - self._last_activity

        if idle_elapsed < self._config.idle_timeout_seconds:
            # Still within deadline — clear any lingering child-wait state and
            # decay cumulative if we have had a sustained active interval.
            self._accumulate_waiting_run(now)
            self._decay_waiting_cumulative_if_quiet(now)
            return WatchdogVerdict.CONTINUE

        # Idle deadline has elapsed.
        if self._in_drain_window:
            return self._handle_drain_window(now, classify_quiet)

        # Consult classify_quiet to determine whether to defer or fire.
        quiet_state = classify_quiet()

        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            return self._handle_waiting_branch(now)

        return self._handle_active_branch(now)

    def _handle_drain_window(
        self,
        now: float,
        classify_quiet: Callable[[], AgentExecutionState],
    ) -> WatchdogVerdict:
        """Handle evaluation while in the drain window.

        Re-consults classify_quiet on every tick. If children appear during the
        drain window, the drain is abandoned and we fall back to WAITING_ON_CHILD
        deferral to prevent false-positive fires while children are alive.
        """
        from ralph.agents.execution_state import AgentExecutionState  # noqa: PLC0415

        assert self._drain_started_at is not None

        quiet_state = classify_quiet()
        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
            # Children appeared during drain — abandon drain and defer instead.
            self._in_drain_window = False
            self._drain_started_at = None
            self._log.info(
                "idle watchdog: drain window abandoned"
                " (children reappeared), switching to WAITING_ON_CHILD"
            )
            return self._handle_waiting_branch(now)

        drain_elapsed = now - self._drain_started_at
        if drain_elapsed < self._config.drain_window_seconds:
            self._log.debug(
                "idle watchdog: drain window active drain_elapsed={}s window={}s",
                round(drain_elapsed, 3),
                self._config.drain_window_seconds,
            )
            return WatchdogVerdict.CONTINUE

        # Drain window exhausted — fire.
        idle_elapsed = now - self._last_activity
        self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
        )
        return WatchdogVerdict.FIRE

    def _handle_waiting_branch(self, now: float) -> WatchdogVerdict:
        """Handle the WAITING_ON_CHILD deferral branch.

        Accumulates time within the current run WITHOUT mutating the cumulative
        total (which is only updated on transition out of WAITING). The ceiling
        check uses cumulative + current-run total to avoid double-counting.
        """
        idle_elapsed = now - self._last_activity
        if self._waiting_on_child_started_at is None:
            # Transition INTO WAITING_ON_CHILD state.
            self._waiting_on_child_started_at = now
            self._log.info(
                "idle watchdog: entering WAITING_ON_CHILD deferral idle_elapsed={}s cumulative={}s",
                round(idle_elapsed, 1),
                round(self._cumulative_waiting_on_child_seconds, 1),
            )

        current_run_elapsed = now - self._waiting_on_child_started_at
        candidate_total = self._cumulative_waiting_on_child_seconds + current_run_elapsed

        if candidate_total >= self._config.max_waiting_on_child_seconds:
            self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
            self._log.warning(
                "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
                WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
                round(idle_elapsed, 1),
                round(candidate_total, 1),
            )
            return WatchdogVerdict.FIRE

        self._log.debug(
            "idle watchdog: WAITING_ON_CHILD deferred cumulative_candidate={}s ceiling={}s",
            round(candidate_total, 3),
            self._config.max_waiting_on_child_seconds,
        )
        return WatchdogVerdict.WAITING_ON_CHILD

    def _handle_active_branch(self, now: float) -> WatchdogVerdict:
        """Handle the case where the agent appears active (no children visible).

        Accumulates any elapsed WAITING run time before entering the drain window.
        When drain_window_seconds=0, fires immediately without a drain window.
        """
        idle_elapsed = now - self._last_activity
        self._accumulate_waiting_run(now)
        if self._config.drain_window_seconds == 0.0:
            self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
            self._log.warning(
                "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
                WatchdogFireReason.NO_OUTPUT_DEADLINE,
                round(idle_elapsed, 1),
                round(self._cumulative_waiting_on_child_seconds, 1),
            )
            return WatchdogVerdict.FIRE
        self._in_drain_window = True
        self._drain_started_at = now
        self._log.info(
            "idle watchdog: entering drain window idle_elapsed={}s cumulative_waiting={}s",
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
        )
        return WatchdogVerdict.CONTINUE
