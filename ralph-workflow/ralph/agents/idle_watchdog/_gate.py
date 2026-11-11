
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind, classify_stuck
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog


def classify_stuck_now(
    self: IdleWatchdog,
    *,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot | None = None,
) -> StuckKind:
    summary = self.last_evidence_summary(now)
    connectivity = self._current_connectivity_state()

    def _noop_classify_quiet() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    return classify_stuck(
        is_waiting_state=self._is_waiting_state,
        connectivity_state=connectivity,
        evidence_summary=summary,
        classify_quiet=_noop_classify_quiet,
        activity_evidence_ttl_seconds=self._config.activity_evidence_ttl_seconds,
        silent_subagent_seconds=self._config.silent_subagent_seconds,
        corroboration=corroboration,
    )


def gate_fire(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    *,
    now: float,
    idle_elapsed: float,
    corroboration: CorroborationSnapshot | None = None,
) -> WatchdogVerdict:
    if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        return WatchdogVerdict.FIRE
    kind = self._classify_stuck_now(now=now, idle_elapsed=idle_elapsed, corroboration=corroboration)
    if kind == StuckKind.STUCK:
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        return WatchdogVerdict.FIRE
    if kind == StuckKind.SILENT_SUBAGENT:
        self._log.info(
            "idle watchdog: silent subagent (firing) reason={} idle_elapsed={}s",
            fire_reason,
            round(idle_elapsed, 1),
        )
        self._set_stall(active=True, now=now, idle_elapsed=idle_elapsed)
        return WatchdogVerdict.FIRE
    if self._stall_active:
        self._set_stall(active=False, now=now, idle_elapsed=idle_elapsed)
    self._last_fire_reason = WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER
    self._last_deferred_kind = kind
    coarse_allowed = self._maybe_log_any_deferred(fire_reason, now)
    if coarse_allowed and self._maybe_log_deferred(fire_reason, kind, idle_elapsed, now):
        self._log.debug(
            "idle watchdog: deferred fire reason={} kind={} idle_elapsed={}s",
            fire_reason,
            kind,
            round(idle_elapsed, 1),
        )
    return WatchdogVerdict.CONTINUE


def maybe_log_deferred(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    deferred_kind: StuckKind,
    idle_elapsed: float,
    now: float,
) -> bool:
    key = (fire_reason.value, deferred_kind.value)
    last = self._last_deferred_log_at.get(key)
    throttle = self._config.watchdog_log_throttle_seconds
    if last is None or (now - last) >= throttle:
        self._last_deferred_log_at[key] = now
        return True
    return False


def maybe_log_any_deferred(
    self: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    now: float,
) -> bool:
    last = self._last_any_deferred_log_at.get(fire_reason.value)
    throttle = self._config.watchdog_log_throttle_seconds
    if last is None or (now - last) >= throttle:
        self._last_any_deferred_log_at[fire_reason.value] = now
        return True
    return False
