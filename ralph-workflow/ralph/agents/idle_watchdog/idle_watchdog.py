"""Idle watchdog for detecting stalled agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.process.child_liveness import AliveBy

from .corroboration_snapshot import CorroborationSnapshot, WaitingCorroborator
from .waiting_status_event import WaitingStatusEvent, WaitingStatusListener
from .waiting_status_kind import WaitingStatusKind
from .watchdog_fire_reason import WatchdogFireReason
from .watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from .timeout_policy import TimeoutPolicy

    class Clock(Protocol):
        def monotonic(self) -> float: ...


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
    # Post-tool-result progression state. The watchdog tracks when a
    # TOOL_RESULT activity was last recorded and whether we are still
    # waiting for the follow-up STREAM_DELTA/OUTPUT_LINE activity. When
    # ``_awaiting_post_tool_result_progression`` is True and the
    # configured ``post_tool_result_progression_seconds`` budget elapses
    # without a follow-up activity, the watchdog fires
    # STALLED_AFTER_TOOL_RESULT. This is a NEW BEHAVIOR: pre-fix, the
    # watchdog only fired NO_OUTPUT_DEADLINE at the full idle timeout,
    # which let the post-tool-result wedge linger for ~300s.
    _last_tool_result_at: float | None = field(default=None, init=False)
    _awaiting_post_tool_result_progression: bool = field(default=False, init=False)

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
        self._last_tool_result_at = None
        self._awaiting_post_tool_result_progression = False
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

        Clears the post-tool-result awaiting flag so a follow-up
        OUTPUT_LINE/STREAM_DELTA does not appear to be the post-tool-result
        progression activity (the flag is set by
        ``record_tool_result_activity()`` only).
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None
        self._awaiting_post_tool_result_progression = False

    def record_tool_result_activity(self) -> None:
        """Record that a TOOL_RESULT activity was observed.

        Sets the awaiting flag and records the timestamp. The next
        ``evaluate()`` call checks whether a follow-up activity
        (OUTPUT_LINE/STREAM_DELTA/TOOL_USE/LIFECYCLE) arrives within
        the configured ``post_tool_result_progression_seconds`` budget.
        If not, the watchdog fires STALLED_AFTER_TOOL_RESULT.

        This is a NEW BEHAVIOR for direct wedge detection. The
        existing ``pty_line_reader._handle_queued_line`` calls this
        method AFTER ``record_activity()`` on the TOOL_RESULT branch
        so the wedge is detected in ~120s by default (the
        post-tool-result budget) rather than waiting for the full
        300s idle timeout.

        Does NOT reset _session_started_at (the session ceiling
        remains absolute).
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._last_activity = now
        self._in_drain_window = False
        self._drain_started_at = None
        self._last_tool_result_at = now
        self._awaiting_post_tool_result_progression = True

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
            isinstance(ws_delta, int | float)
            and ws_delta == 0
            and isinstance(oldest, int | float)
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
        now = self._clock.monotonic()

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
            self._accumulate_waiting_run(now)
            return WatchdogVerdict.CONTINUE

        if self._in_drain_window:
            verdict = self._handle_drain_window(now, classify_quiet)
        elif self._post_tool_result_stalled(now, idle_elapsed):
            verdict = WatchdogVerdict.FIRE
        else:
            quiet_state = classify_quiet()
            if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
                verdict = self._handle_waiting_branch(now)
            else:
                verdict = self._handle_active_branch(now)
        return verdict

    def _post_tool_result_stalled(self, now: float, idle_elapsed: float) -> bool:
        """Return True when post-tool-result progression has stalled long enough to fire."""
        if (
            self._config.post_tool_result_progression_seconds is None
            or not self._awaiting_post_tool_result_progression
            or self._last_tool_result_at is None
        ):
            return False
        since_tool_result = now - self._last_tool_result_at
        if since_tool_result < self._config.post_tool_result_progression_seconds:
            return False
        self._last_fire_reason = WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
        self._log.warning(
            "idle watchdog: FIRE reason={} since_tool_result={}s"
            " idle_elapsed={}s cumulative_waiting={}s",
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            round(since_tool_result, 1),
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
        )
        return True

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
        assert self._drain_started_at is not None

        quiet_state = classify_quiet()
        if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
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

        idle_elapsed = now - self._last_activity
        self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            round(idle_elapsed, 1),
            round(self._cumulative_waiting_on_child_seconds, 1),
        )
        return WatchdogVerdict.FIRE

    _NON_PROGRESS_ALIVE_BY_VALUES = frozenset(
        [
            AliveBy.FRESH_HEARTBEAT_ONLY,
            AliveBy.STALE_LABEL_ONLY,
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        ]
    )

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
            return self._config.max_waiting_on_child_seconds

        if alive_by == AliveBy.FRESH_PROGRESS:
            return self._config.max_waiting_on_child_seconds

        if alive_by in self._NON_PROGRESS_ALIVE_BY_VALUES:
            return self._config.max_waiting_on_child_no_progress_seconds

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
        current_corr = self._safe_corroborate()
        effective_ceiling = self._effective_waiting_ceiling(current_corr)

        if candidate_total >= effective_ceiling:
            self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
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
            for key, value in corr_diag_hs.items():
                if key not in diag:
                    diag[key] = value
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

        if (
            self._config.suspect_waiting_on_child_seconds is not None
            and not self._suspicion_announced_for_run
            and candidate_total >= self._config.suspect_waiting_on_child_seconds
        ):
            self._suspicion_announced_for_run = True
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

        assert self._last_waiting_status_at is not None
        if now - self._last_waiting_status_at >= self._config.waiting_status_interval_seconds:
            self._last_waiting_status_at = now
            corr_diag_pr = self._build_corroboration_diag(current_corr)
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
