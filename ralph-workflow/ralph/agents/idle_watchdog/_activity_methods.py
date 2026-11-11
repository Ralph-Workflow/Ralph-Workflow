
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

KNOWN_TOOL_CALL_VERBS: frozenset[str] = frozenset(
    {
        "tool_use",
        "tool_result",
        "mcp_tool",
        "subagent",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "webfetch",
        "websearch",
    }
)


def parse_tool_call_from_description(description: str | None) -> str | None:
    if not description:
        return None
    head, sep, _tail = description.partition(":")
    if not sep:
        return None
    if head not in KNOWN_TOOL_CALL_VERBS:
        return None
    return head


if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog

_MAX_SUBAGENT_OUTPUT_CAPTURES: int = 128
_MAX_EVICTED_TOMBSTONES: int = _MAX_SUBAGENT_OUTPUT_CAPTURES


def record_invocation_start(self: IdleWatchdog) -> None:
    now = self._clock.monotonic()
    self._last_activity = now
    self._invocation_started_at = now
    self._last_meaningful_output_at = now
    self._has_meaningful_output = False
    self._any_output_count = 0
    self._observed_output_bytes = 0
    self._captured_session_id = None
    self._process_alive = True
    self._process_exit_observed_at = None
    self._waiting_on_child_started_at = None
    self._cumulative_waiting_on_child_seconds = 0.0
    self._in_drain_window = False
    self._drain_started_at = None
    self._last_fire_reason = None
    self._last_deferred_kind = None
    self._last_alive_by = None
    self._last_subagent_progress_description = None
    self._default_subagent_activity_listener = None
    self._last_deferred_log_at = {}
    self._last_any_deferred_log_at = {}
    self._last_evidence_deferral_log_at = {}
    self._last_waiting_status_at = None
    self._suspicion_announced_for_run = False
    self._last_tool_result_at = None
    self._awaiting_post_tool_result_progression = False
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
    self._set_stall(active=False, now=now, idle_elapsed=0.0)


def record_invocation_end(self: IdleWatchdog) -> None:
    now = self._clock.monotonic()
    self._set_stall(
        active=False,
        now=now,
        idle_elapsed=max(0.0, now - self._last_activity),
    )


def diagnostic_snapshot(self: IdleWatchdog, now: float | None = None) -> dict[str, object]:
    timestamp = now if now is not None else self._clock.monotonic()
    live_subagent_count = (
        self._process_monitor.live_subagent_count() if self._process_monitor is not None else 0
    )
    snapshot: dict[str, object] = {
        "last_activity_kind": _latest_activity_kind(self),
        "last_activity_at": _latest_activity_at(self),
        "last_fire_reason": (
            self._last_fire_reason.value if self._last_fire_reason is not None else None
        ),
        "last_deferred_kind": (
            self._last_deferred_kind.value if self._last_deferred_kind is not None else None
        ),
        "last_alive_by": (self._last_alive_by.value if self._last_alive_by is not None else None),
        "idle_elapsed_seconds": round(self.idle_elapsed_seconds(timestamp), 1),
        "invocation_elapsed_seconds": round(self.invocation_elapsed_seconds, 1),
        "cumulative_waiting_on_child_seconds": round(self._cumulative_waiting_on_child_seconds, 1),
        "last_subagent_progress_description": self._last_subagent_progress_description,
        "last_subagent_progress_at": self._last_subagent_progress_at,
        "current_subagent_tool_call": parse_tool_call_from_description(
            self._last_subagent_progress_description
        ),
        "live_subagent_count": live_subagent_count,
        "subagent_progress_count": self._subagent_progress_count,
        "subagent_output_count": self._subagent_output_count,
        "mcp_tool_call_count": self._mcp_tool_call_count,
        "workspace_event_count": self._workspace_event_count_internal,
        "evidence_summary": self.last_evidence_summary(timestamp).to_dict_list(),
        "resumable_session_id": None,
    }
    return snapshot


def _latest_activity_at(self: IdleWatchdog) -> float | None:
    return max(
        (
            timestamp
            for timestamp in (
                self._last_activity,
                self._last_mcp_tool_call_at,
                self._last_subagent_progress_at,
                self._last_subagent_output_at,
                self._last_workspace_event_at,
            )
            if timestamp is not None
        ),
        default=None,
    )


def _latest_activity_kind(self: IdleWatchdog) -> str | None:
    candidates = (
        ("stdout", self._last_activity),
        ("mcp_tool", self._last_mcp_tool_call_at),
        ("subagent_output", self._last_subagent_progress_at),
        ("subagent_output", self._last_subagent_output_at),
        ("workspace", self._last_workspace_event_at),
    )
    latest_kind: str | None = None
    latest_at: float | None = None
    for kind, timestamp in candidates:
        if timestamp is not None and (latest_at is None or timestamp >= latest_at):
            latest_kind = kind
            latest_at = timestamp
    return latest_kind


def record_activity(self: IdleWatchdog) -> None:
    self._reset_idle_baseline()
    self._repetition_tracker.note_progress()
    self._last_meaningful_output_at = self._clock.monotonic()
    self._has_meaningful_output = True


def record_tool_call_activity(
    self: IdleWatchdog,
    tool_name: str,
    tool_args: object,
) -> None:
    now = self._clock.monotonic()
    self._accumulate_waiting_run(now)
    self._repetition_tracker.mark_tool_call(tool_name, tool_args)


def record_tool_result_activity(self: IdleWatchdog, *, is_harness_echo: bool = False) -> None:
    now = self._clock.monotonic()
    self._accumulate_waiting_run(now)
    if not is_harness_echo:
        self._reset_idle_baseline()
        self._last_meaningful_output_at = now
        self._has_meaningful_output = True
    self._last_tool_result_at = now
    self._awaiting_post_tool_result_progression = True


def record_subagent_work(
    self: IdleWatchdog,
    now: float | None = None,
    *,
    description: str | None = None,
) -> None:
    timestamp = now if now is not None else self._clock.monotonic()
    self._subagent_progress_count += 1
    self._last_subagent_progress_at = timestamp
    if description is not None:
        sanitized = _sanitize_subagent_description(description)
        self._last_subagent_progress_description = sanitized


def poll_subagent_output(self: IdleWatchdog, now: float | None = None) -> int:
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
    live_worker_ids = set(captures.keys())
    for tombstoned_worker_id in list(self._evicted_worker_tombstones.keys()):
        if tombstoned_worker_id not in live_worker_ids:
            del self._evicted_worker_tombstones[tombstoned_worker_id]
    for worker_id, fresh_capture in captures.items():
        if worker_id in self._evicted_worker_tombstones:
            continue
        if worker_id not in self._subagent_output_captures:
            self._subagent_output_captures[worker_id] = fresh_capture
        try:
            lines = self._subagent_output_captures[worker_id].read_lines(worker_id)
        except Exception:
            self._log.debug("idle watchdog: subagent output capture raised (suppressed)")
            continue
        if lines:
            total += len(lines)
    while len(self._subagent_output_captures) > _MAX_SUBAGENT_OUTPUT_CAPTURES:
        evicted_worker_id, _ = self._subagent_output_captures.popitem(last=False)
        self._evicted_worker_tombstones[evicted_worker_id] = None
    while len(self._evicted_worker_tombstones) > _MAX_EVICTED_TOMBSTONES:
        self._evicted_worker_tombstones.popitem(last=False)
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
    timestamp = now if now is not None else self._clock.monotonic()
    if weight == 0.0:
        return
    self._workspace_event_count_internal += 1
    self._last_workspace_event_at = timestamp
    self._last_workspace_event_weight = weight
    self._workspace_kind_counts[kind.value] = self._workspace_kind_counts.get(kind.value, 0) + 1


def last_evidence_summary(self: IdleWatchdog, now: float | None = None) -> EvidenceSummary:
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
