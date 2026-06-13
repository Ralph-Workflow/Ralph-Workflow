"""Idle watchdog for detecting stalled agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.process.child_liveness import AliveBy

from .corroboration_snapshot import (
    ChannelEvidenceSummary,
    ChannelName,
    CorroborationSnapshot,
    WaitingCorroborator,
)
from .repetition_tracker import RepetitionTracker
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

    Per-channel activity evidence (NEW): the watchdog tracks three non-stdout
    channels in addition to the stdout baseline:
      - mcp_tool: MCP tools/call invocations/completions routed via the
        Ralph MCP server. Updated by ``record_mcp_tool_call``.
      - subagent: subagent progress signals (heartbeat, phase change) routed
        from the opencode child_liveness registry. Updated by
        ``record_subagent_work``.
      - workspace: workspace file change events captured by
        WorkspaceMonitor. Updated by ``record_workspace_event``, which is
        invoked by the readers' ``on_event`` callback passed to
        ``WorkspaceMonitor.set_on_event`` (the monitor is constructed in
        ``invoke_agent`` before the per-run watchdog exists, so the
        readers register the callback on the monitor immediately after
        the watchdog is created in ``read_lines``; the binding is
        cleared in the ``finally`` block so a stale callback can never
        fire after the run ends).

    The three recorders do NOT touch ``_last_activity`` (the stdout baseline);
    the existing "stdout only resets idle baseline" invariant is preserved.
    Instead, they update per-channel ``_last_at`` timestamps and counters. The
    verdict hook in ``evaluate()`` defers a NO_OUTPUT_DEADLINE fire when ANY
    non-stdout channel is fresher than ``activity_evidence_ttl_seconds``,
    returning CONTINUE with a debug log. Absolute ceilings
    (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG) are checked before
    the deferral hook and remain absolute.
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
    # Per-channel activity evidence state (NEW). The three recorders
    # ``record_mcp_tool_call``, ``record_subagent_work``, and
    # ``record_workspace_event`` only update these fields; they do NOT
    # touch ``_last_activity`` (the stdout baseline) or the cumulative
    # waiting-on-child ceiling. The verdict hook in ``evaluate()``
    # consults these fields via ``_channel_evidence_active`` and
    # ``last_evidence_summary``.
    _mcp_tool_call_count: int = field(default=0, init=False)
    _last_mcp_tool_call_at: float | None = field(default=None, init=False)
    _subagent_progress_count: int = field(default=0, init=False)
    _last_subagent_progress_at: float | None = field(default=None, init=False)
    _workspace_event_count_internal: int = field(default=0, init=False)
    _last_workspace_event_at: float | None = field(default=None, init=False)
    # Per-kind workspace event counter. The watchdog tracks how many
    # file changes have been observed for each WorkspaceChangeKind
    # (source / log / cache / artifact / other) so the post-mortem
    # can see WHICH kinds were most active at the moment of a fire.
    # The workspace_kind_counts property returns a defensive copy.
    _workspace_kind_counts: dict[str, int] = field(default_factory=dict, init=False)

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
        self._mcp_tool_call_count = 0
        self._last_mcp_tool_call_at = None
        self._subagent_progress_count = 0
        self._last_subagent_progress_at = None
        self._workspace_event_count_internal = 0
        self._last_workspace_event_at = None
        self._workspace_kind_counts = {}
        self._entry_corroboration: CorroborationSnapshot | None = None
        self._repetition_tracker = RepetitionTracker(
            clock,
            consecutive_threshold=config.repeated_error_consecutive_threshold,
            window_count=config.repeated_error_window_count,
            window_seconds=config.repeated_error_window_seconds,
        )
        self._last_progress_fingerprint: str | None = None
        self._log = logger.bind(component="idle_watchdog")

    @property
    def last_fire_reason(self) -> WatchdogFireReason | None:
        """The reason the watchdog fired, or None if it hasn't fired yet."""
        return self._last_fire_reason

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        """Cumulative seconds spent in WAITING_ON_CHILD state across all runs."""
        return self._cumulative_waiting_on_child_seconds

    def idle_elapsed_seconds(self, now: float) -> float:
        """Seconds since the last recorded activity (the idle duration).

        Public accessor so callers (e.g. the process-reader fire log) can report
        a meaningful idle-elapsed value instead of the raw monotonic clock.
        """
        return now - self._last_activity

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

        Counts as genuine forward progress for the repeated-error circuit
        breaker: it resets the repetition streak so an error loop only fires
        when the agent is NOT making real progress.
        """
        self._reset_idle_baseline()
        self._repetition_tracker.note_progress()

    def record_lifecycle_activity(self) -> None:
        """Record cosmetic, non-meaningful activity (e.g. lifecycle frames).

        Resets the idle baseline exactly like ``record_activity()`` so the
        agent is not declared idle, but does NOT reset the repeated-error
        circuit breaker: cosmetic output interleaved between identical errors
        must not mask a wedged retry loop.
        """
        self._reset_idle_baseline()

    def record_error_activity(self, message: str) -> None:
        """Record an error/repeat line for the repeated-error circuit breaker.

        Deliberately does NOT reset the idle baseline: a stream of identical
        errors must still let the idle deadline advance (so a silent-after-errors
        agent is also caught), while the repeated-error rule catches a fast retry
        storm well before the idle timeout. The cumulative WAITING_ON_CHILD run is
        still flushed for bookkeeping parity.
        """
        now = self._clock.monotonic()
        self._accumulate_waiting_run(now)
        self._repetition_tracker.note_error(message)

    def record_progress_report(self, message: str) -> None:
        """Record an explicit ``report_progress`` heartbeat from the agent.

        A report that REPEATS the previous status (same fingerprint) is a cosmetic
        heartbeat: it feeds the repeated-error circuit breaker and does NOT reset
        the idle baseline, so an agent narrating "still stuck" forever can no
        longer keep itself alive. A report whose status CHANGES is treated as
        genuine forward progress (resets the idle baseline and the streak).
        """
        fingerprint = RepetitionTracker.fingerprint(message)
        if fingerprint == self._last_progress_fingerprint:
            now = self._clock.monotonic()
            self._accumulate_waiting_run(now)
            self._repetition_tracker.note_error(message)
            return
        self._last_progress_fingerprint = fingerprint
        self.record_activity()

    def _reset_idle_baseline(self) -> None:
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
        self._repetition_tracker.note_progress()

    def record_mcp_tool_call(self, now: float | None = None) -> None:
        """Record an MCP tool-call activity signal (new channel).

        Increments the mcp_tool channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline) — the existing 'stdout only resets idle baseline' invariant
        is preserved. The verdict hook in ``evaluate()`` consults the per-channel
        ``_last_at`` via ``_channel_evidence_active`` and defers a
        NO_OUTPUT_DEADLINE fire while the channel is fresher than the configured
        ``activity_evidence_ttl_seconds``.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._mcp_tool_call_count += 1
        self._last_mcp_tool_call_at = timestamp

    def record_subagent_work(self, now: float | None = None) -> None:
        """Record a subagent work activity signal (new channel).

        Increments the subagent channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline). The verdict hook in ``evaluate()`` defers a
        NO_OUTPUT_DEADLINE fire while this channel is fresher than the
        configured ``activity_evidence_ttl_seconds``.

        A subagent that exists but has produced no tool calls, no progress
        signals, and no file changes for the full TTL is NOT evidence of
        progress — its channel becomes stale and the watchdog returns to
        the normal idle path.

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        self._subagent_progress_count += 1
        self._last_subagent_progress_at = timestamp

    def record_workspace_event(
        self,
        now: float | None = None,
        *,
        kind: WorkspaceChangeKind = WorkspaceChangeKind.OTHER,
        weight: float = 1.0,
    ) -> None:
        """Record a workspace file-change activity signal (new channel).

        Increments the workspace channel counter and updates the per-channel
        ``_last_at`` timestamp. Does NOT touch ``_last_activity`` (the stdout
        baseline). The verdict hook in ``evaluate()`` defers a
        NO_OUTPUT_DEADLINE fire while this channel is fresher than the
        configured ``activity_evidence_ttl_seconds``.

        When ``weight == 0.0`` the event is short-circuited (defense in
        depth: the WorkspaceMonitor already drops weight-0 events before
        invoking this recorder, but the watchdog enforces the contract
        too so a misconfigured binding cannot accidentally record a
        dropped event). When ``weight == 1.0`` the per-kind counter
        ``_workspace_kind_counts[kind.value]`` is advanced so the
        post-mortem diagnostic can show which kinds were most active.

        Args:
            now: Optional monotonic timestamp override; tests use this
                to drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
            kind: The ``WorkspaceChangeKind`` of the recorded event.
                Used to advance the per-kind counter so the post-mortem
                diagnostic can show ``{source: 10, log: 0, ...}`` at
                the moment of a fire. Defaults to
                ``WorkspaceChangeKind.OTHER`` (the legacy 0-arg binding
                from the pre-fix production code).
            weight: The binary weight of the recorded event. ``0.0``
                means the change is dropped (no counter / no timestamp
                update); ``1.0`` means the change counts as full
                activity. Defaults to ``1.0`` for the legacy 0-arg
                binding.
        """
        if weight == 0.0:
            return
        timestamp = now if now is not None else self._clock.monotonic()
        self._workspace_event_count_internal += 1
        self._last_workspace_event_at = timestamp
        self._workspace_kind_counts[kind.value] = self._workspace_kind_counts.get(kind.value, 0) + 1

    @property
    def workspace_kind_counts(self) -> dict[str, int]:
        """Defensive copy of the per-kind workspace event counter.

        Returns a fresh dict on every access so callers (the
        post-mortem diagnostic, the operator UX) can mutate the
        result without affecting the watchdog's internal state. The
        keys are the five ``WorkspaceChangeKind`` string values
        (``source``, ``log``, ``cache``, ``artifact``, ``other``);
        kinds that have never been observed are absent from the
        returned dict.
        """
        return dict(self._workspace_kind_counts)

    def last_evidence_summary(self, now: float | None = None) -> tuple[ChannelEvidenceSummary, ...]:
        """Return a per-channel evidence summary at the given time.

        Always returns a 4-tuple in the fixed channel order
        (stdout, mcp_tool, subagent, workspace). Each ChannelEvidenceSummary
        carries the channel name, the last observed monotonic timestamp
        (``last_at``), the age in seconds (``age_seconds``; None when
        ``last_at`` is None), and the per-channel counter (``counter``; None
        when the channel has never been observed).

        The summary is consumed by the watchdog's own verdict hook
        (via ``_channel_evidence_active``) and by the post-mortem
        diagnostic threading in the readers (the ``_check_fire`` path
        embeds the summary into the ``evidence_summary`` key of the
        watchdog fire diagnostic).

        Args:
            now: Optional monotonic timestamp override; tests use this to
                drive FakeClock without time travel. Defaults to the
                watchdog's injected clock.
        """
        timestamp = now if now is not None else self._clock.monotonic()
        stdout_age = max(0.0, timestamp - self._last_activity)
        return (
            ChannelEvidenceSummary(
                channel_name="stdout",
                last_at=self._last_activity,
                age_seconds=stdout_age,
                counter=None,
            ),
            self._channel_summary(
                "mcp_tool", self._last_mcp_tool_call_at, self._mcp_tool_call_count, timestamp, None
            ),
            self._channel_summary(
                "subagent",
                self._last_subagent_progress_at,
                self._subagent_progress_count,
                timestamp,
                None,
            ),
            self._channel_summary(
                "workspace",
                self._last_workspace_event_at,
                self._workspace_event_count_internal,
                timestamp,
                self._workspace_kind_breakdown_for_summary(),
            ),
        )

    def _workspace_kind_breakdown_for_summary(self) -> dict[str, int] | None:
        """Return the per-kind workspace counter snapshot for the summary.

        Returns ``None`` when no workspace activity has been observed
        yet (so the resulting ``ChannelEvidenceSummary.kind_breakdown``
        is ``None`` and is omitted from ``to_dict()`` for
        backward-compat with consumers that assert on the dict shape).
        Returns a fresh defensive copy when at least one kind has
        been observed (so the frozen dataclass invariant is preserved
        and the watchdog's internal state is not exposed).
        """
        if not self._workspace_kind_counts:
            return None
        return dict(self._workspace_kind_counts)

    @staticmethod
    def _channel_summary(
        channel_name: ChannelName,
        last_at: float | None,
        counter: int,
        now: float,
        kind_breakdown: dict[str, int] | None,
    ) -> ChannelEvidenceSummary:
        """Build a ChannelEvidenceSummary for a single non-stdout channel."""
        age: float | None = None if last_at is None else max(0.0, now - last_at)
        observed_counter: int | None = counter if counter > 0 else None
        return ChannelEvidenceSummary(
            channel_name=channel_name,
            last_at=last_at,
            age_seconds=age,
            counter=observed_counter,
            kind_breakdown=kind_breakdown,
        )

    def _channel_evidence_active(self, now: float) -> bool:
        """Return True when any non-stdout channel is fresher than the TTL.

        Used by the verdict hook in ``evaluate()`` to defer a
        NO_OUTPUT_DEADLINE fire while a non-stdout channel is still showing
        activity. Returns False when the TTL is None (legacy behavior — but
        the verdict hook guards on this already), or when no channel has been
        observed, or when every observed channel is older than the TTL.

        The stdout channel is intentionally excluded: a quiet stdout is the
        NORMAL state we are trying to detect, so it cannot itself defer the
        verdict.
        """
        ttl = self._config.activity_evidence_ttl_seconds
        if ttl is None or ttl <= 0.0:
            return False
        for last_at in (
            self._last_mcp_tool_call_at,
            self._last_subagent_progress_at,
            self._last_workspace_event_at,
        ):
            if last_at is None:
                continue
            if (now - last_at) < ttl:
                return True
        return False

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

    def _build_evidence_summary_diag(
        self,
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
        for entry in summary:
            flat.append(entry.to_dict())
            if entry.channel_name == "stdout":
                continue
            if (
                entry.age_seconds is not None
                and ttl is not None
                and ttl > 0.0
                and entry.age_seconds < ttl
                and (freshest_age is None or entry.age_seconds < freshest_age)
            ):
                freshest_age = entry.age_seconds
                active_channel = entry.channel_name
        diag: dict[str, object] = {
            "evidence_summary": cast("list[object]", list(flat)),
            "active_channel": active_channel,
            "activity_evidence_ttl_seconds": ttl,
        }
        return (diag, freshest_age)

    def _emit_fire_log(
        self,
        reason: WatchdogFireReason,
        *,
        now: float,
        idle_elapsed: float,
        message_suffix: str = '',
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

    def _emit(
        self,
        kind: WaitingStatusKind,
        current_run_seconds: float,
        idle_elapsed: float,
        *,
        ceiling_seconds: float | None = None,
        diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
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
                self._emit_fire_log(
                    WatchdogFireReason.SESSION_CEILING_EXCEEDED,
                    now=now,
                    idle_elapsed=idle_elapsed,
                    message_suffix=f' session_elapsed={round(session_elapsed, 1)}s',
                )
                return WatchdogVerdict.FIRE

        if self._repetition_tracker.tripped():
            self._last_fire_reason = WatchdogFireReason.REPEATED_ERROR_LOOP
            idle_elapsed = now - self._last_activity
            self._emit_fire_log(
                WatchdogFireReason.REPEATED_ERROR_LOOP,
                now=now,
                idle_elapsed=idle_elapsed,
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
                # Cumulative ceiling path; the activity channel does NOT
                # defer this branch (CHILDREN_PERSIST_TOO_LONG is absolute
                # and must fire regardless of non-stdout activity).
                verdict = self._handle_waiting_branch(now)
            elif self._channel_evidence_active(now):
                # Activity channel defers the ACTIVE-branch fire
                # (NO_OUTPUT_DEADLINE) but not the cumulative ceiling.
                verdict = self._handle_evidence_deferral(now, idle_elapsed)
            else:
                verdict = self._handle_active_branch(now)
        return verdict

    def _handle_evidence_deferral(
        self,
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
        self._log.debug(
            "idle watchdog: deferred via activity evidence channel={} age={}s idle_elapsed={}s",
            channel_label,
            age_for_log,
            round(idle_elapsed, 1),
        )
        return WatchdogVerdict.CONTINUE

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
        self._emit_fire_log(
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            now=now,
            idle_elapsed=idle_elapsed,
            message_suffix=f' since_tool_result={round(since_tool_result, 1)}s',
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
        self._emit_fire_log(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=idle_elapsed,
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
            diag: dict[str, object] = {
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
            evidence_block, _freshest_age = self._build_evidence_summary_diag(now)
            for ev_key, ev_value in evidence_block.items():
                if ev_key not in diag:
                    diag[ev_key] = ev_value
            self._emit(
                WaitingStatusKind.HARD_STOP,
                current_run_seconds=current_run_elapsed,
                idle_elapsed=idle_elapsed,
                ceiling_seconds=effective_ceiling,
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
                diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_sf),
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
                diagnostic=cast("dict[str, str | int | float | bool | list[object]]", corr_diag_pr),
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
