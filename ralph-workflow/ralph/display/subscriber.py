"""Queue-backed asyncio+threading-safe state→snapshot bridge."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Full, Queue
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.display.artifact_reader import (
    PlanSummary,
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.lifecycle_filter import is_bare_lifecycle
from ralph.display.prompt_reader import find_prompt_path, read_prompt_preview
from ralph.display.snapshot import PipelineSnapshot, SnapshotContext, snapshot_from_state
from ralph.policy.models import ROLE_REVIEW
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


_DECISION_LOG_MAX = 16


@dataclass
class _VisibleActivityState:
    active_agent: str | None = None
    active_tool: str | None = None
    active_path: str | None = None
    active_unit_id: str | None = None
    active_workdir: str | None = None
    active_command: str | None = None
    active_pattern: str | None = None
    active_tool_repeat: int = 0
    last_activity_line: str | None = None
    waiting_status_line: str | None = None


class _WaitingEventLike(Protocol):
    cumulative_seconds: float
    ceiling_seconds: float
    current_run_seconds: float
    diagnostic: dict[str, object]
    subagent_activity: str | None
    kind: _WaitingKindLike


class _WaitingKindLike(Protocol):
    ENTERED: object
    PROGRESS: object
    SUSPECTED_FROZEN: object
    HARD_STOP: object
    EXITED: object
    name: str


def _resolve_waiting_types() -> tuple[type[_WaitingEventLike], type[_WaitingKindLike]]:
    """Resolve ``WaitingStatusEvent`` / ``WaitingStatusKind`` lazily.

    Importing ``ralph.agents.idle_watchdog`` at module load time would close a
    cycle through ``ralph.agents.execution_state`` ->
    ``ralph.agents.parsers.claude_interactive`` ->
    ``ralph.display.vt_normalizer`` -> ``ralph.display`` -> this module.
    """
    from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind

    return cast("type[_WaitingEventLike]", WaitingStatusEvent), cast(
        "type[_WaitingKindLike]", WaitingStatusKind
    )


def _format_waiting_status_line(event: object) -> str:
    """Build the human-readable line for a WaitingStatusEvent."""
    waiting_event_cls, waiting_kind_cls = _resolve_waiting_types()
    assert isinstance(event, waiting_event_cls)
    cast_event = event
    cum = f"{cast_event.cumulative_seconds:.0f}"
    ceil = f"{cast_event.ceiling_seconds:.0f}"
    run = f"{cast_event.current_run_seconds:.0f}"
    subagent_part = _format_subagent_activity_suffix(cast_event.subagent_activity)
    if cast_event.kind == waiting_kind_cls.ENTERED:
        return f"Background child work started waiting (cumulative={cum}s, ceiling={ceil}s)"
    if cast_event.kind == waiting_kind_cls.PROGRESS:
        delta = cast_event.diagnostic.get("workspace_event_delta")
        alive_by = cast_event.diagnostic.get("alive_by")
        parts = [f"run={run}s", f"cumulative={cum}s", f"ceiling={ceil}s"]
        if delta is not None:
            parts.append(f"workspace_events_since_wait={delta}")
        if alive_by is not None:
            parts.append(f"alive_by={alive_by}")
        base = f"Background child work still active ({', '.join(parts)})"
        return base + subagent_part
    if cast_event.kind == waiting_kind_cls.SUSPECTED_FROZEN:
        evidence = str(cast_event.diagnostic.get("evidence", "unknown"))
        alive_by = cast_event.diagnostic.get("alive_by")
        suffix = f", alive_by={alive_by}" if alive_by is not None else ""
        base = (
            f"Background child work may be frozen"
            f" (cumulative={cum}s, ceiling={ceil}s, evidence={evidence}{suffix})"
        )
        return base + subagent_part
    if cast_event.kind == waiting_kind_cls.EXITED:
        return f"Background child work resumed activity (run={run}s, cumulative={cum}s)"
    scoped = cast_event.diagnostic.get("scoped_child_active", "?")
    oldest_val = cast_event.diagnostic.get("oldest_child_seconds")
    oldest_part = (
        f", oldest_child_seconds={float(cast('int | float', oldest_val)):.0f}s"
        if oldest_val is not None
        else ""
    )
    base = (
        f"Background child work hit hard ceiling"
        f" (cumulative={cum}s, ceiling={ceil}s,"
        f" scoped_child_active={scoped}{oldest_part})"
    )
    return base + subagent_part


_SUBAGENT_ACTIVITY_MAX = 80


def _format_subagent_activity_suffix(text: str | None) -> str:
    """Return a ``subagent=<truncated>`` suffix when ``text`` is non-empty.

    Truncates to 80 chars plus an ellipsis when longer. Returns an empty
    string when ``text`` is ``None`` or whitespace-only so callers can
    append the result unconditionally without producing empty parens.

    Used by ``_format_waiting_status_line`` for the PROGRESS,
    SUSPECTED_FROZEN, and HARD_STOP event kinds. ENTERED and EXITED are
    transition markers and are excluded by design so their lines
    remain stable across operator-visible breadcrumbs and tests.
    """
    if not text or not text.strip():
        return ""
    cleaned = text.strip()
    if len(cleaned) > _SUBAGENT_ACTIVITY_MAX:
        cleaned = cleaned[:_SUBAGENT_ACTIVITY_MAX] + "..."
    return f", subagent={cleaned}"


def _with_agent_visibility(line: str, agent_name: str | None) -> str:
    """Append active agent identity to waiting-status breadcrumbs when known."""
    if not agent_name or f"agent={agent_name}" in line:
        return line
    if line.endswith(")"):
        return f"{line[:-1]}, agent={agent_name})"
    return f"{line} (agent={agent_name})"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PipelineSubscriber:
    """Receives PipelineState after each reducer reduce and enqueues a PipelineSnapshot.

    Thread and asyncio safe: notify() only calls put_nowait() which is documented
    as thread-safe and never blocks. Prompt preview and the plan artifact are
    read once at construction and cached for the lifetime of the subscriber.

    The subscriber additionally exposes record_activity and record_analysis to
    receive lightweight presentation events that should flow into the same
    snapshot queue without breaking the notify(state) contract.
    """

    def __init__(
        self,
        *,
        queue: Queue[PipelineSnapshot],
        workspace_root: Path,
        run_id: str,
        _prompt_reader: Callable[[Path], tuple[str, ...]] = read_prompt_preview,
        on_snapshot: Callable[[PipelineSnapshot], None] | None = None,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        self._queue = queue
        self._run_id = run_id
        self._workspace_root = workspace_root
        self._dropped_count = 0
        self._lock = threading.Lock()
        self._on_snapshot = on_snapshot
        self._pipeline_policy: PipelinePolicy | None = pipeline_policy

        prompt_path = find_prompt_path(workspace_root)
        self._prompt_path: str | None = str(prompt_path) if prompt_path is not None else None
        self._prompt_preview: tuple[str, ...] = (
            _prompt_reader(prompt_path) if prompt_path is not None else ()
        )

        plan = read_plan_artifact(workspace_root) or PlanSummary()
        self._plan_summary: str | None = plan.summary
        self._plan_scope_items: tuple[str, ...] = plan.scope_items
        self._plan_total_steps: int = plan.total_steps
        self._plan_risks: tuple[str, ...] = plan.risks_mitigations
        self._last_plan_refresh_marker: int | None = self._plan_refresh_marker()

        self._previous_phase: str | None = None
        self._activity = _VisibleActivityState()
        self._analysis_phase: str | None = None
        self._analysis_decision: str | None = None
        self._analysis_reason: str | None = None
        self._decision_log: list[tuple[str, str, str, str]] = []
        self._mcp_restart_count: int = 0
        self._last_state: PipelineState | None = None
        self._snapshot_cache_state: PipelineState | None = None
        self._snapshot_cache: PipelineSnapshot | None = None

    @property
    def queue(self) -> Queue[PipelineSnapshot]:
        return self._queue

    def _invalidate_snapshot_cache_locked(self) -> None:
        self._snapshot_cache_state = None
        self._snapshot_cache = None

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def decision_log(self) -> tuple[tuple[str, str, str, str], ...]:
        with self._lock:
            return tuple(self._decision_log)

    @property
    def plan_risks(self) -> tuple[str, ...]:
        with self._lock:
            return self._plan_risks

    @property
    def last_state(self) -> PipelineState | None:
        with self._lock:
            return self._last_state

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def pipeline_policy(self) -> PipelinePolicy | None:
        return self._pipeline_policy

    @property
    def last_tool_name(self) -> str | None:
        """The most recently recorded tool name."""
        with self._lock:
            return self._activity.active_tool

    @property
    def last_tool_path(self) -> str | None:
        """The most recently recorded tool path argument."""
        with self._lock:
            return self._activity.active_path

    @property
    def waiting_status_line(self) -> str | None:
        """The current waiting-status line for debug breadcrumbs."""
        with self._lock:
            return self._activity.waiting_status_line

    @property
    def _active_agent(self) -> str | None:
        return self._activity.active_agent

    @property
    def _active_tool(self) -> str | None:
        return self._activity.active_tool

    @property
    def _active_path(self) -> str | None:
        return self._activity.active_path

    @property
    def _active_unit_id(self) -> str | None:
        return self._activity.active_unit_id

    @property
    def _active_workdir(self) -> str | None:
        return self._activity.active_workdir

    @property
    def _active_command(self) -> str | None:
        return self._activity.active_command

    @property
    def _active_pattern(self) -> str | None:
        return self._activity.active_pattern

    @property
    def _active_tool_repeat(self) -> int:
        return self._activity.active_tool_repeat

    @property
    def _last_activity_line(self) -> str | None:
        return self._activity.last_activity_line

    @property
    def _waiting_status_line(self) -> str | None:
        return self._activity.waiting_status_line

    def notify(self, state: PipelineState) -> None:
        """Build a PipelineSnapshot from state and enqueue it non-blocking.

        Never blocks. On queue.Full, increments dropped_count silently.
        Safe to call from both sync (runner.py) and async (coordinator.py) contexts.
        """
        snapshot: PipelineSnapshot | None = None
        with self._lock:
            self._refresh_plan_from_disk_locked(state)
            if (
                self._last_state is state
                and self._snapshot_cache_state is state
                and self._snapshot_cache is not None
            ):
                snapshot = self._snapshot_cache
            else:
                self._record_state_transitions_locked(state)
                self._refresh_analysis_for_phase_change_locked(state)
                self._activity.active_agent = state.current_agent() or self._activity.active_agent
                self._last_state = state
                snapshot = self._build_snapshot_locked(state)
        if snapshot is not None:
            self._publish(snapshot)

    def build_snapshot(self, state: PipelineState) -> PipelineSnapshot | None:
        """Project the subscriber's accumulated state into a snapshot.

        Read-only: does not mutate any internal state. Safe to call from
        external code (such as the end-of-run summary path) without breaking
        the notify(state) contract.
        """
        with self._lock:
            return self._build_snapshot_locked(state)

    def record_activity(
        self,
        *,
        unit_id: str,
        line: str,
        agent_name: str = "",
        tool_name: str | None = None,
        path: str | None = None,
        workdir: str | None = None,
        command: str | None = None,
        pattern: str | None = None,
    ) -> None:
        with self._lock:
            self._invalidate_snapshot_cache_locked()
            self._activity.active_unit_id = unit_id
            self._activity.active_agent = agent_name or self._activity.active_agent
            # Snapshot the prior tool identity BEFORE applying updates so we can
            # detect a consecutive identical call and keep the live status fresh.
            prior_identity = (
                self._activity.active_tool,
                self._activity.active_path,
                self._activity.active_workdir,
                self._activity.active_command,
                self._activity.active_pattern,
            )
            if tool_name is not None:
                self._activity.active_tool = tool_name
            if path:
                self._activity.active_path = path
            if workdir:
                self._activity.active_workdir = workdir
            if command:
                self._activity.active_command = command
            if pattern:
                self._activity.active_pattern = pattern
            if tool_name is not None:
                new_identity = (
                    self._activity.active_tool,
                    self._activity.active_path,
                    self._activity.active_workdir,
                    self._activity.active_command,
                    self._activity.active_pattern,
                )
                if new_identity == prior_identity and prior_identity[0] is not None:
                    self._activity.active_tool_repeat += 1
                else:
                    self._activity.active_tool_repeat = 1
            # Never store bare lifecycle markers as the last activity line —
            # they carry no user payload and would overwrite a richer previous value.
            if line and not is_bare_lifecycle(line):
                self._activity.last_activity_line = line
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def record_waiting_status(
        self,
        event: object,
        *,
        unit_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Record a waiting-status event from IdleWatchdog and push a fresh snapshot."""
        waiting_event_cls, waiting_kind_cls = _resolve_waiting_types()
        if not isinstance(event, waiting_event_cls):
            return
        cast_event = event
        snapshots_to_publish: list[PipelineSnapshot] = []
        with self._lock:
            self._invalidate_snapshot_cache_locked()
            if unit_id is not None:
                self._activity.active_unit_id = unit_id
            if agent_name is not None:
                self._activity.active_agent = agent_name
            line = _with_agent_visibility(
                _format_waiting_status_line(event),
                self._activity.active_agent,
            )
            self._activity.waiting_status_line = line
            if cast_event.kind in (waiting_kind_cls.SUSPECTED_FROZEN, waiting_kind_cls.HARD_STOP):
                self._append_decision_log_locked(
                    phase=self._previous_phase or "unknown",
                    decision=cast_event.kind.name,
                    reason=line,
                )
            snapshot = self._build_snapshot_locked(self._last_state)
            if snapshot is not None:
                snapshots_to_publish.append(snapshot)
            if cast_event.kind == waiting_kind_cls.EXITED:
                self._activity.waiting_status_line = None
                cleared = self._build_snapshot_locked(self._last_state)
                if cleared is not None:
                    snapshots_to_publish.append(cleared)
        for s in snapshots_to_publish:
            self._publish(s)

    def record_analysis(self, phase: str, decision: str, reason: str | None = None) -> None:
        """Record an analysis result; updates the analysis panel and decision log."""
        with self._lock:
            self._invalidate_snapshot_cache_locked()
            self._analysis_phase = phase
            self._analysis_decision = decision
            self._analysis_reason = reason
            self._append_decision_log_locked(
                phase=phase,
                decision=decision,
                reason=reason or "",
            )
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def record_mcp_restart(self, restart_count: int) -> None:
        """Record the current MCP server restart count and push a fresh snapshot."""
        with self._lock:
            self._mcp_restart_count = restart_count
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def record_permission_prompt_action(
        self,
        *,
        agent_name: str,
        prompt_summary: str,
        selected_option: str,
    ) -> None:
        """Record an auto-answered permission prompt for visibility and auditing."""
        line = f"Ralph auto-answered permission prompt: {prompt_summary} → {selected_option}"
        with self._lock:
            self._invalidate_snapshot_cache_locked()
            self._activity.active_agent = agent_name or self._activity.active_agent
            self._activity.last_activity_line = line
            self._append_decision_log_locked(
                phase=self._previous_phase or "unknown",
                decision="permission_prompt_auto_answered",
                reason=f"{prompt_summary} -> {selected_option}",
            )
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def _record_state_transitions_locked(self, state: PipelineState) -> None:
        prev = self._previous_phase
        cur = state.phase
        if prev is None or prev == cur:
            self._previous_phase = cur
            return

        policy = self._pipeline_policy
        prev_role = policy.phases[prev].role if policy and prev in policy.phases else None
        cur_role = policy.phases[cur].role if policy and cur in policy.phases else None

        if prev_role == "analysis":
            # Determine whether the transition is a "proceed" (toward commit) or "revise"
            commit_role = "commit"
            decision = "proceed" if cur_role == commit_role else "revise"
            self._append_decision_log_locked(
                phase=prev,
                decision=decision,
                reason=f"-> {cur}",
            )
        elif prev_role == ROLE_REVIEW:
            # review role: going to execution-role means revise; commit-role means proceed
            decision = "revise" if cur_role == "execution" else "proceed"
            self._append_decision_log_locked(
                phase=prev,
                decision=decision,
                reason=f"-> {cur}",
            )
        elif cur_role == "terminal":
            cur_def = policy.phases[cur] if policy and cur in policy.phases else None
            terminal_outcome = cur_def.terminal_outcome if cur_def else None
            decision = terminal_outcome or cur
            self._append_decision_log_locked(
                phase=cur,
                decision=decision,
                reason=state.last_error or "",
            )

        if (
            self._last_state is not None
            and self._last_state.pr_url is None
            and state.pr_url is not None
        ):
            self._append_decision_log_locked(
                phase=state.phase,
                decision="pr_opened",
                reason=state.pr_url,
            )

        self._previous_phase = cur

    def _refresh_plan_from_disk_locked(self, state: PipelineState) -> None:
        del state
        marker = self._plan_refresh_marker()
        if marker == self._last_plan_refresh_marker:
            return
        self._invalidate_snapshot_cache_locked()
        plan = read_plan_artifact(self._workspace_root) or PlanSummary()
        self._plan_summary = plan.summary
        self._plan_scope_items = plan.scope_items
        self._plan_total_steps = plan.total_steps
        self._plan_risks = plan.risks_mitigations
        self._last_plan_refresh_marker = marker
        self._snapshot_cache_state = None
        self._snapshot_cache = None

    def _plan_refresh_marker(self) -> int | None:
        plan_path = self._workspace_root / ".agent" / "artifacts" / "plan.json"
        try:
            return plan_path.stat().st_mtime_ns
        except OSError:
            return None

    def _refresh_analysis_for_phase_change_locked(self, state: PipelineState) -> None:
        prev = self._previous_phase
        cur = state.phase
        if prev == cur:
            return
        policy = self._pipeline_policy
        # Refresh analysis artifact when entering or leaving an analysis-role phase
        for phase_name in (cur, prev):
            if phase_name is None:
                continue
            phase_def = policy.phases.get(phase_name) if policy else None
            if phase_def is not None and phase_def.role == "analysis":
                summary = read_latest_analysis_decision(self._workspace_root, phase_name)
                if summary is not None:
                    self._analysis_phase = phase_name
                    self._analysis_decision = summary.decision
                    self._analysis_reason = summary.reason
                    self._snapshot_cache_state = None
                    self._snapshot_cache = None

    def _append_decision_log_locked(
        self,
        *,
        phase: str,
        decision: str,
        reason: str,
    ) -> None:
        self._invalidate_snapshot_cache_locked()
        self._decision_log.append((phase, decision, reason, _now_iso()))
        if len(self._decision_log) > _DECISION_LOG_MAX:
            self._decision_log = self._decision_log[-_DECISION_LOG_MAX:]

    def _build_snapshot_locked(
        self,
        state: PipelineState | None,
    ) -> PipelineSnapshot | None:
        if state is None:
            return None
        if self._snapshot_cache_state is state and self._snapshot_cache is not None:
            return self._snapshot_cache
        snapshot = snapshot_from_state(
            state,
            SnapshotContext(
                prompt_path=self._prompt_path,
                prompt_preview=self._prompt_preview,
                run_id=self._run_id,
                pipeline_policy=self._pipeline_policy,
                plan_summary=self._plan_summary,
                plan_scope_items=self._plan_scope_items,
                plan_total_steps=self._plan_total_steps,
                plan_risks=self._plan_risks,
                active_agent=self._activity.active_agent,
                active_tool=self._activity.active_tool,
                active_path=self._activity.active_path,
                active_unit_id=self._activity.active_unit_id,
                active_workdir=self._activity.active_workdir,
                active_command=self._activity.active_command,
                active_pattern=self._activity.active_pattern,
                active_tool_repeat=self._activity.active_tool_repeat,
                last_activity_line=self._activity.last_activity_line,
                waiting_status_line=self._activity.waiting_status_line,
                analysis_phase=self._analysis_phase,
                analysis_decision=self._analysis_decision,
                analysis_reason=self._analysis_reason,
                decision_log=tuple(self._decision_log),
                mcp_restart_count=self._mcp_restart_count,
                active_process_labels=tuple(
                    r.label for r in get_process_manager().list_active() if r.label is not None
                ),
            ),
        )
        self._snapshot_cache_state = state
        self._snapshot_cache = snapshot
        return snapshot

    def _publish(self, snapshot: PipelineSnapshot) -> None:
        if self._on_snapshot is not None:
            try:
                self._on_snapshot(snapshot)
            except Exception as exc:
                logger.bind(event="subscriber_callback_error").error(
                    "callback failed: {err}", err=exc
                )
        self._enqueue(snapshot)

    def _enqueue(self, snapshot: PipelineSnapshot) -> None:
        try:
            self._queue.put_nowait(snapshot)
        except Full:
            # Silent backpressure - keep count for end-of-run diagnostics only.
            # Do NOT emit per-drop logs into the user transcript.
            self._dropped_count += 1


__all__ = ["PipelineSubscriber"]
