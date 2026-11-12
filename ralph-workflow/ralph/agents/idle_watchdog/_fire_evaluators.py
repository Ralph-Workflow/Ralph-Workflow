from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.process.child_liveness import AliveBy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog

_FRESH_ALIVE_BY_STATES: frozenset[AliveBy] = frozenset(
    {AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY}
)

_STRICTLY_STUCK_ALIVE_BY_VALUES: frozenset[AliveBy] = frozenset(
    {
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
    }
)


def _alive_by_is_fresh(alive_by: AliveBy | None) -> bool:
    return alive_by in _FRESH_ALIVE_BY_STATES


def _quiet_state_or_default(
    classify_quiet: Callable[[], AgentExecutionState],
    default: AgentExecutionState,
) -> AgentExecutionState:
    try:
        return classify_quiet()
    except Exception:
        return default


def _merge_diagnostics(
    self: IdleWatchdog,
    now: float,
    corroboration: CorroborationSnapshot,
    diag: dict[str, object],
) -> None:
    corr_diag = self._build_corroboration_diag(corroboration)
    for key, val in corr_diag.items():
        if key not in diag:
            diag[key] = val
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val


def is_no_progress_quiet(
    self: IdleWatchdog, now: float, corroboration: CorroborationSnapshot
) -> bool:
    if self._config.no_progress_quiet_seconds is None:
        return False
    if (
        corroboration.alive_by == AliveBy.FRESH_HEARTBEAT_ONLY
        and self._config.no_progress_quiet_heartbeat_ceiling_seconds is not None
        and self.invocation_elapsed_seconds
        >= self._config.no_progress_quiet_heartbeat_ceiling_seconds
        and (
            self._config.no_progress_quiet_minimum_invocation_seconds is None
            or self.invocation_elapsed_seconds
            >= self._config.no_progress_quiet_minimum_invocation_seconds
        )
    ):
        return True
    if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
        return False
    if (
        self._config.no_progress_quiet_minimum_invocation_seconds is not None
        and self.invocation_elapsed_seconds
        < self._config.no_progress_quiet_minimum_invocation_seconds
    ):
        return False
    if corroboration.alive_by is not None:
        return False
    return not self._channel_evidence_active(now)


def evaluate_no_progress_quiet(
    self: IdleWatchdog, now: float, idle_elapsed: float
) -> WatchdogVerdict | None:
    if (
        self._config.no_progress_quiet_seconds is None
        and self._config.no_progress_quiet_heartbeat_ceiling_seconds is None
    ):
        return None

    corroboration = self._safe_corroborate()
    heartbeat_verdict = _heartbeat_only_verdict(self, now, idle_elapsed, corroboration)
    if heartbeat_verdict is not None:
        return heartbeat_verdict

    quiet_seconds = _no_progress_quiet_thresholds_met(self, now, idle_elapsed, corroboration)
    if quiet_seconds is None:
        return None

    return _no_progress_quiet_fire_verdict(self, now, idle_elapsed, corroboration, quiet_seconds)


def _no_progress_quiet_thresholds_met(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
) -> float | None:
    quiet_seconds = self._config.no_progress_quiet_seconds
    if quiet_seconds is None:
        return None
    if self.invocation_elapsed_seconds < quiet_seconds:
        return None
    if idle_elapsed < quiet_seconds:
        return None
    if not self._is_no_progress_quiet(now, corroboration):
        return None
    return quiet_seconds


def _heartbeat_only_verdict(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
) -> WatchdogVerdict | None:
    ceiling = self._config.no_progress_quiet_heartbeat_ceiling_seconds
    if ceiling is None:
        return None
    if corroboration.alive_by != AliveBy.FRESH_HEARTBEAT_ONLY:
        return None
    if self.invocation_elapsed_seconds < ceiling:
        return None
    minimum = self._config.no_progress_quiet_minimum_invocation_seconds
    if minimum is not None and self.invocation_elapsed_seconds < minimum:
        return None

    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_PROGRESS_QUIET,
        now=now,
        idle_elapsed=idle_elapsed,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
    self._last_alive_by = corroboration.alive_by
    diag: dict[str, object] = {
        "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        "ceiling": ceiling,
        "effective_ceiling": "heartbeat_only",
    }
    _merge_diagnostics(self, now, corroboration, diag)
    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=ceiling,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._log.warning(
        "idle watchdog: FIRE reason={} effective_ceiling=heartbeat_only"
        " idle_elapsed={}s invocation_elapsed={}s heartbeat_ceiling={}s",
        WatchdogFireReason.NO_PROGRESS_QUIET,
        round(idle_elapsed, 1),
        round(self.invocation_elapsed_seconds, 1),
        ceiling,
    )
    return WatchdogVerdict.FIRE


def _no_progress_quiet_fire_verdict(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
    quiet_seconds: float,
) -> WatchdogVerdict:
    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_PROGRESS_QUIET, now=now, idle_elapsed=idle_elapsed
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE

    self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
    self._last_alive_by = corroboration.alive_by
    diag: dict[str, object] = {
        "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        "ceiling": quiet_seconds,
        "effective_ceiling": "no_progress_quiet",
    }
    _merge_diagnostics(self, now, corroboration, diag)

    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=quiet_seconds,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._log.warning(
        "idle watchdog: FIRE reason={} idle_elapsed={}s invocation_elapsed={}s",
        WatchdogFireReason.NO_PROGRESS_QUIET,
        round(idle_elapsed, 1),
        round(self.invocation_elapsed_seconds, 1),
    )
    return WatchdogVerdict.FIRE


def evaluate_strictly_stuck(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
) -> WatchdogVerdict | None:
    ceiling = self._config.no_progress_quiet_strictly_stuck_seconds
    if ceiling is None:
        self._strictly_stuck_run_started_at = None
        return None
    if corroboration.alive_by not in _STRICTLY_STUCK_ALIVE_BY_VALUES:
        self._strictly_stuck_run_started_at = None
        return None
    if self._strictly_stuck_run_started_at is None:
        self._strictly_stuck_run_started_at = now
        return None
    strictly_stuck_run_seconds = now - self._strictly_stuck_run_started_at
    if strictly_stuck_run_seconds < ceiling:
        return None
    if self._channel_evidence_active(now):
        return None
    return _strictly_stuck_fire_verdict(
        self, now, idle_elapsed, corroboration, strictly_stuck_run_seconds, ceiling
    )


def _strictly_stuck_fire_verdict(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
    strictly_stuck_run_seconds: float,
    ceiling: float,
) -> WatchdogVerdict:
    gate_verdict = self._gate_fire(
        WatchdogFireReason.STRICTLY_STUCK,
        now=now,
        idle_elapsed=idle_elapsed,
        corroboration=corroboration,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.STRICTLY_STUCK
    diag: dict[str, object] = {
        "alive_by": corroboration.alive_by.value if corroboration.alive_by is not None else None,
        "strictly_stuck_run_seconds": round(strictly_stuck_run_seconds, 1),
        "strictly_stuck_ceiling_seconds": ceiling,
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        "scoped_child_active": (
            corroboration.scoped_child_active
            if corroboration.scoped_child_active is not None
            else False
        ),
    }
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val
    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(strictly_stuck_run_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=ceiling,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._log.warning(
        "idle watchdog: FIRE reason={} idle_elapsed={}s strictly_stuck_run_seconds={}s alive_by={}",
        WatchdogFireReason.STRICTLY_STUCK,
        round(idle_elapsed, 1),
        round(strictly_stuck_run_seconds, 1),
        corroboration.alive_by,
    )
    return WatchdogVerdict.FIRE


def evaluate_no_output_at_start(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict | None:
    if not _no_output_at_start_window_open(self, now):
        return None
    quiet_state = _quiet_state_or_default(classify_quiet, AgentExecutionState.ACTIVE)
    if quiet_state != AgentExecutionState.ACTIVE:
        return None
    if self._channel_evidence_active(now):
        return None
    corroboration = self._safe_corroborate()
    if (
        _alive_by_is_fresh(corroboration.alive_by)
        or self._cumulative_waiting_on_child_seconds > 0.0
    ):
        return None
    return _no_output_at_start_fire_verdict(self, now, idle_elapsed, corroboration)


def _no_output_at_start_window_open(self: IdleWatchdog, now: float) -> bool:
    threshold = self._config.no_output_at_start_seconds
    if threshold is None:
        return False
    if self._has_meaningful_output:
        return False
    last_meaningful_output_at = self._last_meaningful_output_at
    if last_meaningful_output_at is None:
        return False
    return (now - last_meaningful_output_at) >= threshold


def _no_output_at_start_fire_verdict(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot,
) -> WatchdogVerdict:
    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_OUTPUT_AT_START,
        now=now,
        idle_elapsed=idle_elapsed,
        corroboration=corroboration,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE

    self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_AT_START
    diag: dict[str, object] = {
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "no_output_at_start_seconds": self._config.no_output_at_start_seconds,
        "last_activity_equals_started_at": True,
        "scoped_child_active": (
            corroboration.scoped_child_active
            if corroboration.scoped_child_active is not None
            else False
        ),
    }
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val

    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=self._config.no_output_at_start_seconds,
        diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
    )
    self._emit_fire_log(
        WatchdogFireReason.NO_OUTPUT_AT_START,
        now=now,
        idle_elapsed=idle_elapsed,
        message_suffix=(f" no_output_at_start_seconds={self._config.no_output_at_start_seconds}s"),
    )
    return WatchdogVerdict.FIRE
