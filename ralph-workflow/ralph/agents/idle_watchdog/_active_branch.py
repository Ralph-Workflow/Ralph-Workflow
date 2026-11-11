
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._activity_methods import (
    parse_tool_call_from_description,
)
from ralph.agents.idle_watchdog._evidence_tier import ChannelName
from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog


def build_evidence_summary_diag(
    self: IdleWatchdog,
    now: float,
) -> tuple[dict[str, object], float | None]:
    summary = self.last_evidence_summary(now)
    ttl = self._config.activity_evidence_ttl_seconds
    active_channel = "none"
    freshest_age: float | None = None
    flat: list[dict[str, object]] = []
    for entry in summary.channels:
        flat.append(entry.to_dict())
        if entry.channel_name == ChannelName.STDOUT:
            continue
        if not entry.can_defer:
            continue
        if (
            entry.age_seconds is not None
            and ttl is not None
            and ttl > 0.0
            and entry.age_seconds < ttl
            and (freshest_age is None or entry.age_seconds < freshest_age)
        ):
            freshest_age = entry.age_seconds
            active_channel = entry.channel_name.value
    diag: dict[str, object] = {
        "evidence_summary": cast("list[object]", list(flat)),
        "active_channel": active_channel,
        "activity_evidence_ttl_seconds": ttl,
    }
    return (diag, freshest_age)


def emit_fire_log(
    self: IdleWatchdog,
    reason: WatchdogFireReason,
    *,
    now: float,
    idle_elapsed: float,
    message_suffix: str = "",
    **extra_fields: object,
) -> None:
    evidence_block, _freshest_age = self._build_evidence_summary_diag(now)
    extra_payload: dict[str, object] = {
        "evidence_summary": evidence_block["evidence_summary"],
        "active_channel": evidence_block.get("active_channel", "none"),
        "fire_reason": reason.value,
    }
    extra_payload.update(extra_fields)
    self._log.warning(
        "idle watchdog: FIRE reason={}{} idle_elapsed={}s cumulative_waiting={}s",
        reason,
        message_suffix,
        round(idle_elapsed, 1),
        round(self._cumulative_waiting_on_child_seconds, 1),
        extra=extra_payload,
    )


def emit(
    self: IdleWatchdog,
    kind: WaitingStatusKind,
    current_run_seconds: float,
    idle_elapsed: float,
    *,
    ceiling_seconds: float | None = None,
    suspect_threshold_seconds: float | None = None,
    diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
) -> None:
    main_listener = self._listener
    subagent_listener = self._default_subagent_activity_listener
    if main_listener is None and subagent_listener is None:
        return
    candidate_total = self._cumulative_waiting_on_child_seconds + current_run_seconds
    _suspect = (
        suspect_threshold_seconds
        if suspect_threshold_seconds is not None
        else self._config.suspect_waiting_on_child_seconds
    )
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
        suspect_threshold_seconds=_suspect,
        diagnostic=dict(diagnostic) if diagnostic else {},
        subagent_activity=self._last_subagent_progress_description,
        last_subagent_progress_at=self._last_subagent_progress_at,
        current_subagent_tool_call=parse_tool_call_from_description(
            self._last_subagent_progress_description
        ),
        stall_active=self._stall_active,
    )
    if main_listener is not None:
        try:
            main_listener(event)
        except Exception:
            self._log.debug("idle watchdog: listener raised (suppressed)")
    if event.subagent_activity is not None:
        if subagent_listener is not None:
            try:
                subagent_listener(event)
            except Exception:
                self._log.debug(
                    "idle watchdog: default subagent activity listener raised (suppressed)"
                )
        else:
            self._log.info(
                "idle watchdog: subagent activity: {}",
                event.subagent_activity,
            )


def evaluate(
    self: IdleWatchdog,
    *,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
    now = self._clock.monotonic()
    self._classify_quiet_provider = classify_quiet

    self._evaluate_tick_active = True
    self._tick_corroboration = None
    try:
        return self._evaluate_inner(
            now=now,
            classify_quiet=classify_quiet,
        )
    finally:
        self._evaluate_tick_active = False
        self._tick_corroboration = None


def evaluate_inner(
    self: IdleWatchdog,
    *,
    now: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
    self.poll_subagent_output(now=now)

    if self._config.profile.value == "activity_only":
        return _evaluate_activity_only(self, now)

    fire_reason: WatchdogFireReason | None = None
    if self._config.max_session_seconds is not None:
        session_elapsed = now - self._session_started_at
        if session_elapsed >= self._config.max_session_seconds:
            fire_reason = WatchdogFireReason.SESSION_CEILING_EXCEEDED
    if fire_reason is None and self._repetition_tracker.tripped():
        if self._repetition_tracker.tripped_tool_dimension():
            fire_reason = WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
        else:
            fire_reason = WatchdogFireReason.REPEATED_ERROR_LOOP
    if fire_reason is not None:
        idle_elapsed = now - self._last_activity
        gate_verdict = self._gate_fire(fire_reason, now=now, idle_elapsed=idle_elapsed)
        if gate_verdict == WatchdogVerdict.FIRE:
            self._emit_fire_log(
                fire_reason,
                now=now,
                idle_elapsed=idle_elapsed,
                message_suffix=(
                    f" session_elapsed={round(now - self._session_started_at, 1)}s"
                    if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
                    else ""
                ),
            )
            self._last_fire_reason = fire_reason
            return WatchdogVerdict.FIRE
        return WatchdogVerdict.CONTINUE

    idle_elapsed = now - self._last_activity
    quiet_state = classify_quiet()
    no_output_at_start_verdict = self._evaluate_no_output_at_start(
        now, idle_elapsed, classify_quiet
    )
    if no_output_at_start_verdict is not None:
        return no_output_at_start_verdict

    if self._config.idle_timeout_seconds is None:
        return WatchdogVerdict.CONTINUE

    strictly_stuck_verdict = self._evaluate_strictly_stuck(
        now,
        idle_elapsed,
        corroboration=self._safe_corroborate(),
    )
    if strictly_stuck_verdict is not None:
        return strictly_stuck_verdict

    if quiet_state == AgentExecutionState.WAITING_ON_CHILD and (
        self._config.no_progress_quiet_seconds is not None
        or self._config.no_progress_quiet_heartbeat_ceiling_seconds is not None
    ):
        no_progress_verdict = self._evaluate_no_progress_quiet(now, idle_elapsed)
        if no_progress_verdict is not None:
            return no_progress_verdict

    if idle_elapsed < self._config.idle_timeout_seconds:
        self._accumulate_waiting_run(now)
        return WatchdogVerdict.CONTINUE

    verdict = self._evaluate_final_verdict(now, idle_elapsed, classify_quiet)
    return verdict


def _evaluate_activity_only(self: IdleWatchdog, now: float) -> WatchdogVerdict:
    kind, last_at = self.activity_only_snapshot()
    if last_at is None:
        last_at = self._session_started_at
    idle_elapsed = now - last_at
    if self._activity_only_operator_cap_reached(now):
        self._last_fire_reason = WatchdogFireReason.OPERATOR_CAP_REACHED
        self._emit_fire_log(
            WatchdogFireReason.OPERATOR_CAP_REACHED,
            now=now,
            idle_elapsed=idle_elapsed,
            message_suffix=f" last_activity_kind={kind or 'none'}",
        )
        return WatchdogVerdict.FIRE
    self._emit_activity_only_status(now, kind, last_at)
    timeout = self._config.idle_timeout_seconds
    if timeout is None or idle_elapsed < timeout:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.CONFLICT_INACTIVITY
    self._emit_fire_log(
        WatchdogFireReason.CONFLICT_INACTIVITY,
        now=now,
        idle_elapsed=idle_elapsed,
        message_suffix=f" last_activity_kind={kind or 'none'}",
    )
    return WatchdogVerdict.FIRE


def _activity_only_operator_cap_reached(self: IdleWatchdog, now: float) -> bool:
    cap = self._config.activity_only_operator_cap_seconds
    started_at = self._invocation_started_at
    return cap is not None and started_at is not None and now - started_at >= cap


def _emit_activity_only_status(
    self: IdleWatchdog, now: float, kind: str | None, last_at: float
) -> None:
    last_status_at: float | None = self._last_activity_only_status_at
    interval = self._config.activity_only_status_interval_seconds
    if last_status_at is not None and now - last_status_at < interval:
        return
    started_at: float | None = self._invocation_started_at
    invocation_elapsed = max(0.0, now - started_at) if started_at is not None else 0.0
    self._last_activity_only_status_at = now
    last_age = max(0.0, now - last_at)
    self._emit(
        WaitingStatusKind.PROGRESS,
        current_run_seconds=invocation_elapsed,
        idle_elapsed=last_age,
        ceiling_seconds=self._config.idle_timeout_seconds,
        diagnostic={
            "last_activity_kind": kind or "none",
            "last_activity_age_seconds": last_age,
            "invocation_elapsed_seconds": invocation_elapsed,
        },
    )


def handle_evidence_deferral(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
) -> WatchdogVerdict:
    summary, freshest_age = self._build_evidence_summary_diag(now)
    active_channel_value = summary.get("active_channel", "none")
    channel_label = active_channel_value if isinstance(active_channel_value, str) else "none"
    age_for_log = round(freshest_age, 1) if freshest_age is not None else round(idle_elapsed, 1)
    if self._maybe_log_evidence_deferral(channel_label, now):
        self._log.debug(
            "idle watchdog: deferred via activity evidence channel={} age={}s idle_elapsed={}s",
            channel_label,
            age_for_log,
            round(idle_elapsed, 1),
        )
    return WatchdogVerdict.CONTINUE


def maybe_log_evidence_deferral(self: IdleWatchdog, channel_label: str, now: float) -> bool:
    key = channel_label
    last = self._last_evidence_deferral_log_at.get(key)
    throttle = self._config.watchdog_log_throttle_seconds
    if last is None or (now - last) >= throttle:
        self._last_evidence_deferral_log_at[key] = now
        return True
    return False


def post_tool_result_stalled(
    self: IdleWatchdog, now: float, idle_elapsed: float
) -> WatchdogVerdict | None:
    if (
        self._config.post_tool_result_progression_seconds is None
        or not self._awaiting_post_tool_result_progression
        or self._last_tool_result_at is None
    ):
        return None
    since_tool_result = now - self._last_tool_result_at
    if since_tool_result < self._config.post_tool_result_progression_seconds:
        return None
    gate_verdict = self._gate_fire(
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        now=now,
        idle_elapsed=idle_elapsed,
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
    self._emit_fire_log(
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        now=now,
        idle_elapsed=idle_elapsed,
        message_suffix=f" since_tool_result={round(since_tool_result, 1)}s",
    )
    return WatchdogVerdict.FIRE


def handle_drain_window(
    self: IdleWatchdog,
    now: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
    assert self._drain_started_at is not None

    quiet_state = classify_quiet()
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        self._in_drain_window = False
        self._drain_started_at = None
        self._log.info(
            "idle watchdog: drain window abandoned"
            " (children reappeared), switching to WAITING_ON_CHILD"
        )
        return self._handle_waiting_branch(now, classify_quiet)

    drain_elapsed = now - self._drain_started_at
    if drain_elapsed < self._config.drain_window_seconds:
        if self._maybe_log_any_deferred(WatchdogFireReason.SESSION_CEILING_EXCEEDED, now):
            self._log.debug(
                "idle watchdog: drain window active drain_elapsed={}s window={}s",
                round(drain_elapsed, 3),
                self._config.drain_window_seconds,
            )
        return WatchdogVerdict.CONTINUE

    idle_elapsed = now - self._last_activity
    gate_verdict = self._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE, now=now, idle_elapsed=idle_elapsed
    )
    if gate_verdict == WatchdogVerdict.CONTINUE:
        return WatchdogVerdict.CONTINUE
    self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
    self._emit_fire_log(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=idle_elapsed,
    )
    return WatchdogVerdict.FIRE


def handle_active_branch(self: IdleWatchdog, now: float) -> WatchdogVerdict:
    idle_elapsed = now - self._last_activity
    self._accumulate_waiting_run(now)
    if self._config.drain_window_seconds == 0.0:
        gate_verdict = self._gate_fire(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=idle_elapsed,
        )
        if gate_verdict == WatchdogVerdict.CONTINUE:
            return WatchdogVerdict.CONTINUE
        self._last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
        self._emit_fire_log(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=idle_elapsed,
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


def evaluate_final_verdict(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
    if self._in_drain_window:
        return self._handle_drain_window(now, classify_quiet)
    post_tool_verdict = self._post_tool_result_stalled(now, idle_elapsed)
    if post_tool_verdict is not None:
        return post_tool_verdict
    quiet_state = classify_quiet()
    if quiet_state == AgentExecutionState.WAITING_ON_CHILD:
        return self._handle_waiting_branch(now, classify_quiet)
    if self._channel_evidence_active(now):
        return self._handle_evidence_deferral(now, idle_elapsed)
    return self._handle_active_branch(now)
