"""Active/drain/evaluate branch helpers for :class:`IdleWatchdog`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._evidence_tier import ChannelName
from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog
    from ralph.agents.idle_watchdog.waiting_status_kind import WaitingStatusKind

def build_evidence_summary_diag(
    self: IdleWatchdog,
    now: float,
) -> tuple[dict[str, object], float | None]:
    """Build the per-channel evidence_summary diagnostic block.

    Returns a 2-tuple ``(diag, freshest_age)`` where ``diag`` embeds
    the per-channel ChannelEvidenceSummary dicts under the
    ``evidence_summary`` key, plus a flat ``active_channel`` label
    (the name of the freshest non-stdout channel, or "none" when no
    channel is currently active) and the configured
    ``activity_evidence_ttl_seconds``. ``freshest_age`` is the age
    in seconds of the freshest non-stdout channel currently below
    the TTL (i.e. the channel that is doing the deferral), or
    ``None`` when no non-stdout channel is currently fresh.

    Used by both the verdict hook (for the deferred CONTINUE path)
    and the HARD_STOP diagnostic (for the CHILDREN_PERSIST_TOO_LONG
    path). The freshest_age is surfaced separately so the
    ``_handle_evidence_deferral`` debug log can name the actual
    channel age (not the stdout idle elapsed) as the reason for
    the deferral.
    """
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
    """Emit a fire log with per-channel evidence_summary in loguru extra."""
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
    """Build and dispatch a WaitingStatusEvent to listeners.

    The configured ``WaitingStatusListener`` always receives the event.
    Additionally, any ``subagent_activity`` payload is forwarded to the
    default subagent-activity listener so callers can observe real-time
    subagent progress without implementing a full status listener.

    Never propagates listener exceptions; logs at DEBUG if one is raised.
    """
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
    # Store the most recent classify_quiet callable so the gate
    # (``_gate_fire`` -> ``_classify_stuck_now``) can consult the
    # classifier's ``WAITING_ON_CHILD`` / ``RESUMABLE_CONTINUE``
    # branches with the same live signal the rest of ``evaluate()``
    # is using. A noop stub would force those branches to never
    # fire, which is the bug the analysis feedback called out.
    self._classify_quiet_provider = classify_quiet

    # Arm the tick-scoped corroboration cache for this
    # ``evaluate()`` call. ``_evaluate_tick_active`` is the
    # unambiguous "tick active" sentinel that ``_safe_corroborate``
    # checks before consulting ``_tick_corroboration`` (the cache is
    # only consulted while the tick is active). The cache itself
    # (``_tick_corroboration``) starts as ``None`` and is lazily
    # populated on the FIRST ``_safe_corroborate()`` call inside
    # ``_evaluate_inner`` so the corroborator's side-effects (e.g.
    # registry ``prune_stale``) do NOT pre-empt the strategy's
    # first ``classify_quiet()`` read of the same registry. This
    # preserves the historical "strategy first, corroborator
    # second" call order that
    # ``test_stale_scoped_child_evidence_fires_no_output_deadline``
    # pins while still reusing one snapshot across the
    # WAITING_ON_CHILD path's entry/ceiling/diagnostic reads
    # inside a single tick
    # (``test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics``).
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


def evaluate_inner(  # noqa: PLR0911 - gate + 5 sub-evaluators; each is a distinct verdict path
    self: IdleWatchdog,
    *,
    now: float,
    classify_quiet: Callable[[], AgentExecutionState],
) -> WatchdogVerdict:
    """Inner ``evaluate()`` body. Runs inside the tick-scoped cache lifetime.

    ``evaluate()`` arms ``self._tick_corroboration = None`` for this
    call. ``_safe_corroborate()`` lazily populates the cache on the
    first read, then returns the cached snapshot for every
    subsequent read so all sub-evaluators (NO_OUTPUT_AT_START,
    STRICTLY_STUCK, NO_PROGRESS_QUIET, WAITING_ON_CHILD) and
    diagnostic surfaces on this tick see the SAME alive_by signal.
    Outside of ``evaluate()`` (external probing, helper access from
    test code that bypasses ``evaluate()``) the cache is ``None``
    and the corroborator is invoked directly.
    """
    # Poll observable subagent output streams before any verdict so fresh
    # subagent output is treated as first-party activity on this tick.
    self.poll_subagent_output(now=now)

    fire_reason: WatchdogFireReason | None = None
    # The session ceiling is the highest-priority fire reason
    # (operator-set hard cap, absolute). It MUST be checked
    # first so a session-ceiling fire always wins over a
    # concurrent repeated-error-loop fire. Both checks below
    # are independent: REPEATED_ERROR_LOOP is a wedged
    # retry-loop signal that is gated by the smart-verdict
    # gate, while SESSION_CEILING_EXCEEDED bypasses the gate
    # (see ``_gate_fire``). Prior versions used an ``elif``
    # here, which made the repeated-error breaker unreachable
    # whenever ``max_session_seconds`` was configured (the
    # default production configuration sets the session
    # ceiling via ``GeneralConfig.agent_max_session_seconds``,
    # so the breaker was silently disabled in normal runs).
    if self._config.max_session_seconds is not None:
        session_elapsed = now - self._session_started_at
        if session_elapsed >= self._config.max_session_seconds:
            fire_reason = WatchdogFireReason.SESSION_CEILING_EXCEEDED
    if fire_reason is None and self._repetition_tracker.tripped():
        # Two independent repetition dimensions share the same
        # consecutive + window thresholds.  When BOTH dimensions
        # are tripped the error dimension wins (the canonical
        # ``REPEATED_ERROR_LOOP`` reason).  When ONLY the
        # tool-call dimension is tripped the watchdog fires
        # ``REPEATED_IDENTICAL_TOOL_CALL`` so the failure
        # classifier sees a precise cause.
        if self._repetition_tracker.tripped_tool_dimension():
            fire_reason = WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
        else:
            fire_reason = WatchdogFireReason.REPEATED_ERROR_LOOP
    if fire_reason is not None:
        idle_elapsed = now - self._last_activity
        # Smart-verdict gate: SESSION_CEILING_EXCEEDED is the only
        # absolute reason and bypasses the gate. REPEATED_ERROR_LOOP
        # is gated because a wedged retry loop is a stuck-detection
        # signal, not an operator-set hard cap.
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

    # STRICTLY_STUCK orthogonal ceiling. Engages BEFORE the
    # idle_timeout_seconds check so a stuck-but-alive job whose
    # idle_timeout has already elapsed is caught by the strictly-
    # stuck ceiling (which is tuned for that exact case) rather
    # than by the generic NO_OUTPUT_DEADLINE path. The corroborator
    # is consumed via the safe-normalize seam so a missing or
    # misbehaving corroborator falls through to an empty snapshot
    # (no alive_by) and the ceiling does not engage.
    strictly_stuck_verdict = self._evaluate_strictly_stuck(
        now,
        idle_elapsed,
        corroboration=self._safe_corroborate(),
    )
    if strictly_stuck_verdict is not None:
        return strictly_stuck_verdict

    if (
        quiet_state == AgentExecutionState.WAITING_ON_CHILD
        and (
            self._config.no_progress_quiet_seconds is not None
            or self._config.no_progress_quiet_heartbeat_ceiling_seconds is not None
        )
    ):
        no_progress_verdict = self._evaluate_no_progress_quiet(now, idle_elapsed)
        if no_progress_verdict is not None:
            return no_progress_verdict

    if idle_elapsed < self._config.idle_timeout_seconds:
        self._accumulate_waiting_run(now)
        return WatchdogVerdict.CONTINUE

    verdict = self._evaluate_final_verdict(now, idle_elapsed, classify_quiet)
    return verdict


def handle_evidence_deferral(
    self: IdleWatchdog,
    now: float,
    idle_elapsed: float,
) -> WatchdogVerdict:
    """Defer a NO_OUTPUT_DEADLINE fire while a non-stdout channel is fresh.

    Called from ``evaluate()`` when the idle deadline has elapsed and the
    post-tool-result wedge has NOT fired, but at least one non-stdout
    channel (mcp_tool, subagent, workspace) is fresher than
    ``activity_evidence_ttl_seconds``. The watchdog returns CONTINUE
    with a debug log naming the active channel, and the cumulative
    WAITING_ON_CHILD ceiling is NOT advanced (deferral is independent
    of waiting-on-child state — a productive session that emits no
    stdout but is busy on a non-stdout channel is not a 'child wait').

    This is the activity-aware verdict path. The SESSION_CEILING and
    CHILDREN_PERSIST_TOO_LONG ceilings are checked BEFORE this hook
    in ``evaluate()`` and remain absolute.

    The debug emission is throttled by the same
    ``watchdog_log_throttle_seconds`` window used by ``_gate_fire``
    so a session that stays active only through non-stdout evidence
    does not produce per-tick DEBUG spam (the PROMPT log showed
    repeated per-tick emissions here while the gate already
    silenced its own spam).
    """
    summary, freshest_age = self._build_evidence_summary_diag(now)
    active_channel_value = summary.get("active_channel", "none")
    channel_label = active_channel_value if isinstance(active_channel_value, str) else "none"
    # The 'age=' field is the age of the FRESHEST non-stdout channel
    # (i.e. the channel that is doing the deferral). When no channel
    # is fresh we fall back to idle_elapsed so the log always shows
    # a finite number; in that case the channel label is 'none' and
    # the log line still tells the operator why the verdict was
    # deferred (or, for 'none', that the deferral was driven by
    # some channel the helper did not enumerate).
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
    """Return True (and stamp the throttle map) when the
    ``_handle_evidence_deferral`` DEBUG emission is allowed for
    this ``channel_label`` key.

    Mirrors ``_maybe_log_deferred`` (which throttles ``_gate_fire``
    DEBUG spam). The PROMPT log showed per-tick DEBUG records at
    ``_handle_evidence_deferral`` while a session stayed active
    only through non-stdout evidence; this helper consults
    ``self._last_evidence_deferral_log_at`` and the configured
    ``watchdog_log_throttle_seconds`` to keep emissions to at most
    one per channel key per throttle window. A different channel
    label (e.g. ``mcp_tool`` vs ``subagent``) gets its own entry
    so the operator still sees the channel transition even when
    the previous channel's emissions were suppressed.
    """
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
    """Return the verdict when post-tool-result progression has stalled long enough.

    Returns ``None`` when the post-tool-result stall check is not
    applicable (no tool result, not awaiting progression, or the
    stall window has not yet elapsed). Returns ``WatchdogVerdict.FIRE``
    when the stall has been confirmed and the gate allowed the fire.
    Returns ``WatchdogVerdict.CONTINUE`` when the stall has been
    confirmed but the StuckClassifier gate deferred the fire (e.g.
    the agent is in a waiting state, the network is offline, or a
    first-party channel is fresh).

    The gate is consulted BEFORE the fire reason is set and BEFORE
    the log is emitted so a deferred fire leaves no diagnostic trace
    that suggests an actual fire.
    """
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
        return self._handle_waiting_branch(now, classify_quiet)

    drain_elapsed = now - self._drain_started_at
    if drain_elapsed < self._config.drain_window_seconds:
        # Throttle the drain-window DEBUG emission via the existing
        # ``maybe_log_any_deferred`` helper. The PROMPT log showed the
        # bare ``_log.debug(...)`` here firing every tick (e.g.
        # ``idle watchdog: drain window active drain_elapsed=0.1s``
        # every 0.1s) which is log spam. The throttle caps emissions
        # to at most one per ``watchdog_log_throttle_seconds`` (30s
        # default) using the SESSION_CEILING_EXCEEDED key (drain
        # windows are entered when a session ceiling is being
        # approached). The single-tick pre-amble is not informative
        # to an operator; a periodic heartbeat that names the elapsed
        # drain time is the human-readable signal.
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
    """Handle the case where the agent appears active (no children visible).

    Accumulates any elapsed WAITING run time before entering the drain window.
    When drain_window_seconds=0, fires immediately without a drain window.
    """
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
    """Compute the final verdict after idle timeout.

    Called from evaluate() when the idle deadline has elapsed. Handles
    drain_window, post-tool stall, waiting branch, evidence deferral,
    and active branch cases.
    """
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
