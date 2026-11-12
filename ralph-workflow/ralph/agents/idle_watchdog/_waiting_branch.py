from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._sanitize import _sanitize_subagent_description
from ralph.agents.idle_watchdog._timeout_profile import TimeoutProfile
from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.process.child_liveness import AliveBy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog


def effective_waiting_ceiling(
    self: IdleWatchdog,
    corroboration: CorroborationSnapshot,
) -> float:
    alive_by = corroboration.alive_by
    _effective = self._config.max_waiting_on_child_seconds
    if alive_by is None:
        return _effective
    if alive_by == AliveBy.FRESH_PROGRESS:
        return _effective
    _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
    if _os_desc_only and self._config.os_descendant_only_ceiling_seconds is not None:
        if self._config.max_waiting_on_child_no_progress_seconds is not None:
            _effective = min(
                self._config.os_descendant_only_ceiling_seconds,
                self._config.max_waiting_on_child_no_progress_seconds,
            )
        else:
            _effective = self._config.os_descendant_only_ceiling_seconds
    elif (
        self._config.max_waiting_on_child_no_progress_seconds is not None
        and alive_by in self._NON_PROGRESS_ALIVE_BY_VALUES
    ):
        _effective = self._config.max_waiting_on_child_no_progress_seconds
    return _effective


def effective_ceiling_label(
    self: IdleWatchdog,
    corroboration: CorroborationSnapshot,
    effective_ceiling: float,
) -> str:
    alive_by = corroboration.alive_by
    if alive_by is None:
        return "standard"
    if alive_by == AliveBy.FRESH_PROGRESS:
        return "standard"
    if (
        alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        and self._config.os_descendant_only_ceiling_seconds is not None
        and effective_ceiling == self._config.os_descendant_only_ceiling_seconds
    ):
        return "os_descendant_only"
    if effective_ceiling < self._config.max_waiting_on_child_seconds:
        return "no_progress"
    return "standard"


def compute_effective_suspect(
    self: IdleWatchdog,
    alive_by: AliveBy | None,
    candidate_total: float,
) -> tuple[float | None, str]:
    if self._config.suspect_waiting_on_child_seconds is None:
        return None, "standard"
    _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
    if _os_desc_only and self._config.os_descendant_only_suspect_seconds is not None:
        eff = min(
            self._config.suspect_waiting_on_child_seconds,
            self._config.os_descendant_only_suspect_seconds,
        )
        return eff, "os_descendant_only"
    return self._config.suspect_waiting_on_child_seconds, "standard"


def handle_waiting_branch(
    self: IdleWatchdog,
    now: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
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

    try:
        current_quiet_state = classify_quiet()
    except Exception:
        current_quiet_state = AgentExecutionState.WAITING_ON_CHILD
    if current_quiet_state != AgentExecutionState.WAITING_ON_CHILD:
        self._accumulate_waiting_run(now)
        if self._channel_evidence_active(now):
            return self._handle_evidence_deferral(now, idle_elapsed)
        return self._handle_active_branch(now)

    current_corr = self._safe_corroborate()
    effective_ceiling = self._effective_waiting_ceiling(current_corr)

    alive_by = current_corr.alive_by
    _os_desc_only = alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
    _os_desc_only_suspect = (
        self._config.os_descendant_only_suspect_seconds is not None if _os_desc_only else False
    )
    effective_suspect, suspect_reason = self._compute_effective_suspect(alive_by, candidate_total)

    if (
        self._config.stuck_job_sub_ceiling_seconds is not None
        and current_corr.scoped_child_active
        and current_corr.alive_by in self._STUCK_ALIVE_BY_VALUES
        and candidate_total >= self._config.stuck_job_sub_ceiling_seconds
    ):
        gate_verdict = self._gate_fire(
            WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            now=now,
            idle_elapsed=idle_elapsed,
            corroboration=current_corr,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
        corr_diag_sj = self._build_corroboration_diag(current_corr)
        corr_diag_sj["evidence"] = self._build_evidence_string(corr_diag_sj)
        _ceiling_lbl_sj = self._effective_ceiling_label(current_corr, effective_ceiling)
        diag_sj: dict[str, object] = {
            "cumulative": round(candidate_total, 1),
            "run_elapsed": round(current_run_elapsed, 1),
            "idle_elapsed": round(idle_elapsed, 1),
            "effective_ceiling": effective_ceiling,
            "effective_ceiling_label": _ceiling_lbl_sj,
            "stuck_job_sub_ceiling_seconds": self._config.stuck_job_sub_ceiling_seconds,
        }
        if effective_suspect is not None:
            diag_sj["suspect_threshold"] = effective_suspect
        for key, value in corr_diag_sj.items():
            if key not in diag_sj:
                diag_sj[key] = value
        evidence_block_sj, _freshest_age_sj = self._build_evidence_summary_diag(now)
        for ev_key, ev_value in evidence_block_sj.items():
            if ev_key not in diag_sj:
                diag_sj[ev_key] = ev_value
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=current_run_elapsed,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=self._config.stuck_job_sub_ceiling_seconds,
            suspect_threshold_seconds=effective_suspect,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag_sj),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s"
            " stuck_job_sub_ceiling={}s alive_by={}",
            WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            round(idle_elapsed, 1),
            round(candidate_total, 1),
            self._config.stuck_job_sub_ceiling_seconds,
            alive_by,
        )
        return WatchdogVerdict.FIRE

    if candidate_total >= effective_ceiling:
        if self._config.profile == TimeoutProfile.ACTIVITY_ONLY:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
        corr_diag_hs = self._build_corroboration_diag(current_corr)
        corr_diag_hs["evidence"] = self._build_evidence_string(corr_diag_hs)
        _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
        diag: dict[str, object] = {
            "cumulative": round(candidate_total, 1),
            "run_elapsed": round(current_run_elapsed, 1),
            "idle_elapsed": round(idle_elapsed, 1),
            "effective_ceiling": effective_ceiling,
            "effective_ceiling_label": _ceiling_lbl,
        }
        if effective_suspect is not None:
            diag["suspect_threshold"] = effective_suspect
        for key, value in corr_diag_hs.items():
            if key not in diag:
                diag[key] = value
        evidence_block, _freshest_age = self._build_evidence_summary_diag(now)
        for ev_key, ev_value in evidence_block.items():
            if ev_key not in diag:
                diag[ev_key] = ev_value
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        self._emit(
            WaitingStatusKind.HARD_STOP,
            current_run_seconds=current_run_elapsed,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=effective_ceiling,
            suspect_threshold_seconds=effective_suspect,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", diag),
        )
        self._log.warning(
            "idle watchdog: FIRE reason={} idle_elapsed={}s cumulative_waiting={}s",
            WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            round(idle_elapsed, 1),
            round(candidate_total, 1),
        )
        return WatchdogVerdict.FIRE

    if (
        effective_suspect is not None
        and not self._suspicion_announced_for_run
        and candidate_total >= effective_suspect
    ):
        self._suspicion_announced_for_run = True
        corr_diag_sf = self._build_corroboration_diag(current_corr)
        corr_diag_sf["evidence"] = self._build_evidence_string(corr_diag_sf)
        corr_diag_sf["suspect_reason"] = suspect_reason
        corr_diag_sf["suspect_threshold"] = effective_suspect
        _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
        corr_diag_sf["effective_ceiling_label"] = _ceiling_lbl
        self._log.warning(
            "idle watchdog: SUSPECTED_FROZEN candidate_total={}s suspect={}s ceiling={}s",
            round(candidate_total, 1),
            effective_suspect,
            effective_ceiling,
        )
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        self._emit(
            WaitingStatusKind.SUSPECTED_FROZEN,
            current_run_seconds=current_run_elapsed,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=effective_ceiling,
            suspect_threshold_seconds=effective_suspect,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_sf),
        )

    assert self._last_waiting_status_at is not None
    if now - self._last_waiting_status_at >= self._config.waiting_status_interval_seconds:
        self._last_waiting_status_at = now
        corr_diag_pr = self._build_corroboration_diag(current_corr)
        _ceiling_lbl = self._effective_ceiling_label(current_corr, effective_ceiling)
        corr_diag_pr["effective_ceiling"] = effective_ceiling
        corr_diag_pr["effective_ceiling_label"] = _ceiling_lbl
        heartbeat_count = self._subagent_count_for_heartbeat()
        self._log.info(
            "idle watchdog: agent waiting on subagent ({} alive) for {}s - hard ceiling at {}s",
            heartbeat_count,
            round(candidate_total, 0),
            round(effective_ceiling, 0),
        )
        self._emit(
            WaitingStatusKind.PROGRESS,
            current_run_seconds=current_run_elapsed,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=effective_ceiling,
            diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_pr),
        )

    live_subagent_count = (
        self._process_monitor.live_subagent_count() if self._process_monitor is not None else 0
    )
    if (self._last_subagent_progress_description is not None or live_subagent_count > 0) and (
        self._last_subagent_progress_emit_at is None
        or (now - self._last_subagent_progress_emit_at)
        >= self._config.watchdog_subagent_progress_interval_seconds
    ):
        self._last_subagent_progress_emit_at = now
        subagent_diag: dict[str, object] = {
            "live_subagent_count": live_subagent_count,
            "subagent_progress_count": self._subagent_progress_count,
            "last_subagent_progress_at": self._last_subagent_progress_at,
        }
        if self._last_subagent_progress_description is not None:
            subagent_diag["subagent_activity"] = _sanitize_subagent_description(
                self._last_subagent_progress_description
            )[:200]
        self._emit(
            WaitingStatusKind.SUBAGENT_PROGRESS,
            current_run_seconds=current_run_elapsed,
            idle_elapsed=idle_elapsed,
            ceiling_seconds=effective_ceiling,
            diagnostic=cast(
                "dict[str, str | int | float | bool | list[object]]",
                subagent_diag,
            ),
        )

    return WatchdogVerdict.WAITING_ON_CHILD
