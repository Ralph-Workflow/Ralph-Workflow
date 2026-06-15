"""PTY line reader for interactive agent sessions."""

from __future__ import annotations

import codecs
import contextlib
import os
import threading
import time
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke._errors import (
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._process_reader import (
    _NON_MEANINGFUL_ACTIVITY_KINDS,
    _TERMINAL_PROCESS_STATUSES,
)
from ralph.agents.invoke._pty_extras import _PtyExtras
from ralph.agents.invoke._pty_helpers import (
    _MENU_QUIESCENCE_SECONDS,
    _RECENT_CHOICE_LINES_MAX,
    _extract_choice_menu_state,
    _fuzzy_contains_permission_prompt,
    _interactive_auto_response_for_prompt,
    _is_auto_mode_menu_snapshot,
    _is_permission_prompt_line,
    _pending_vt_snapshot_line,
    _permission_prompt_action_message,
    _split_complete_vt_lines,
    _visible_tui_text,
    _write_pty_input,
)
from ralph.agents.invoke._pty_transcript import (
    find_claude_transcript_entry,
    find_latest_claude_transcript_entry,
    transcript_lines_from_event,
)
from ralph.agents.invoke._session import (
    _TURN_BOUNDARY_MARKER,
    extract_visible_tui_transport_session_id,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)
from ralph.mcp.server._activity_sink import (
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.process.child_liveness import AliveBy, ChildLivenessRegistry, classify_child_snapshot
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import (
    ManagedPtyProcess,
    ProcessEvent,
    get_process_manager,
)
from ralph.process.pty import read_master_chunk, wait_for_master_readable
from ralph.process.teardown import teardown_subtree

from ._monitor_factory import _make_process_monitor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx
    from ralph.agents.timeout_clock import Clock

type _MergedDiagType = "dict[str, str | int | float | bool | list[object]] | None"


class PtyLineReader:
    def __init__(
        self,
        handle: ManagedPtyProcess,
        agent_name: str,
        ctx: _AgentRunCtx,
        clock: Clock,
        extras: _PtyExtras | None,
    ) -> None:
        _extras = extras or _PtyExtras()
        self._handle = handle
        self._agent_name = agent_name
        self._started_at_wall_clock = time.time()
        self._config = ctx.config
        self._policy = ctx.policy
        self._monitor = ctx.monitor
        self._workspace_path = cast("Path | None", getattr(ctx, "workspace_path", None))
        self._clock = clock
        self._strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy = (
            ctx.execution_strategy or GenericExecutionStrategy()
        )
        self._probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._pre_output_listener = cast(
            "Callable[[], None] | None",
            getattr(ctx, "pre_output_listener", None),
        )
        self._expected_session_id = _extras.expected_session_id
        self._stop_sentinel_path = _extras.stop_sentinel_path
        self._permission_prompt_listener = _extras.permission_prompt_listener
        self._lines_queue: list[str] = []
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._last_activity_kind: AgentActivityKind | None = None
        self._awaiting_post_tool_result_progress = False
        self._last_tool_use_name: str | None = None
        self._last_tool_result_at: float | None = None
        self._last_tool_result_excerpt: str | None = None
        self._reader_done: list[bool] = [False]
        self._input_writer_fd = os.dup(handle.master_fd)
        self._input_writer_lock = threading.Lock()
        self._auto_mode_prompt_seen = False
        self._auto_response_menu_seen = False
        self._auto_mode_menu_screen: str | None = None
        self._last_auto_mode_response_at: float | None = None
        self._last_auto_mode_menu_seen_at: float | None = None
        self._pending_permission_prompt_line: str | None = None
        self._pending_permission_prompt_started_at: float | None = None
        self._recent_choice_lines: list[str] = []
        self._transcript_session_ids: list[str] = []
        self._transcript_session_ids_lock = threading.Lock()
        if self._expected_session_id:
            self._transcript_session_ids.append(self._expected_session_id)

    def _start_thread(self, target: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _on_waiting_event(self, event: WaitingStatusEvent) -> None:
        if event.kind == WaitingStatusKind.HARD_STOP:
            self._last_hard_stop[0] = event
        if self._waiting_listener is not None:
            self._waiting_listener(event)

    def _corroborate(self) -> CorroborationSnapshot:
        workspace_event_count: int | None = (
            self._monitor.event_count if self._monitor is not None else None
        )
        last_workspace_event_at: float | None = (
            self._monitor.last_event_at if self._monitor is not None else None
        )
        oldest_child_seconds: float | None = None
        scoped_child_active: bool | None = None
        scoped_child_count: int | None = None
        try:
            descendant_count, descendant_oldest = self._handle.descendant_snapshot()
            scoped_child_count = descendant_count
            scoped_child_active = descendant_count > 0
            oldest_child_seconds = descendant_oldest
        except Exception:
            logger.debug("corroborator: PTY process scan failed (suppressed)")
        alive_by: AliveBy | None = None
        registry = cast("ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None))
        if registry is not None:
            try:
                label_prefix = cast(
                    "str | None",
                    getattr(self._strategy, "_active_label_prefix", lambda: None)(),
                )
                snapshot = registry.snapshot(label_prefix or "")
                verdict = classify_child_snapshot(
                    snapshot,
                    has_os_descendants=bool(scoped_child_active),
                )
                alive_by = verdict.alive_by
            except Exception:
                logger.debug("corroborator: PTY registry snapshot failed (suppressed)")
        elif scoped_child_active:
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        return CorroborationSnapshot(
            workspace_event_count=workspace_event_count,
            last_workspace_event_at=last_workspace_event_at,
            oldest_child_seconds=oldest_child_seconds,
            scoped_child_active=scoped_child_active,
            scoped_child_count=scoped_child_count,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_meaningful[0],
            alive_by=alive_by,
        )

    def _read_thread(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        if self._pre_output_listener is not None:
            with contextlib.suppress(Exception):
                self._pre_output_listener()
        try:
            while True:
                if self._handle.poll() is not None and not wait_for_master_readable(
                    self._handle.master_fd, 0.01
                ):
                    break
                if not wait_for_master_readable(self._handle.master_fd, 0.05):
                    continue
                try:
                    chunk = read_master_chunk(self._handle.master_fd)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                completed, pending = _split_complete_vt_lines(pending)
                if completed:
                    with self._lines_lock:
                        self._lines_queue.extend(completed)
                        self._lines_event.set()
            tail = pending + decoder.decode(b"", final=True)
            if tail:
                snapshot_line = _pending_vt_snapshot_line(tail)
                if snapshot_line is None:
                    snapshot_line = tail
                with self._lines_lock:
                    self._lines_queue.append(snapshot_line)
                    self._lines_event.set()
        except Exception:
            pass
        finally:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()

    def _record_transcript_session_id(self, raw_line: str) -> None:
        session_id = extract_visible_tui_transport_session_id(raw_line)
        if session_id is None:
            session_id = extract_visible_tui_transport_session_id(_visible_tui_text(raw_line))
        if session_id is None:
            return
        with self._transcript_session_ids_lock:
            if session_id in self._transcript_session_ids:
                self._transcript_session_ids.remove(session_id)
            self._transcript_session_ids.insert(0, session_id)

    def _transcript_session_id_candidates(self) -> tuple[str, ...]:
        with self._transcript_session_ids_lock:
            return tuple(self._transcript_session_ids)

    def _transcript_thread(self) -> None:
        if self._expected_session_id is None and self._workspace_path is None:
            return
        transcript_path: Path | None = None
        transcript_session_id: str | None = None
        file_obj = None
        transcript_parser = ClaudeInteractiveTranscriptParser()
        while not self._monitor_stop.is_set():
            candidate_ids = self._transcript_session_id_candidates()
            if transcript_path is None or (
                candidate_ids
                and transcript_session_id is not None
                and candidate_ids[0] != transcript_session_id
            ):
                entry = find_claude_transcript_entry(candidate_ids)
                if entry is None and self._workspace_path is not None:
                    entry = find_latest_claude_transcript_entry(
                        self._workspace_path,
                        min_mtime=self._started_at_wall_clock,
                    )
                if entry is None:
                    self._monitor_stop.wait(0.1)
                    continue
                next_path, matched_session_id = entry
                if transcript_path != next_path:
                    if file_obj is not None:
                        file_obj.close()
                    transcript_path = next_path
                    file_obj = transcript_path.open("r", encoding="utf-8", errors="replace")
                transcript_session_id = matched_session_id
            assert file_obj is not None
            line = file_obj.readline()
            if not line:
                self._monitor_stop.wait(0.1)
                continue
            emitted_lines = transcript_lines_from_event(line, parser=transcript_parser)
            if emitted_lines:
                with self._lines_lock:
                    self._lines_queue.extend(emitted_lines)
                    self._lines_event.set()
        if file_obj is not None:
            file_obj.close()

    def _sentinel_thread(self) -> None:
        if self._stop_sentinel_path is None:
            return
        while not self._monitor_stop.is_set():
            if self._stop_sentinel_path.exists():
                with self._lines_lock:
                    self._lines_queue.append(_TURN_BOUNDARY_MARKER + "\n")
                    self._lines_event.set()
                with contextlib.suppress(OSError):
                    _write_pty_input(
                        self._input_writer_fd, "/exit\r\n", lock=self._input_writer_lock
                    )
                return
            self._monitor_stop.wait(0.05)

    def _classify_quiet(self) -> AgentExecutionState:
        try:
            return self._strategy.classify_quiet(self._handle, self._probe)
        except Exception:
            logger.opt(exception=True).debug(
                "idle watchdog: classify_quiet raised for PTY runtime; "
                "defaulting to WAITING_ON_CHILD"
            )
            return AgentExecutionState.WAITING_ON_CHILD

    def _check_fire(
        self,
        watchdog: IdleWatchdog,
        verdict: WatchdogVerdict,
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        if verdict != WatchdogVerdict.FIRE:
            return None
        assert (
            self._policy.idle_timeout_seconds is not None
            or self._policy.max_session_seconds is not None
        )
        fire_reason = watchdog.last_fire_reason
        assert fire_reason is not None
        hard_stop_event = self._last_hard_stop[0]
        hard_stop_diag = hard_stop_event.diagnostic if hard_stop_event is not None else None
        diagnostic = hard_stop_diag
        if (
            fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
            and self._awaiting_post_tool_result_progress
            and self._last_tool_result_at is not None
        ):
            fire_reason = WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
            diagnostic = {
                "last_tool_name": self._last_tool_use_name or "tool",
                "last_tool_result_excerpt": self._last_tool_result_excerpt or "",
                "idle_since_tool_result_seconds": round(
                    self._clock.monotonic() - self._last_tool_result_at, 1
                ),
            }
        timeout_val = (
            self._policy.max_session_seconds
            if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
            else self._policy.no_progress_quiet_seconds
            if fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        with self._lines_lock:
            pending_lines = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
        pid = cast("int | None", getattr(self._handle, "pid", None))
        if pid is not None:
            teardown_subtree(pid)
        # Always merge the watchdog's per-channel evidence summary into
        # the diagnostic so a post-mortem (or the on-call operator) can
        # see exactly which evidence channels were fresh and which
        # were stale at the moment the watchdog fired. The watchdog's
        # own ``last_evidence_summary`` produces a list of
        # ``ChannelEvidenceSummary.to_dict()`` entries; we surface that
        # under ``evidence_summary`` alongside any existing diagnostic
        # (HARD_STOP or post-tool-result). ``diagnostic`` is mutable in
        # the post-tool-result rewrite above, so this merge is
        # deliberately applied AFTER that rewrite.
        merged_diag: dict[str, object] = {
            "evidence_summary": watchdog.last_evidence_summary(
                self._clock.monotonic()
            ).to_dict_list(),
        }
        if diagnostic is not None:
            for key, value in diagnostic.items():
                if key not in merged_diag:
                    merged_diag[key] = value
        return pending_lines, _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=cast("_MergedDiagType", merged_diag),
        )

    def _run_drain_window(
        self,
        watchdog: IdleWatchdog,
        drain_deadline: float | None,
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        while True:
            fire_result = self._check_fire(
                watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
            )
            if fire_result is not None:
                return fire_result
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def _observe_queued_line(self, queued_line: str) -> None:
        visible_line = _visible_tui_text(queued_line)

        # Maintain a sliding window of recent lines so cross-line menu detection
        # can work even when TUI repaint sequences fragment menu options across
        # individual queued entries.
        self._recent_choice_lines.append(queued_line)
        if len(self._recent_choice_lines) > _RECENT_CHOICE_LINES_MAX:
            self._recent_choice_lines.pop(0)

        def _check_menu(screen_text: str) -> bool:
            if _extract_choice_menu_state(screen_text) is not None:
                self._auto_mode_menu_screen = screen_text
                self._last_auto_mode_menu_seen_at = self._clock.monotonic()
                return True
            if _fuzzy_contains_permission_prompt(screen_text):
                self._auto_mode_menu_screen = screen_text
                self._last_auto_mode_menu_seen_at = self._clock.monotonic()
                return True
            return False

        menu_detected = _check_menu(queued_line)
        if not menu_detected:
            combined = "\n".join(self._recent_choice_lines)
            menu_detected = _check_menu(combined)

        prompt_line_seen = "enable auto mode?" in visible_line.lower()
        menu_snapshot_seen = _is_auto_mode_menu_snapshot(queued_line)
        if prompt_line_seen or menu_snapshot_seen:
            self._auto_mode_prompt_seen = True
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        auto_response_text = (
            "\n".join(self._recent_choice_lines) if self._recent_choice_lines else queued_line
        )
        auto_response = _interactive_auto_response_for_prompt(
            auto_response_text,
            auto_mode_prompt_seen=self._auto_mode_prompt_seen,
        )
        if auto_response is not None:
            self._auto_response_menu_seen = True
            self._auto_mode_menu_screen = auto_response_text
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        if _is_permission_prompt_line(queued_line):
            self._pending_permission_prompt_line = queued_line.rstrip()
            self._pending_permission_prompt_started_at = self._clock.monotonic()
        else:
            self._pending_permission_prompt_line = None
            self._pending_permission_prompt_started_at = None

    def _maybe_send_auto_response(self) -> None:
        if self._auto_response_menu_seen:
            now = self._clock.monotonic()
            menu_quiescent = (
                self._last_auto_mode_menu_seen_at is not None
                and (now - self._last_auto_mode_menu_seen_at) >= _MENU_QUIESCENCE_SECONDS
            )
            if menu_quiescent and (
                self._last_auto_mode_response_at is None
                or (
                    (now - self._last_auto_mode_response_at) >= 1.0
                    and self._last_auto_mode_menu_seen_at is not None
                    and self._last_auto_mode_menu_seen_at > self._last_auto_mode_response_at
                )
            ):
                with contextlib.suppress(OSError):
                    screen = self._auto_mode_menu_screen or ""
                    response = (
                        _interactive_auto_response_for_prompt(
                            screen,
                            auto_mode_prompt_seen=self._auto_mode_prompt_seen,
                        )
                        or "\r"
                    )
                    _write_pty_input(self._input_writer_fd, response, lock=self._input_writer_lock)
                    action_message = _permission_prompt_action_message(
                        screen,
                        auto_mode_prompt_seen=self._auto_mode_prompt_seen,
                    )
                    if action_message is not None:
                        logger.info(action_message)
                        if self._permission_prompt_listener is not None:
                            self._permission_prompt_listener(action_message)
                    self._pending_permission_prompt_line = None
                    self._pending_permission_prompt_started_at = None
                    self._last_auto_mode_response_at = now
        prompt_grace_exceeded = (
            self._pending_permission_prompt_started_at is not None
            and (self._clock.monotonic() - self._pending_permission_prompt_started_at)
            >= _MENU_QUIESCENCE_SECONDS
        )
        if (
            self._pending_permission_prompt_line is not None
            and prompt_grace_exceeded
            and not self._auto_response_menu_seen
        ):
            prompt_text = self._pending_permission_prompt_line
            with contextlib.suppress(OSError):
                _write_pty_input(self._input_writer_fd, "\r", lock=self._input_writer_lock)
                logger.warning(
                    "Ralph auto-answered unknown prompt with Enter. Prompt text: {}",
                    repr(prompt_text[:200]),
                )
            self._pending_permission_prompt_line = None
            self._pending_permission_prompt_started_at = None

    def _on_interrupt(self) -> None:
        self._monitor_stop.set()
        with contextlib.suppress(Exception):
            self._handle.close()

    def _cleanup(
        self,
        readers: list[threading.Thread],
        unsubscribe: Callable[[], None],
        interrupted: bool,
    ) -> None:
        self._monitor_stop.set()
        timeouts = (
            0.1 if interrupted else 10,
            0.1 if interrupted else 1,
            0.1 if interrupted else 1,
        )
        for reader, timeout in zip(readers, timeouts, strict=True):
            reader.join(timeout=timeout)
        with contextlib.suppress(Exception):
            os.close(self._input_writer_fd)
        if self._stop_sentinel_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._stop_sentinel_path.unlink()
        unsubscribe()

    def _handle_queued_line(self, queued_line: str, watchdog: IdleWatchdog) -> Iterator[str]:
        self._record_transcript_session_id(queued_line)
        self._observe_queued_line(queued_line)
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is not None:
            self._last_activity_kind = activity_signal.kind
            self._last_meaningful[0] = activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
            if activity_signal.kind == AgentActivityKind.ERROR_LINE:
                # Repeated identical errors must not reset the idle baseline or
                # the repetition streak's progress counter; they feed the
                # repeated-error circuit breaker instead.
                watchdog.record_error_activity(activity_signal.raw)
            elif activity_signal.kind == AgentActivityKind.PROGRESS_REPORT:
                # Repeated identical progress heartbeats feed the breaker; a
                # changed status counts as genuine progress (handled inside).
                watchdog.record_progress_report(activity_signal.raw)
            elif activity_signal.kind == AgentActivityKind.LIFECYCLE:
                # Cosmetic frames keep the agent off the idle deadline but do
                # NOT count as forward progress for the circuit breaker.
                watchdog.record_lifecycle_activity()
            else:
                if activity_signal.kind == AgentActivityKind.TOOL_USE:
                    self._awaiting_post_tool_result_progress = False
                    raw = activity_signal.raw.strip()
                    self._last_tool_use_name = raw.split(":", 1)[-1].strip() if ":" in raw else raw
                elif activity_signal.kind == AgentActivityKind.TOOL_RESULT:
                    self._awaiting_post_tool_result_progress = True
                    self._last_tool_result_at = self._clock.monotonic()
                    self._last_tool_result_excerpt = activity_signal.raw.strip()[:200]
                elif activity_signal.kind == AgentActivityKind.OUTPUT_LINE:
                    self._awaiting_post_tool_result_progress = False
                watchdog.record_activity()
                # NEW BEHAVIOR: also record the post-tool-result
                # activity so the watchdog's new direct-fire
                # STALLED_AFTER_TOOL_RESULT path can detect the wedge
                # in ~120s by default (the post-tool-result budget)
                # rather than waiting for the full 300s idle timeout.
                if activity_signal.kind == AgentActivityKind.TOOL_RESULT:
                    watchdog.record_tool_result_activity()
        else:
            self._last_meaningful[0] = False
        self._strategy.observe_line(queued_line)
        yield queued_line
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from pending_lines
            raise exc

    def _handle_done_path(self, watchdog: IdleWatchdog) -> Iterator[str]:
        drain_deadline = (
            self._clock.monotonic() + self._policy.drain_window_seconds
            if self._policy.drain_window_seconds
            else None
        )
        drain_result = self._run_drain_window(watchdog, drain_deadline)
        if drain_result is not None:
            pending_lines, exc = drain_result
            yield from pending_lines
            raise exc

    def _idle_check_and_wait(self, watchdog: IdleWatchdog) -> Iterator[str]:
        self._maybe_send_auto_response()
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from pending_lines
            raise exc
        self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def read_lines(self) -> Iterator[str]:
        reader = self._start_thread(self._read_thread)
        transcript_reader = self._start_thread(self._transcript_thread)
        sentinel_reader = self._start_thread(self._sentinel_thread)
        process_monitor = _make_process_monitor(self._handle, self._config, self._policy)
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
            process_monitor=process_monitor,
        )
        watchdog.record_invocation_start()

        # Register the watchdog's workspace channel recorder as the
        # on-event callback on the WorkspaceMonitor so every file
        # change in the monitored workspace is visible to the
        # activity-aware verdict as a fresh workspace channel signal.
        # The monitor is constructed in invoke_agent BEFORE the
        # watchdog is created (the watchdog lives inside this
        # generator), so we cannot bind the recorder at monitor
        # construction time; the late ``set_on_event`` binding
        # happens here, immediately after the watchdog exists. The
        # binding is cleared in the finally block below so a stale
        # callback can never fire after the run ends.
        if self._monitor is not None:
            # Forward (kind, weight) so the watchdog's per-kind
            # counter receives the real classification; the
            # 0-arg bound method form would always yield
            # (OTHER, 1.0) and miss the AC #7 contract.
            def _forward_event(
                kind: WorkspaceChangeKind, weight: float
            ) -> None:
                watchdog.record_workspace_event(kind=kind, weight=weight)

            self._monitor.set_on_event(_forward_event)

        # Register the watchdog's MCP activity recorder as the active sink
        # for the in-process Ralph MCP server so each tools/call invocation
        # defers a NO_OUTPUT_DEADLINE fire while the agent is actively
        # using the MCP. The contextvar isolates concurrent agent runs
        # in the same process so a sibling run's MCP calls never feed
        # this watchdog's evidence surface. The recorder accepts a
        # `now: float | None` argument; the sink protocol passes a
        # `tool_name: str`, so we wrap the recorder in a thin closure
        # that ignores the string and forwards to the recorder.
        def _mcp_sink(_tool_name: str) -> None:
            watchdog.record_mcp_tool_call()

        def _subagent_sink(_line: str) -> None:
            watchdog.record_subagent_work()

        sink_token = set_active_sink(_mcp_sink)
        subagent_token = set_subagent_sink(_subagent_sink)
        unsubscribe = get_process_manager().register_listener(self._on_process_event)
        interrupted = [False]
        try:
            while True:
                self._lines_event.clear()
                queued_line: str | None = None
                is_done = False
                with self._lines_lock:
                    if self._lines_queue:
                        queued_line = self._lines_queue.pop(0)
                    elif self._reader_done[0]:
                        is_done = True

                if queued_line is not None:
                    yield from self._handle_queued_line(queued_line, watchdog)
                    continue

                if is_done:
                    yield from self._handle_done_path(watchdog)
                    break

                yield from self._idle_check_and_wait(watchdog)
        except BaseException:
            interrupted[0] = True
            self._on_interrupt()
            raise
        finally:
            reset_active_sink(sink_token)
            reset_subagent_sink(subagent_token)
            if self._monitor is not None:
                self._monitor.set_on_event(None)
            self._cleanup([reader, transcript_reader, sentinel_reader], unsubscribe, interrupted[0])
