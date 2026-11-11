
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


def _alive_by_is_fresh(alive_by: AliveBy | None) -> bool:
    return alive_by in _FRESH_ALIVE_BY_STATES


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
            "ceiling": self._config.no_progress_quiet_heartbeat_ceiling_seconds,
            "effective_ceiling": "heartbeat_only",
        }
        corr_diag = self._build_corroboration_diag(corroboration)
        for key, val in corr_diag.items():
            if key not in diag:
                diag[key] = val
        evidence_block, _ = self._build_evidence_summary_diag(now)
        for ev_key, ev_val in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_val
        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=round(self.invocation_elapsed_seconds, 1),
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.no_progress_quiet_heartbeat_ceiling_seconds,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} effective_ceiling=heartbeat_only"
            " idle_elapsed={}s invocation_elapsed={}s heartbeat_ceiling={}s",
            WatchdogFireReason.NO_PROGRESS_QUIET,
            round(idle_elapsed, 1),
            round(self.invocation_elapsed_seconds, 1),
            self._config.no_progress_quiet_heartbeat_ceiling_seconds,
        )
        return WatchdogVerdict.FIRE

    if self._config.no_progress_quiet_seconds is None:
        return None
    if self.invocation_elapsed_seconds < self._config.no_progress_quiet_seconds:
        return None
    if idle_elapsed < self._config.no_progress_quiet_seconds:
        return None

    if not self._is_no_progress_quiet(now, corroboration):
        return None

    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_PROGRESS_QUIET, now=now, idle_elapsed=idle_elapsed
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE

    self._last_fire_reason = WatchdogFireReason.NO_PROGRESS_QUIET
    self._last_alive_by = corroboration.alive_by
    diag = {
        "cumulative": round(self._cumulative_waiting_on_child_seconds, 1),
        "invocation_elapsed": round(self.invocation_elapsed_seconds, 1),
        "idle_elapsed": round(idle_elapsed, 1),
        "ceiling": self._config.no_progress_quiet_seconds,
        "effective_ceiling": "no_progress_quiet",
    }
    corr_diag = self._build_corroboration_diag(corroboration)
    for key, val in corr_diag.items():
        if key not in diag:
            diag[key] = val
    evidence_block, _ = self._build_evidence_summary_diag(now)
    for ev_key, ev_val in evidence_block.items():
        if ev_key not in diag:
            diag[ev_key] = ev_val

    self._emit(
        WaitingStatusKind.HARD_STOP,
        current_run_seconds=round(self.invocation_elapsed_seconds, 1),
        idle_elapsed=idle_elapsed,
        ceiling_seconds=self._config.no_progress_quiet_seconds,
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
    if self._config.no_progress_quiet_strictly_stuck_seconds is None:
        self._strictly_stuck_run_started_at = None
        return None
    _strictly_stuck_alive_by = (
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
    )
    if corroboration.alive_by not in _strictly_stuck_alive_by:
        self._strictly_stuck_run_started_at = None
        return None
    if self._strictly_stuck_run_started_at is None:
        self._strictly_stuck_run_started_at = now
        return None
    strictly_stuck_run_seconds = now - self._strictly_stuck_run_started_at
    if strictly_stuck_run_seconds < self._config.no_progress_quiet_strictly_stuck_seconds:
        return None
    if self._channel_evidence_active(now):
        return None
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
        "strictly_stuck_ceiling_seconds": (self._config.no_progress_quiet_strictly_stuck_seconds),
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
        ceiling_seconds=self._config.no_progress_quiet_strictly_stuck_seconds,
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
    if (
        self._config.no_output_at_start_seconds is None
        or self._has_meaningful_output
        or self._last_meaningful_output_at is None
        or (now - self._last_meaningful_output_at) < self._config.no_output_at_start_seconds
    ):
        return None
    try:
        quiet_state = classify_quiet()
    except Exception:
        quiet_state = AgentExecutionState.ACTIVE
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return None
    if quiet_state not in {
        AgentExecutionState.ACTIVE,
        AgentExecutionState.WAITING_ON_CHILD,
    }:
        return None
    if self._channel_evidence_active(now):
        return None
    corroboration = self._safe_corroborate()
    if _alive_by_is_fresh(corroboration.alive_by):
        return None
    if self._cumulative_waiting_on_child_seconds > 0.0:
        return None

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
