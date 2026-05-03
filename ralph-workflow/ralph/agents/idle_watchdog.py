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

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger

from ralph.timeout_defaults import (
    DESCENDANT_WAIT_POLL_SECONDS,
    DESCENDANT_WAIT_TIMEOUT_SECONDS,
    DRAIN_WINDOW_SECONDS,
    IDLE_POLL_INTERVAL_SECONDS,
    MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
    MAX_WAITING_ON_CHILD_SECONDS,
    PARENT_EXIT_GRACE_SECONDS,
    PROCESS_EXIT_WAIT_SECONDS,
    SUSPECT_WAITING_ON_CHILD_SECONDS,
    WAITING_STATUS_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.timeout_clock import Clock

__all__ = [
    "AliveBy",
    "CorroborationSnapshot",
    "IdleWatchdog",
    "TimeoutPolicy",
    "WaitingCorroborator",
    "WaitingStatusEvent",
    "WaitingStatusKind",
    "WaitingStatusListener",
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


class WaitingStatusKind(StrEnum):
    """Kind of waiting-status event emitted by IdleWatchdog.

    ENTERED: transition into WAITING_ON_CHILD deferral.
    PROGRESS: periodic status update while still waiting (rate-limited).
    SUSPECTED_FROZEN: cumulative wait crossed suspect threshold; child may be frozen.
    EXITED: transition out of WAITING_ON_CHILD (activity or drain resumed).
    HARD_STOP: cumulative ceiling crossed; watchdog about to fire CHILDREN_PERSIST_TOO_LONG.
    """

    ENTERED = "entered"
    PROGRESS = "progress"
    SUSPECTED_FROZEN = "suspected_frozen"
    EXITED = "exited"
    HARD_STOP = "hard_stop"


class AliveBy(StrEnum):
    """Typed corroboration reasons describing why child work still appears alive."""

    FRESH_PROGRESS = "fresh_progress"
    FRESH_HEARTBEAT_ONLY = "fresh_heartbeat_only"
    STALE_LABEL_ONLY = "stale_label_only"
    OS_DESCENDANT_ONLY_STALE_PROGRESS = "os_descendant_only_stale_progress"


@dataclass(frozen=True)
class WaitingStatusEvent:
    """Structured status event emitted by IdleWatchdog during WAITING_ON_CHILD deferral.

    This dataclass is frozen so subscribers cannot accidentally mutate shared state.

    The ``diagnostic`` dict is a forward-compatible extension point for Phase 3
    corroborating signals (workspace_event_delta, oldest_child_seconds,
    scoped_child_active, etc.). This plan ships only the throttle, transition,
    suspicion, and hard-stop summary semantics; Phase 3 fields are out of scope.

    Attributes:
        kind: The type of event (ENTERED, PROGRESS, SUSPECTED_FROZEN, EXITED, HARD_STOP).
        cumulative_seconds: Cumulative WAITING_ON_CHILD seconds across the session so far.
        current_run_seconds: Seconds spent in the current WAITING_ON_CHILD run.
        idle_elapsed_seconds: Seconds since last record_activity() call.
        ceiling_seconds: The active WAITING_ON_CHILD ceiling for this event.
        suspect_threshold_seconds: The suspect_waiting_on_child_seconds threshold, or None.
        diagnostic: Optional dict of extra diagnostic keys for HARD_STOP events.
    """

    kind: WaitingStatusKind
    cumulative_seconds: float
    current_run_seconds: float
    idle_elapsed_seconds: float
    ceiling_seconds: float
    suspect_threshold_seconds: float | None
    diagnostic: dict[str, str | int | float | bool] = field(default_factory=dict)


#: Listener callable type for waiting-status events.
WaitingStatusListener = Callable[[WaitingStatusEvent], None]


@dataclass(frozen=True)
class CorroborationSnapshot:
    """Advisory snapshot of corroborating signals for WAITING_ON_CHILD diagnosis.

    All fields are Optional so callers without a given source can leave them None.
    Corroborators are advisory only; they NEVER affect WatchdogVerdict. The hard
    stop is determined solely by max_waiting_on_child_seconds and max_session_seconds.
    """

    workspace_event_count: int | None = None
    oldest_child_seconds: float | None = None
    scoped_child_active: bool | None = None
    scoped_child_count: int | None = None
    terminal_child_events_total: int | None = None
    last_activity_was_meaningful: bool | None = None
    alive_by: AliveBy | None = None


#: Corroborator callable type — advisory only, never changes the watchdog verdict.
WaitingCorroborator = Callable[[], CorroborationSnapshot]


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
       absolute ceiling across the session and never decays.
    4. PROCESS_EXIT_HANG — subprocess closed stdout but did not exit within budget.
    5. DESCENDANT_HANG — descendant-wait deadline elapsed with persistent WAITING_ON_CHILD
       (post-exit only, owned by PostExitWatchdog).

    Suspicion is purely informational and does NOT affect any fire condition. The
    ``suspect_waiting_on_child_seconds`` threshold exists only to emit an elevated
    warning event before the hard stop; crossing it never shortens the hard-stop
    ceiling.

    Attributes:
        idle_timeout_seconds: Maximum seconds without output before watchdog may fire.
            None disables the idle-timeout watchdog entirely.
        drain_window_seconds: After a potential timeout, the watchdog enters a drain
            window of this duration to allow late output to flush.
        max_waiting_on_child_seconds: Hard cumulative ceiling on time spent in
            WAITING_ON_CHILD state across the entire session. Activity cannot decay
            or reset it; once exceeded, fires CHILDREN_PERSIST_TOO_LONG even while
            children are still alive.
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
        waiting_status_interval_seconds: How often to emit a PROGRESS status event
            while WAITING_ON_CHILD deferral is active. Controls only the status
            emission cadence; does NOT affect timeout safety or ceiling math.
        suspect_waiting_on_child_seconds: Cumulative WAITING time after which a
            SUSPECTED_FROZEN event is emitted. Purely informational — does NOT
            shorten the hard-stop ceiling or change the watchdog verdict.
            Must be strictly less than max_waiting_on_child_seconds when set.
            None disables suspicion events.
        max_waiting_on_child_no_progress_seconds: Hard ceiling on cumulative
            WAITING_ON_CHILD time when corroboration shows the child is alive but
            not making progress (e.g., heartbeat-only, stale-label, or OS-descendant-only
            evidence). When set, must be <= max_waiting_on_child_seconds. When None,
            the no-progress ceiling is disabled and max_waiting_on_child_seconds is
            used for all WAITING_ON_CHILD states.
    """

    idle_timeout_seconds: float | None
    drain_window_seconds: float = DRAIN_WINDOW_SECONDS
    max_waiting_on_child_seconds: float = MAX_WAITING_ON_CHILD_SECONDS
    max_session_seconds: float | None = None
    idle_poll_interval_seconds: float = IDLE_POLL_INTERVAL_SECONDS
    parent_exit_grace_seconds: float = PARENT_EXIT_GRACE_SECONDS
    descendant_wait_timeout_seconds: float = DESCENDANT_WAIT_TIMEOUT_SECONDS
    descendant_wait_poll_seconds: float = DESCENDANT_WAIT_POLL_SECONDS
    process_exit_wait_seconds: float = PROCESS_EXIT_WAIT_SECONDS
    waiting_status_interval_seconds: float = WAITING_STATUS_INTERVAL_SECONDS
    suspect_waiting_on_child_seconds: float | None = SUSPECT_WAITING_ON_CHILD_SECONDS
    max_waiting_on_child_no_progress_seconds: float | None = (
        MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS
    )

    def __post_init__(self) -> None:
        self._validate_idle_fields()
        self._validate_session_and_poll_fields()
        self._validate_waiting_status_fields()

    def _validate_idle_fields(self) -> None:
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

    def _validate_session_and_poll_fields(self) -> None:
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

    def _validate_waiting_status_fields(self) -> None:
        if self.waiting_status_interval_seconds <= 0:
            msg = "waiting_status_interval_seconds must be positive"
            raise ValueError(msg)
        if self.suspect_waiting_on_child_seconds is not None:
            if self.suspect_waiting_on_child_seconds <= 0:
                msg = "suspect_waiting_on_child_seconds must be positive"
                raise ValueError(msg)
            if self.suspect_waiting_on_child_seconds >= self.max_waiting_on_child_seconds:
                msg = (
                    "suspect_waiting_on_child_seconds must be strictly less than"
                    " max_waiting_on_child_seconds"
                )
                raise ValueError(msg)
        if self.max_waiting_on_child_no_progress_seconds is not None:
            if self.max_waiting_on_child_no_progress_seconds <= 0:
                msg = "max_waiting_on_child_no_progress_seconds must be positive"
                raise ValueError(msg)
            if self.max_waiting_on_child_no_progress_seconds > self.max_waiting_on_child_seconds:
                msg = (
                    "max_waiting_on_child_no_progress_seconds must be <="
                    " max_waiting_on_child_seconds"
                )
                raise ValueError(msg)


@dataclass
class IdleWatchdog:
    """Tracks agent idle time and decides when to fire the timeout.

    The watchdog owns the last_activity timestamp; the caller's loop must NEVER
    mutate `_last_activity` directly. Activity must flow through `record_activity()`,
    which preserves the cumulative WAITING_ON_CHILD ceiling while advancing the
    idle baseline. Direct resets here previously caused a false-negative bug where
    WAITING_ON_CHILD deferred the deadline forever.

    Cumulative WAITING_ON_CHILD time is an absolute ceiling that is preserved across
    every transition (heartbeat activity, drain windows, classify_quiet outcomes).
    Once recorded, cumulative time never decays during the session — this mirrors
    max_session_seconds semantics so neither ceiling can be defeated by a process
    that alternates between producing output and waiting on children.

    The session ceiling (max_session_seconds) is checked first on every evaluate()
    call and cannot be defeated by activity — record_activity() does not reset it.

    Status events are emitted via the optional listener:
    - ENTERED once when WAITING_ON_CHILD deferral begins.
    - PROGRESS at most once per waiting_status_interval_seconds (rate-limited).
    - SUSPECTED_FROZEN once per WAITING run when suspect threshold is crossed.
    - EXITED when transitioning out of WAITING_ON_CHILD.
    - HARD_STOP immediately before returning FIRE for CHILDREN_PERSIST_TOO_LONG.

    Listener exceptions are caught and logged at DEBUG; they never propagate.
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
    _last_waiting_status_at: float | None = field(default=None, init=False)
    _suspicion_announced_for_run: bool = field(default=False, init=False)

    def __init__(
        self,
        config: TimeoutPolicy,
        clock: Clock,
        listener: WaitingStatusListener | None = None,
        *,
        corroborator: WaitingCorroborator | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._listener = listener
        self._corroborator = corroborator
        now = clock.monotonic()
        self._last_activity = now
        self._session_started_at = now
        self._waiting_on_child_started_at = None
        self._cumulative_waiting_on_child_seconds = 0.0
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_fire_reason = None
        self._last_waiting_status_at = None
        self._suspicion_announced_for_run = False
        self._entry_corroboration: CorroborationSnapshot | None = None
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

        Does NOT reset _cumulative_waiting_on_child_seconds. Cumulative is a true
        absolute ceiling (parallel to the session ceiling) and never decays during
        the session.
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None

    def _accumulate_waiting_run(self, now: float) -> None:
        """Add elapsed time from the current WAITING run to the cumulative total.

        Called on every transition OUT of the WAITING_ON_CHILD state so the
        cumulative total is preserved across WAITING<->ACTIVE oscillation.
        Double-counting is prevented by only calling this on transitions (not on
        consecutive WAITING evaluations).

        Emits a EXITED event if we were actually in a WAITING run.
        """
        if self._waiting_on_child_started_at is not None:
            elapsed = now - self._waiting_on_child_started_at
            current_run_elapsed = max(0.0, elapsed)
            idle_elapsed = now - self._last_activity
            self._cumulative_waiting_on_child_seconds += current_run_elapsed
            self._emit(
                WaitingStatusKind.EXITED,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
            )
            self._waiting_on_child_started_at = None
            self._last_waiting_status_at = None
            self._suspicion_announced_for_run = False
            self._entry_corroboration = None

    def _safe_corroborate(self) -> CorroborationSnapshot:
        """Call the corroborator safely, returning an empty snapshot on None or error."""
        if self._corroborator is None:
            return CorroborationSnapshot()
        try:
            return self._corroborator()
        except Exception:
            self._log.debug("idle watchdog: corroborator raised (suppressed)")
            return CorroborationSnapshot()

    def _build_corroboration_diag(
        self,
        current: CorroborationSnapshot,
    ) -> dict[str, str | int | float | bool]:
        """Build a diagnostic dict comparing current corroboration snapshot to entry baseline."""
        diag: dict[str, str | int | float | bool] = {}
        entry = self._entry_corroboration
        if (
            current.workspace_event_count is not None
            and entry is not None
            and entry.workspace_event_count is not None
        ):
            diag["workspace_event_delta"] = (
                current.workspace_event_count - entry.workspace_event_count
            )
        if current.oldest_child_seconds is not None:
            diag["oldest_child_seconds"] = current.oldest_child_seconds
        if current.scoped_child_active is not None:
            diag["scoped_child_active"] = current.scoped_child_active
        if current.scoped_child_count is not None:
            diag["scoped_child_count"] = current.scoped_child_count
        if (
            current.terminal_child_events_total is not None
            and entry is not None
            and entry.terminal_child_events_total is not None
        ):
            diag["terminal_child_events_since_entry"] = (
                current.terminal_child_events_total - entry.terminal_child_events_total
            )
        if current.last_activity_was_meaningful is False:
            diag["lifecycle_only_activity"] = True
        if current.alive_by is not None:
            diag["alive_by"] = current.alive_by
        return diag

    def _build_evidence_string(
        self,
        diag: dict[str, str | int | float | bool],
    ) -> str:
        """Compose a human-readable evidence label for a SUSPECTED_FROZEN event."""
        suspect = self._config.suspect_waiting_on_child_seconds
        tokens: list[str] = []
        ws_delta = diag.get("workspace_event_delta")
        oldest = diag.get("oldest_child_seconds")
        if (
            isinstance(ws_delta, (int, float))
            and ws_delta == 0
            and isinstance(oldest, (int, float))
            and suspect is not None
            and oldest >= suspect
        ):
            tokens.append("time_and_workspace_quiet")
        if diag.get("scoped_child_active") is True:
            tokens.append("time_and_scoped_child_active")
        if diag.get("lifecycle_only_activity") is True:
            tokens.append("time_and_lifecycle_only")
        return "+".join(tokens) if tokens else "time_only"

    def _emit(
        self,
        kind: WaitingStatusKind,
        current_run_seconds: float,
        idle_elapsed: float,
        *,
        ceiling_seconds: float | None = None,
        diagnostic: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        """Build and dispatch a WaitingStatusEvent to the listener.

        Never propagates listener exceptions; logs at DEBUG if one is raised.
        """
        if self._listener is None:
            return
        candidate_total = self._cumulative_waiting_on_child_seconds + current_run_seconds
        event = WaitingStatusEvent(
            kind=kind,
            cumulative_seconds=candidate_total,
            current_run_seconds=current_run_seconds,
            idle_elapsed_seconds=idle_elapsed,
            ceiling_seconds=(
                self._config.max_waiting_on_child_seconds
                if ceiling_seconds is None
                else ceiling_seconds
            ),
            suspect_threshold_seconds=self._config.suspect_waiting_on_child_seconds,
            diagnostic=dict(diagnostic) if diagnostic else {},
        )
        try:
            self._listener(event)
        except Exception:
            self._log.debug("idle watchdog: listener raised (suppressed)")

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
            # Still within deadline — accumulate any pending WAITING run;
            # cumulative is absolute and never decays.
            self._accumulate_waiting_run(now)
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

    # Non-progress alive_by values — child is alive but not making forward progress.
    _NON_PROGRESS_ALIVE_BY_VALUES = frozenset([
        AliveBy.FRESH_HEARTBEAT_ONLY,
        AliveBy.STALE_LABEL_ONLY,
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    ])

    def _effective_waiting_ceiling(
        self,
        corroboration: CorroborationSnapshot,
    ) -> float:
        """Compute the effective waiting ceiling based on corroboration.

        Returns the shorter no-progress ceiling when the child is alive but not
        making forward progress (heartbeat-only, stale-label, or OS-descendant-only).
        Returns the standard full ceiling when the child is making progress or when
        the no-progress ceiling is disabled (None).
        """
        if self._config.max_waiting_on_child_no_progress_seconds is None:
            return self._config.max_waiting_on_child_seconds

        alive_by = corroboration.alive_by
        if alive_by is None:
            # No alive_by means we can't determine progress — use full ceiling (safe default).
            return self._config.max_waiting_on_child_seconds

        if alive_by == AliveBy.FRESH_PROGRESS:
            # Real progress — use full ceiling.
            return self._config.max_waiting_on_child_seconds

        if alive_by in self._NON_PROGRESS_ALIVE_BY_VALUES:
            # Non-progress evidence — use shorter no-progress ceiling.
            return self._config.max_waiting_on_child_no_progress_seconds

        # Unknown alive_by value — fall back to full ceiling.
        return self._config.max_waiting_on_child_seconds

    def _handle_waiting_branch(self, now: float) -> WatchdogVerdict:
        """Handle the WAITING_ON_CHILD deferral branch.

        Accumulates time within the current run WITHOUT mutating the cumulative
        total (which is only updated on transition out of WAITING). The ceiling
        check uses cumulative + current-run total to avoid double-counting.

        Emits structured status events (ENTERED, PROGRESS, SUSPECTED_FROZEN,
        HARD_STOP) rather than per-tick debug spam. Status emission cadence is
        governed by waiting_status_interval_seconds and does NOT affect ceiling math.

        When max_waiting_on_child_no_progress_seconds is set and corroboration shows
        non-progress evidence (heartbeat-only, stale-label, or OS-descendant-only),
        the shorter no-progress ceiling is used instead of the full ceiling.
        """
        idle_elapsed = now - self._last_activity
        if self._waiting_on_child_started_at is None:
            # Transition INTO WAITING_ON_CHILD state — capture entry baseline FIRST.
            self._entry_corroboration = self._safe_corroborate()
            self._waiting_on_child_started_at = now
            self._last_waiting_status_at = now
            self._suspicion_announced_for_run = False
            self._log.info(
                "idle watchdog: entering WAITING_ON_CHILD deferral idle_elapsed={}s cumulative={}s",
                round(idle_elapsed, 1),
                round(self._cumulative_waiting_on_child_seconds, 1),
            )
            entry_ceiling = self._effective_waiting_ceiling(self._entry_corroboration)
            self._emit(
                WaitingStatusKind.ENTERED,
                current_run_seconds=0.0,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=entry_ceiling,
            )

        current_run_elapsed = now - self._waiting_on_child_started_at
        candidate_total = self._cumulative_waiting_on_child_seconds + current_run_elapsed

        # Capture ONE corroboration snapshot per tick and reuse it for the ceiling
        # decision and all same-tick diagnostics to prevent divergence.
        current_corr = self._safe_corroborate()
        effective_ceiling = self._effective_waiting_ceiling(current_corr)

        if candidate_total >= effective_ceiling:
            self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
            # Reuse current_corr for diagnostic - do NOT call corroborate again
            corr_diag_hs = self._build_corroboration_diag(current_corr)
            corr_diag_hs["evidence"] = self._build_evidence_string(corr_diag_hs)
            diag: dict[str, str | int | float | bool] = {
                "cumulative": round(candidate_total, 1),
                "run_elapsed": round(current_run_elapsed, 1),
                "idle_elapsed": round(idle_elapsed, 1),
                "ceiling": effective_ceiling,
                "effective_ceiling": (
                    "no_progress"
                    if effective_ceiling < self._config.max_waiting_on_child_seconds
                    else "standard"
                ),
            }
            if self._config.suspect_waiting_on_child_seconds is not None:
                diag["suspect_threshold"] = self._config.suspect_waiting_on_child_seconds
            for k, v in corr_diag_hs.items():
                if k not in diag:
                    diag[k] = v
            self._emit(
                WaitingStatusKind.HARD_STOP,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                diagnostic=diag,
            )
            self._log.warning(
                "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
                WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
                round(idle_elapsed, 1),
                round(candidate_total, 1),
            )
            return WatchdogVerdict.FIRE

        # Suspicion threshold: emit once per WAITING run, does not change verdict.
        if (
            self._config.suspect_waiting_on_child_seconds is not None
            and not self._suspicion_announced_for_run
            and candidate_total >= self._config.suspect_waiting_on_child_seconds
        ):
            self._suspicion_announced_for_run = True
            # Reuse current_corr snapshot - do NOT call corroborate again
            corr_diag_sf = self._build_corroboration_diag(current_corr)
            corr_diag_sf["evidence"] = self._build_evidence_string(corr_diag_sf)
            self._log.warning(
                "idle watchdog: SUSPECTED_FROZEN candidate_total={}s suspect={}s ceiling={}s",
                round(candidate_total, 1),
                self._config.suspect_waiting_on_child_seconds,
                effective_ceiling,
            )
            self._emit(
                WaitingStatusKind.SUSPECTED_FROZEN,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                diagnostic=corr_diag_sf,
            )

        # Periodic PROGRESS emission — throttled to waiting_status_interval_seconds.
        assert self._last_waiting_status_at is not None
        if now - self._last_waiting_status_at >= self._config.waiting_status_interval_seconds:
            self._last_waiting_status_at = now
            # Reuse current_corr snapshot - do NOT call corroborate again
            corr_diag_pr = self._build_corroboration_diag(current_corr)
            # Include effective ceiling classification in diagnostic for visibility.
            if effective_ceiling < self._config.max_waiting_on_child_seconds:
                corr_diag_pr["effective_ceiling"] = "no_progress"
            self._log.info(
                "idle watchdog: WAITING_ON_CHILD progress cumulative={}s ceiling={}s",
                round(candidate_total, 1),
                round(effective_ceiling, 1),
            )
            self._emit(
                WaitingStatusKind.PROGRESS,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
                diagnostic=corr_diag_pr,
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
