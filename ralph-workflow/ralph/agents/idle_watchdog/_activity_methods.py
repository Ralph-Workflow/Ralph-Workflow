"""Activity recording and evidence summary helpers for :class:`IdleWatchdog`."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog._evidence_tier import (
    CHANNEL_DEFERS_BY_DEFAULT,
    CHANNEL_TIERS,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ralph.agents.idle_watchdog._sanitize import _sanitize_subagent_description
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.process.child_liveness import AliveBy

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog

# wt-024 M7 (AC-04): bound the per-invocation subagent output capture
# cache so a high-fan-out invocation that sees many distinct worker
# IDs in a single watchdog tick cannot grow the dict unboundedly.
# FIFO eviction retains the most recently observed worker captures
# (the ones most likely to be queried on the next tick) while
# shedding older workers in insertion order. The cap is a HARD
# bound: when the cache is full and a new worker appears, the
# OLDEST-INSERTED worker is evicted regardless of whether it is
# still live or not. There is no LRU refresh on poll -- the cache
# uses pure FIFO eviction so the bound holds across an entire
# invocation regardless of how many times each worker is polled.
#
# To preserve the no-duplicate-output property of stateful
# ``SubagentOutputCapture`` implementations (the production
# ``FileSubagentOutputCapture`` tracks a per-worker byte offset
# and would otherwise re-read historical lines if recreated from
# offset 0 after eviction), evicted worker IDs are recorded in a
# separate bounded ``_evicted_worker_tombstones`` map. Tombstoned
# workers are skipped for one eviction cycle so the duplicate
# line bug cannot reappear. The tombstone itself is bounded at
# ``_MAX_EVICTED_TOMBSTONES`` and uses FIFO eviction, keeping the
# total memory footprint of the watchdog bounded at
# ``_MAX_SUBAGENT_OUTPUT_CAPTURES + _MAX_EVICTED_TOMBSTONES``
# per invocation regardless of how many distinct worker IDs are
# observed.
#
# These constants are PRIVATE module attributes: the cap is not a
# user-tunable knob, and tests exercise the bound by generating
# enough workers to trigger the production cap (no DI seam is
# exposed on the public ``IdleWatchdog`` constructor).
_MAX_SUBAGENT_OUTPUT_CAPTURES: int = 128
_MAX_EVICTED_TOMBSTONES: int = _MAX_SUBAGENT_OUTPUT_CAPTURES

def record_invocation_start(self: IdleWatchdog) -> None:
    """Record the start of the invocation.

    Reset EVERY per-invocation field so a reused watchdog cannot
    defer/fingerprint/throttle based on the previous run's state.
    Process-lifetime state (the monotonic clock handle, the
    configured policy, the injected corroborator/monitor/listener
    providers) is intentionally preserved.

    Fields reset (per-invocation semantics):
      * ``_last_activity`` -- baseline for the stdout activity line
      * ``_invocation_started_at`` -- invocation timestamp
      * ``_last_meaningful_output_at`` / ``_has_meaningful_output``
      * ``_waiting_on_child_started_at`` /
        ``_cumulative_waiting_on_child_seconds`` --
        WAITING_ON_CHILD is per-invocation; the cumulative counter
        resets so a chained retry on the same watchdog cannot
        inherit the prior run's cumulative budget
      * ``_in_drain_window`` / ``_drain_started_at``
      * ``_last_fire_reason`` / ``_last_deferred_kind`` /
        ``_last_alive_by`` -- the fire history belongs to the
        previous run, NOT the new invocation
      * ``_last_deferred_log_at`` /
        ``_last_any_deferred_log_at`` /
        ``_last_evidence_deferral_log_at`` -- the per-key log
        throttle maps MUST survive long-lived WAITING runs but
        MUST NOT carry state across invocations (different run =
        different operator-relevant history); the coarse
        per-``fire_reason`` map (``_last_any_deferred_log_at``)
        shares the per-invocation reset semantics with the
        per-tuple map (``_last_deferred_log_at``) and the
        per-channel evidence map (``_last_evidence_deferral_log_at``)
      * ``_last_waiting_status_at`` /
        ``_suspicion_announced_for_run`` -- the suspicion /
        status emit cadence is per-invocation
      * ``_last_tool_result_at`` /
        ``_awaiting_post_tool_result_progression`` -- the
        post-tool-result wedge state is per-invocation
      * Per-channel evidence counters and timestamps
        (``_mcp_tool_call_count`` / ``_last_mcp_tool_call_at``,
        ``_subagent_progress_count`` / ``_last_subagent_progress_at``,
        ``_subagent_output_count`` / ``_last_subagent_output_at``,
        ``_workspace_event_count_internal`` /
        ``_last_workspace_event_at`` /
        ``_last_workspace_event_weight`` /
        ``_workspace_kind_counts``) -- each channel's evidence
        state is per-invocation; a stale channel from the prior
        run could otherwise defer a fresh fire incorrectly
      * ``_last_subagent_progress_emit_at`` -- the
        SUBAGENT_PROGRESS waiting-status emit throttle timestamp
        is per-invocation (the channel-evidence timestamp is
        already cleared above; this is the separate emit-cadence
        timestamp the gate consults in ``_handle_waiting_branch``)
      * ``_last_subagent_progress_description`` /
        ``_default_subagent_activity_listener``
      * ``_subagent_output_captures`` -- the capture cache is
        per-invocation (each run opens new child PIDs)
      * ``_evicted_worker_tombstones`` -- the eviction tombstone
        is per-invocation (a tombstoned worker ID from the prior
        run would suppress fresh output on the new run)
      * ``_entry_corroboration`` -- the entry corroboration is
        captured at run-start; the previous run's entry is stale
      * ``_last_progress_fingerprint`` -- the progress-repeat
        fingerprint is per-invocation (a fingerprint from the
        previous run would cause a same-fingerprint line in the
        new run to be skipped as a "repeat" when it is actually
        fresh)
      * ``_last_classify_quiet_provider`` -- the per-invocation
        ``classify_quiet`` callable the gate consults on every
        non-absolute fire; stale callable from the prior run
        could make the gate read a dead run's state
    """
    now = self._clock.monotonic()
    self._last_activity = now
    self._invocation_started_at = now
    self._last_meaningful_output_at = now
    self._has_meaningful_output = False
    self._waiting_on_child_started_at = None
    self._cumulative_waiting_on_child_seconds = 0.0
    self._in_drain_window = False
    self._drain_started_at = None
    self._last_fire_reason = None
    self._last_deferred_kind = None
    self._last_alive_by = None
    self._last_subagent_progress_description = None
    self._default_subagent_activity_listener = None
    # Reset the per-key log throttle maps so a new invocation starts
    # with empty maps. The throttle MUST survive long-lived WAITING
    # runs but MUST NOT carry state across invocations (different
    # run = different operator-relevant history). The coarse
    # per-``fire_reason`` map (``_last_any_deferred_log_at``) is
    # reset here alongside the per-tuple map
    # (``_last_deferred_log_at``) and the per-channel evidence map
    # (``_last_evidence_deferral_log_at``); without this reset, a
    # fresh invocation can inherit the prior run's coarse throttle
    # timestamps and incorrectly suppress its first human-visible
    # deferred-status log (R6 per-invocation semantics).
    self._last_deferred_log_at = {}
    self._last_any_deferred_log_at = {}
    self._last_evidence_deferral_log_at = {}
    self._last_waiting_status_at = None
    self._suspicion_announced_for_run = False
    self._last_tool_result_at = None
    self._awaiting_post_tool_result_progression = False
    # Per-channel evidence state: counters, last_at timestamps, and
    # the workspace kind counters. All cleared so a reused watchdog
    # starts from a clean baseline; otherwise a previous run's
    # fresh channel could defer a fresh fire incorrectly on the
    # new invocation.
    self._mcp_tool_call_count = 0
    self._last_mcp_tool_call_at = None
    self._subagent_progress_count = 0
    self._last_subagent_progress_at = None
    self._last_subagent_progress_emit_at = None
    self._subagent_output_count = 0
    self._last_subagent_output_at = None
    self._workspace_event_count_internal = 0
    self._last_workspace_event_at = None
    self._last_workspace_event_weight = 0.0
    self._workspace_kind_counts = {}
    self._subagent_output_captures = OrderedDict()
    self._evicted_worker_tombstones = OrderedDict()
    self._entry_corroboration = None
    self._last_progress_fingerprint = None
    self._classify_quiet_provider = None


def diagnostic_snapshot(self: IdleWatchdog, now: float | None = None) -> dict[str, object]:
    """Return a JSON-serializable dict of the watchdog's full state.

    The snapshot is a PURE READ of watchdog state (no side effects).
    The only clock touch is the injected ``self._clock.monotonic()``
    when ``now`` is None; tests pass an explicit ``now`` to drive
    the FakeClock deterministically without time travel.

    Shape (forward-compatible; ``None`` when the field has never
    been populated):

    - ``last_fire_reason``: ``str | None`` (WatchdogFireReason.value)
    - ``last_deferred_kind``: ``str | None`` (StuckKind.value)
    - ``last_alive_by``: ``str | None`` (AliveBy.value)
    - ``idle_elapsed_seconds``: ``float``
    - ``invocation_elapsed_seconds``: ``float``
    - ``cumulative_waiting_on_child_seconds``: ``float``
    - ``last_subagent_progress_description``: ``str | None``
    - ``live_subagent_count``: ``int`` (0 when no monitor)
    - ``subagent_progress_count``: ``int``
    - ``subagent_output_count``: ``int``
    - ``mcp_tool_call_count``: ``int``
    - ``workspace_event_count``: ``int``
    - ``evidence_summary``: ``list[dict[str, object]]`` (per-channel)
    - ``resumable_session_id``: ``str | None`` (None; populated
      externally by the watchdog kill path that captures the
      subprocess transport session id)
    """
    timestamp = now if now is not None else self._clock.monotonic()
    live_subagent_count = (
        self._process_monitor.live_subagent_count()
        if self._process_monitor is not None
        else 0
    )
    snapshot: dict[str, object] = {
        "last_fire_reason": (
            self._last_fire_reason.value
            if self._last_fire_reason is not None
            else None
        ),
        "last_deferred_kind": (
            self._last_deferred_kind.value
            if self._last_deferred_kind is not None
            else None
        ),
        "last_alive_by": (
            self._last_alive_by.value if self._last_alive_by is not None else None
        ),
        "idle_elapsed_seconds": round(self.idle_elapsed_seconds(timestamp), 1),
        "invocation_elapsed_seconds": round(self.invocation_elapsed_seconds, 1),
        "cumulative_waiting_on_child_seconds": round(
            self._cumulative_waiting_on_child_seconds, 1
        ),
        "last_subagent_progress_description": self._last_subagent_progress_description,
        "live_subagent_count": live_subagent_count,
        "subagent_progress_count": self._subagent_progress_count,
        "subagent_output_count": self._subagent_output_count,
        "mcp_tool_call_count": self._mcp_tool_call_count,
        "workspace_event_count": self._workspace_event_count_internal,
        "evidence_summary": self.last_evidence_summary(timestamp).to_dict_list(),
        "resumable_session_id": None,
    }
    return snapshot


def record_activity(self: IdleWatchdog) -> None:
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
    self._last_meaningful_output_at = self._clock.monotonic()
    self._has_meaningful_output = True


def record_tool_call_activity(
    self: IdleWatchdog,
    tool_name: str,
    tool_args: object,
) -> None:
    """Record a tool-call observation for the tool-call circuit breaker.

    New seam added to feed :meth:`RepetitionTracker.mark_tool_call`
    from real production call sites so an agent wedged in an
    identical-tool-call retry loop (the same ``Bash`` command with
    the same arguments re-issued N times without producing forward
    progress) trips
    :data:`WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL`.

    Deliberately does NOT reset the idle baseline: identical
    tool-call wedges must still let the idle deadline advance
    (so a silent-after-wedge agent is also caught) while the
    tool-call rule catches the fast retry storm well before
    the idle timeout.  The cumulative WAITING_ON_CHILD run is
    still flushed for bookkeeping parity.

    Args:
        tool_name: The tool name (e.g. ``"Bash"``).  Empty / None
            tool names are coerced to ``"unknown"`` inside the
            tracker so the fingerprint is always well-formed.
        tool_args: The tool arguments (any JSON-serializable
            structure).  ``None`` is treated as an empty dict
            inside the tracker.  ``sort_keys=True`` ensures
            dict-key ordering does not affect the fingerprint.
    """
    now = self._clock.monotonic()
    self._accumulate_waiting_run(now)
    self._repetition_tracker.mark_tool_call(tool_name, tool_args)


def record_tool_result_activity(self: IdleWatchdog) -> None:
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


def record_subagent_work(
    self: IdleWatchdog,
    now: float | None = None,
    *,
    description: str | None = None,
) -> None:
    """Record a subagent work activity signal (subagent_output channel).

    Increments the subagent_output first-party channel counter and updates
    the per-channel ``_last_at`` timestamp. Does NOT touch
    ``_last_activity`` (the stdout baseline). The verdict hook in
    ``evaluate()`` defers a NO_OUTPUT_DEADLINE fire while this channel is
    fresher than the configured ``activity_evidence_ttl_seconds``.

    A subagent that exists but has produced no tool calls, no progress
    signals, and no file changes for the full TTL is NOT evidence of
    progress — its channel becomes stale and the watchdog returns to
    the normal idle path.

    Args:
        now: Optional monotonic timestamp override; tests use this to
            drive FakeClock without time travel. Defaults to the
            watchdog's injected clock.
        description: Optional short string describing the subagent
            activity being recorded (e.g. the raw line that triggered
            the activity sink). Truncated to 200 chars and surfaced
            via the ``subagent_activity`` field on subsequent
            ``WaitingStatusEvent`` instances so operators can see the
            most recent subagent signal at the moment of any event.
    """
    timestamp = now if now is not None else self._clock.monotonic()
    self._subagent_progress_count += 1
    self._last_subagent_progress_at = timestamp
    if description is not None:
        sanitized = _sanitize_subagent_description(description)
        self._last_subagent_progress_description = sanitized


def poll_subagent_output(self: IdleWatchdog, now: float | None = None) -> int:
    """Poll observable subagent output streams and record new lines.

    Uses the injected ``ProcessMonitor`` to discover subagent log files and
    reads only new lines since the last poll. Each new line advances the
    ``subagent_output`` first-party channel.

    Args:
        now: Optional monotonic timestamp override.

    Returns:
        Number of new lines observed across all workers.
    """
    if self._process_monitor is None:
        return 0
    timestamp = now if now is not None else self._clock.monotonic()
    try:
        captures = self._process_monitor.discover_subagent_outputs()
    except Exception:
        self._log.debug(
            "idle watchdog: process_monitor.discover_subagent_outputs raised (suppressed)"
        )
        return 0
    total = 0
    # wt-024 M7 (AC-04): HARD FIFO bound + tombstone.
    #
    # The cache is hard-bounded at ``_MAX_SUBAGENT_OUTPUT_CAPTURES``.
    # When the cap binds (live worker count exceeds cap), the
    # OLDEST-INSERTED worker is evicted regardless of whether it is
    # still live. There is no LRU refresh on poll -- the cache uses
    # pure FIFO eviction so the bound holds across an entire
    # invocation regardless of how many times each worker is polled.
    #
    # To preserve the no-duplicate-output property of stateful
    # ``SubagentOutputCapture`` implementations (the production
    # ``FileSubagentOutputCapture`` tracks a per-worker read byte
    # offset and would re-read every historical line if recreated
    # from offset 0 after eviction), evicted worker IDs are recorded
    # in a bounded ``_evicted_worker_tombstones`` map. Tombstoned
    # workers are skipped for one eviction cycle so they cannot
    # immediately re-enter the cache and re-emit historical lines.
    #
    # The cap is enforced at the END of the polling pass (after all
    # live workers have been polled) so the public surface still
    # reports EVERY worker's lines for the current tick (a
    # high-fan-out tick is not a sampling cap; only the next-tick
    # cache state is bounded).
    live_worker_ids = set(captures.keys())
    # Drop tombstone entries for workers that are no longer in the
    # discovery result (they have actually exited, so the tombstone
    # can release its memory). Workers still alive stay tombstoned
    # so they are skipped on this poll.
    for tombstoned_worker_id in list(self._evicted_worker_tombstones.keys()):
        if tombstoned_worker_id not in live_worker_ids:
            del self._evicted_worker_tombstones[tombstoned_worker_id]
    for worker_id, fresh_capture in captures.items():
        # Skip tombstoned workers: their stateful read position was
        # lost on eviction, so re-adding them now would re-read every
        # historical line. They will be reconsidered after the
        # tombstone cycles out (when a still-live worker is evicted
        # and pushes them out, OR when they are no longer in the
        # discovery result and the tombstone cleanup releases them).
        if worker_id in self._evicted_worker_tombstones:
            continue
        # Reuse existing capture if present (preserves stateful read
        # position across polls). Only insert the fresh capture when
        # the worker is new to the cache. The OrderedDict retains
        # insertion order so pure FIFO eviction (popitem(last=False))
        # removes the OLDEST-INSERTED worker when the cap binds.
        if worker_id not in self._subagent_output_captures:
            self._subagent_output_captures[worker_id] = fresh_capture
        try:
            lines = self._subagent_output_captures[worker_id].read_lines(worker_id)
        except Exception:
            self._log.debug("idle watchdog: subagent output capture raised (suppressed)")
            continue
        if lines:
            total += len(lines)
    # Hard FIFO cap enforcement: evict the oldest-inserted entries
    # until the cache is at or below the cap. Evicted worker IDs
    # go into the bounded tombstone so they cannot be re-added on
    # the next poll and re-emit historical lines.
    while len(self._subagent_output_captures) > _MAX_SUBAGENT_OUTPUT_CAPTURES:
        evicted_worker_id, _ = self._subagent_output_captures.popitem(last=False)
        self._evicted_worker_tombstones[evicted_worker_id] = None
    # Bound the tombstone itself so a long-lived watchdog tick that
    # keeps evicting FIFO workers cannot grow it past its cap. The
    # tombstone is also FIFO so the oldest-evicted workers are
    # dropped first (they have been waiting the longest and a still-
    # live worker that has been evicted more recently has higher
    # priority for the cooldown).
    while len(self._evicted_worker_tombstones) > _MAX_EVICTED_TOMBSTONES:
        self._evicted_worker_tombstones.popitem(last=False)
    # Drop dead-worker entries (workers absent from the latest
    # discovery result). These workers' captures were never polled
    # for new lines because they no longer exist; releasing them
    # here keeps the cache tight for the next tick.
    for cached_worker_id in list(self._subagent_output_captures.keys()):
        if cached_worker_id not in live_worker_ids:
            del self._subagent_output_captures[cached_worker_id]
    if total:
        self.record_subagent_output(total, now=timestamp)
    return total


def record_workspace_event(
    self: IdleWatchdog,
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
    timestamp = now if now is not None else self._clock.monotonic()
    if weight == 0.0:
        return
    self._workspace_event_count_internal += 1
    self._last_workspace_event_at = timestamp
    self._last_workspace_event_weight = weight
    self._workspace_kind_counts[kind.value] = self._workspace_kind_counts.get(kind.value, 0) + 1


def last_evidence_summary(self: IdleWatchdog, now: float | None = None) -> EvidenceSummary:
    """Return a tier-aware per-channel evidence summary at the given time.

    Returns an ``EvidenceSummary`` containing five channels in fixed order:
    stdout (first-party), mcp_tool (first-party), subagent_output
    (first-party), subagent_liveness (side-channel), workspace
    (side-channel). Each ``ChannelEvidenceSummary`` carries the channel
    name, tier label, last observed monotonic timestamp, age in seconds,
    counter, and deferral permission.

    The summary is consumed by the watchdog's own verdict hook
    (via ``_channel_evidence_active``) and by the post-mortem
    diagnostic threading in the readers.

    Args:
        now: Optional monotonic timestamp override; tests use this to
            drive FakeClock without time travel. Defaults to the
            watchdog's injected clock.
    """
    timestamp = now if now is not None else self._clock.monotonic()
    return EvidenceSummary(
        channels=(
            self._channel_summary(
                ChannelName.STDOUT,
                self._last_activity,
                None,
                timestamp,
                None,
                alive_by=None,
            ),
            self._channel_summary(
                ChannelName.MCP_TOOL,
                self._last_mcp_tool_call_at,
                self._mcp_tool_call_count,
                timestamp,
                None,
                alive_by=None,
            ),
            self._subagent_output_summary(timestamp),
            self._subagent_liveness_summary(timestamp),
            self._workspace_summary(timestamp),
        )
    )


def channel_summary(
    channel_name: ChannelName,
    last_at: float | None,
    counter: int | None,
    now: float,
    kind_breakdown: dict[str, int] | None,
    alive_by: AliveBy | None = None,
    can_defer_override: bool | None = None,
) -> ChannelEvidenceSummary:
    """Build a ChannelEvidenceSummary for a single channel."""
    age: float | None = None if last_at is None else max(0.0, now - last_at)
    observed_counter: int | None = counter if counter is not None and counter > 0 else None
    can_defer = (
        can_defer_override
        if can_defer_override is not None
        else CHANNEL_DEFERS_BY_DEFAULT[channel_name]
    )
    return ChannelEvidenceSummary(
        channel_name=channel_name,
        tier=CHANNEL_TIERS[channel_name],
        last_at=last_at,
        age_seconds=age,
        counter=observed_counter,
        kind_breakdown=kind_breakdown,
        alive_by=alive_by,
        can_defer=can_defer,
    )


def subagent_liveness_summary(self: IdleWatchdog, now: float) -> ChannelEvidenceSummary:
    """Build the side-channel subagent_liveness summary.

    Uses the last subagent progress signal as a proxy for liveness when no
    process monitor is injected. When a process monitor is available, the
    watchdog consults it for live spawned subagents and records the liveness
    timestamp. The channel is side-channel and is quality-filtered: bare
    PID existence (alive_by in non-progress states) does NOT defer the
    verdict.

    ``can_defer`` is set to True ONLY when a process monitor has
    confirmed at least one live subagent (i.e. a real liveness
    signal from a real source). The classifier uses ``can_defer``
    to distinguish "live child from process monitor" (defers
    the gate so the dumb-kill protection applies) from "stale
    child from corroborator" (does NOT defer, so the
    no_progress / os_descendant_only ceilings can fire). The
    watchdog's own ``_channel_evidence_active`` continues to
    ignore this channel because the corroborator-only path has
    ``can_defer=False``.
    """
    last_at = self._last_subagent_progress_at
    counter = self._subagent_progress_count
    alive_by: AliveBy | None = None
    can_defer = False
    if self._process_monitor is not None:
        live = self._process_monitor.live_subagent_count()
        if live > 0:
            counter = max(counter, live)
            if last_at is None:
                last_at = now
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
            can_defer = True
    age: float | None = None if last_at is None else max(0.0, now - last_at)
    observed_counter: int | None = counter if counter > 0 else None
    return ChannelEvidenceSummary(
        channel_name=ChannelName.SUBAGENT_LIVENESS,
        tier=EvidenceTier.SIDE_CHANNEL,
        last_at=last_at,
        age_seconds=age,
        counter=observed_counter,
        alive_by=alive_by,
        can_defer=can_defer,
    )
