"""Queue-backed asyncio+threading-safe state→snapshot bridge."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from queue import Full, Queue
from typing import TYPE_CHECKING

from loguru import logger

from ralph.display.artifact_reader import (
    PlanSummary,
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.lifecycle_filter import is_bare_lifecycle
from ralph.display.prompt_reader import find_prompt_path, read_prompt_preview
from ralph.display.snapshot import PipelineSnapshot, snapshot_from_state

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.state import PipelineState


_DECISION_LOG_MAX = 16
_ANALYSIS_PHASES = frozenset({"development_analysis", "review_analysis"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PipelineSubscriber:
    """Receives PipelineState after each reducer reduce and enqueues a PipelineSnapshot.

    Thread and asyncio safe: notify() only calls put_nowait() which is documented
    as thread-safe and never blocks. Prompt preview and the plan artifact are
    read once at construction and cached for the lifetime of the subscriber.

    The subscriber additionally exposes record_activity, record_phase_transition,
    and record_analysis to receive lightweight presentation events that should
    flow into the same snapshot queue without breaking the notify(state) contract.
    """

    def __init__(
        self,
        *,
        queue: Queue[PipelineSnapshot],
        workspace_root: Path,
        run_id: str,
        prompt_reader: Callable[[Path], tuple[str, ...]] = read_prompt_preview,
        on_snapshot: Callable[[PipelineSnapshot], None] | None = None,
    ) -> None:
        self._queue = queue
        self._run_id = run_id
        self._workspace_root = workspace_root
        self._dropped_count = 0
        self._lock = threading.Lock()
        self._on_snapshot = on_snapshot

        prompt_path = find_prompt_path(workspace_root)
        self._prompt_path: str | None = str(prompt_path) if prompt_path is not None else None
        self._prompt_preview: tuple[str, ...] = (
            prompt_reader(prompt_path) if prompt_path is not None else ()
        )

        plan = read_plan_artifact(workspace_root) or PlanSummary()
        self._plan_summary: str | None = plan.summary
        self._plan_scope_items: tuple[str, ...] = plan.scope_items
        self._plan_total_steps: int = plan.total_steps
        self._plan_risks: tuple[str, ...] = plan.risks_mitigations
        self._last_plan_refresh_marker: int | None = self._plan_refresh_marker()

        self._previous_phase: str | None = None
        self._active_agent: str | None = None
        self._active_tool: str | None = None
        self._active_path: str | None = None
        self._active_workdir: str | None = None
        self._active_command: str | None = None
        self._last_activity_line: str | None = None
        self._analysis_phase: str | None = None
        self._analysis_decision: str | None = None
        self._analysis_reason: str | None = None
        self._decision_log: list[tuple[str, str, str, str]] = []
        self._last_state: PipelineState | None = None

    @property
    def queue(self) -> Queue[PipelineSnapshot]:
        return self._queue

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def decision_log(self) -> tuple[tuple[str, str, str, str], ...]:
        with self._lock:
            return tuple(self._decision_log)

    @property
    def plan_risks(self) -> tuple[str, ...]:
        return self._plan_risks

    @property
    def last_state(self) -> PipelineState | None:
        with self._lock:
            return self._last_state

    @property
    def last_tool_name(self) -> str | None:
        """The most recently recorded tool name."""
        with self._lock:
            return self._active_tool

    @property
    def last_tool_path(self) -> str | None:
        """The most recently recorded tool path argument."""
        with self._lock:
            return self._active_path

    def notify(self, state: PipelineState) -> None:
        """Build a PipelineSnapshot from state and enqueue it non-blocking.

        Never blocks. On queue.Full, increments dropped_count silently.
        Safe to call from both sync (runner.py) and async (coordinator.py) contexts.
        """
        with self._lock:
            self._record_state_transitions_locked(state)
            self._refresh_plan_from_disk_locked(state)
            self._refresh_analysis_for_phase_change_locked(state)
            self._active_agent = state.current_agent() or self._active_agent
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

    def record_activity(  # noqa: PLR0913
        self,
        unit_id: str,
        line: str,
        agent_name: str = "",
        tool_name: str | None = None,
        path: str | None = None,
        workdir: str | None = None,
        command: str | None = None,
    ) -> None:
        """Record a lightweight agent-activity event and push a fresh snapshot."""
        del unit_id
        with self._lock:
            self._active_agent = agent_name or self._active_agent
            if tool_name is not None:
                self._active_tool = tool_name
            if path:
                self._active_path = path
            if workdir:
                self._active_workdir = workdir
            if command:
                self._active_command = command
            # Never store bare lifecycle markers as the last activity line —
            # they carry no user payload and would overwrite a richer previous value.
            if line and not is_bare_lifecycle(line):
                self._last_activity_line = line
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def record_phase_transition(self, from_phase: str, to_phase: str) -> None:
        """Record a phase transition into the decision log."""
        with self._lock:
            self._append_decision_log_locked(
                phase=from_phase,
                decision=f"→ {to_phase}",
                reason="phase transition",
            )
            snapshot = self._build_snapshot_locked(self._last_state)
        if snapshot is not None:
            self._publish(snapshot)

    def record_analysis(self, phase: str, decision: str, reason: str | None = None) -> None:
        """Record an analysis result; updates the analysis panel and decision log."""
        with self._lock:
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

    def _record_state_transitions_locked(self, state: PipelineState) -> None:
        prev = self._previous_phase
        cur = state.phase
        if prev is None or prev == cur:
            self._previous_phase = cur
            return

        if prev == "development_analysis" and cur in {"development_commit", "development"}:
            self._append_decision_log_locked(
                phase="development_analysis",
                decision="proceed" if cur == "development_commit" else "revise",
                reason=f"-> {cur}",
            )
        elif prev == "review_analysis" and cur in {"fix", "review_commit"}:
            self._append_decision_log_locked(
                phase="review_analysis",
                decision="revise" if cur == "fix" else "proceed",
                reason=f"-> {cur}",
            )
        elif cur in {"complete", "failed"}:
            self._append_decision_log_locked(
                phase=cur,
                decision=cur,
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
        plan = read_plan_artifact(self._workspace_root) or PlanSummary()
        self._plan_summary = plan.summary
        self._plan_scope_items = plan.scope_items
        self._plan_total_steps = plan.total_steps
        self._plan_risks = plan.risks_mitigations
        self._last_plan_refresh_marker = marker

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
        if cur in _ANALYSIS_PHASES or prev in _ANALYSIS_PHASES:
            for drain in (cur, prev):
                if drain in _ANALYSIS_PHASES:
                    summary = read_latest_analysis_decision(self._workspace_root, drain)
                    if summary is not None:
                        self._analysis_phase = drain
                        self._analysis_decision = summary.decision
                        self._analysis_reason = summary.reason

    def _append_decision_log_locked(
        self,
        *,
        phase: str,
        decision: str,
        reason: str,
    ) -> None:
        self._decision_log.append((phase, decision, reason, _now_iso()))
        if len(self._decision_log) > _DECISION_LOG_MAX:
            self._decision_log = self._decision_log[-_DECISION_LOG_MAX:]

    def _build_snapshot_locked(
        self,
        state: PipelineState | None,
    ) -> PipelineSnapshot | None:
        if state is None:
            return None
        return snapshot_from_state(
            state,
            prompt_path=self._prompt_path,
            prompt_preview=self._prompt_preview,
            run_id=self._run_id,
            plan_summary=self._plan_summary,
            plan_scope_items=self._plan_scope_items,
            plan_total_steps=self._plan_total_steps,
            plan_current_step=None,
            plan_risks=self._plan_risks,
            active_agent=self._active_agent,
            active_tool=self._active_tool,
            active_path=self._active_path,
            active_workdir=self._active_workdir,
            active_command=self._active_command,
            last_activity_line=self._last_activity_line,
            analysis_phase=self._analysis_phase,
            analysis_decision=self._analysis_decision,
            analysis_reason=self._analysis_reason,
            decision_log=tuple(self._decision_log),
        )

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
