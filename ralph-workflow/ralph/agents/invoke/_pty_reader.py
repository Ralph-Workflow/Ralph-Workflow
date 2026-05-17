"""PTY-based process line reader for interactive Claude mode."""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import sys
import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger
from tqdm import tqdm

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
from ralph.agents.invoke._completion import _check_process_result, _CompletionCheckOptions
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    InactivityTimeoutOpts,
    InteractivePermissionPromptError,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._process_reader import (
    _MAX_PARSED_OUTPUT_LINES,
    _NON_MEANINGFUL_ACTIVITY_KINDS,
    _TERMINAL_PROCESS_STATUSES,
    _agent_command_name,
    _subprocess_env,
)
from ralph.agents.invoke._pty_helpers import (
    _MENU_QUIESCENCE_SECONDS,
    _extract_choice_menu_state,
    _interactive_auto_response_for_prompt,
    _is_auto_mode_menu_snapshot,
    _is_permission_prompt_line,
    _pending_vt_snapshot_line,
    _permission_prompt_action_message,
    _split_complete_vt_lines,
    _visible_tui_text,
    _write_pty_input,
)
from ralph.agents.invoke._session import (
    _EXPLICIT_COMPLETION_MARKER,
    _TURN_BOUNDARY_MARKER,
    _bounded_output_lines,
    _extract_session_id_from_line,
)
from ralph.agents.invoke._types import _AgentRunCtx, _PtyExtras
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.process.child_liveness import AliveBy, ChildLivenessRegistry, classify_child_snapshot
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import (
    ManagedPtyProcess,
    ProcessEvent,
    PtySpawnOptions,
    get_process_manager,
)
from ralph.process.pty import read_master_chunk, wait_for_master_readable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _find_claude_transcript_path(session_id: str) -> Path | None:
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return None
    target_name = f"{session_id}.jsonl"
    for candidate_root in projects_root.iterdir():
        candidate = candidate_root / target_name
        if candidate.is_file():
            return candidate
    return None


def _extract_message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _transcript_lines_from_assistant_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "tool_use":
            lines.append(f"claude tool: {item.get('name', 'tool')!s}\n")
        elif item_type == "text":
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"{text}\n")
    return lines


def _transcript_lines_from_user_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_dict = cast("dict[str, object]", item)
        if item_dict.get("type") != "tool_result":
            continue
        result_content = _extract_message_text(item_dict.get("content"))
        if result_content:
            lines.append(f"claude tool result: {result_content}\n")
    return lines


def _transcript_lines_from_message(
    message: object, extractor: Callable[[list[object]], list[str]]
) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return extractor(content)


def _transcript_lines_from_event(raw_line: str) -> list[str]:
    try:
        parsed = cast("object", json.loads(raw_line))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if event_type in {"permission-mode", ""}:
        return []
    if event_type == "assistant":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_assistant_content
        )
    if event_type == "user":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_user_content
        )
    return []


class _PtyLineReader:
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
        self._policy = ctx.policy
        self._monitor = ctx.monitor
        self._clock = clock
        self._strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy = (
            ctx.execution_strategy or GenericExecutionStrategy()
        )
        self._probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
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
        self._reader_done: list[bool] = [False]
        self._input_writer = os.fdopen(os.dup(handle.master_fd), "wb", buffering=0)
        self._input_writer_lock = threading.Lock()
        self._auto_mode_prompt_seen = False
        self._auto_response_menu_seen = False
        self._auto_mode_menu_screen: str | None = None
        self._last_auto_mode_response_at: float | None = None
        self._last_auto_mode_menu_seen_at: float | None = None
        self._pending_permission_prompt_line: str | None = None
        self._pending_permission_prompt_started_at: float | None = None

    def _start_thread(self, target: Callable[[], None]) -> threading.Thread:
        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _on_waiting_event(self, evt: WaitingStatusEvent) -> None:
        if evt.kind == WaitingStatusKind.HARD_STOP:
            self._last_hard_stop[0] = evt
        if self._waiting_listener is not None:
            self._waiting_listener(evt)

    def _corroborate(self) -> CorroborationSnapshot:
        ws_count: int | None = self._monitor.event_count if self._monitor is not None else None
        oldest_secs: float | None = None
        scoped_active: bool | None = None
        scoped_count: int | None = None
        try:
            desc_count, desc_oldest = self._handle.descendant_snapshot()
            scoped_count = desc_count
            scoped_active = desc_count > 0
            oldest_secs = desc_oldest
        except Exception:
            logger.debug("corroborator: PTY process scan failed (suppressed)")
        alive_by: AliveBy | None = None
        reg = cast("ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None))
        if reg is not None:
            try:
                label_prefix = cast(
                    "str | None",
                    getattr(self._strategy, "_active_label_prefix", lambda: None)(),
                )
                reg_snap = reg.snapshot(label_prefix or "")
                verdict = classify_child_snapshot(
                    reg_snap, has_os_descendants=bool(scoped_active)
                )
                alive_by = verdict.alive_by
            except Exception:
                logger.debug("corroborator: PTY registry snapshot failed (suppressed)")
        elif scoped_active:
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        return CorroborationSnapshot(
            workspace_event_count=ws_count,
            oldest_child_seconds=oldest_secs,
            scoped_child_active=scoped_active,
            scoped_child_count=scoped_count,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_meaningful[0],
            alive_by=alive_by,
        )

    def _read_thread(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        last_snapshot: str | None = None
        try:
            while True:
                if self._handle.poll() is not None and not wait_for_master_readable(
                    self._handle.master_fd, 0.01
                ):
                    break
                if not wait_for_master_readable(self._handle.master_fd, 0.05):
                    continue
                chunk = read_master_chunk(self._handle.master_fd)
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                completed, pending = _split_complete_vt_lines(pending)
                if completed:
                    with self._lines_lock:
                        self._lines_queue.extend(completed)
                        self._lines_event.set()
                    last_snapshot = None
                    continue
                snapshot_line = _pending_vt_snapshot_line(pending)
                if snapshot_line is not None and snapshot_line != last_snapshot:
                    with self._lines_lock:
                        self._lines_queue.append(snapshot_line)
                        self._lines_event.set()
                    last_snapshot = snapshot_line
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
            self._monitor_stop.set()

    def _transcript_thread(self) -> None:
        if self._expected_session_id is None:
            return
        transcript_path: Path | None = None
        file_obj = None
        while not self._monitor_stop.is_set():
            if transcript_path is None:
                transcript_path = _find_claude_transcript_path(self._expected_session_id)
                if transcript_path is None:
                    self._monitor_stop.wait(0.1)
                    continue
                file_obj = transcript_path.open("r", encoding="utf-8", errors="replace")
            assert file_obj is not None
            line = file_obj.readline()
            if not line:
                self._monitor_stop.wait(0.1)
                continue
            emitted_lines = _transcript_lines_from_event(line)
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
                        self._input_writer, "/exit\r\n", lock=self._input_writer_lock
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
        timeout_val = (
            self._policy.max_session_seconds
            if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        with self._lines_lock:
            pending_lines = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
        hs_event = self._last_hard_stop[0]
        hard_stop_diag = hs_event.diagnostic if hs_event is not None else None
        return pending_lines, _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=hard_stop_diag,
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
            self._clock.wait_for_event(
                self._lines_event, self._policy.idle_poll_interval_seconds
            )

    def _observe_queued_line(self, queued_line: str) -> None:
        visible_line = _visible_tui_text(queued_line)
        if _extract_choice_menu_state(queued_line) is not None:
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        prompt_line_seen = "enable auto mode?" in visible_line.lower()
        menu_snapshot_seen = _is_auto_mode_menu_snapshot(queued_line)
        if prompt_line_seen or menu_snapshot_seen:
            self._auto_mode_prompt_seen = True
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        auto_response = _interactive_auto_response_for_prompt(
            queued_line,
            auto_mode_prompt_seen=self._auto_mode_prompt_seen,
        )
        if auto_response is not None:
            self._auto_response_menu_seen = True
            self._auto_mode_menu_screen = queued_line
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
                or (now - self._last_auto_mode_response_at) >= 1.0
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
                    _write_pty_input(
                        self._input_writer, response, lock=self._input_writer_lock
                    )
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
            and (
                self._clock.monotonic() - self._pending_permission_prompt_started_at
            ) >= _MENU_QUIESCENCE_SECONDS
        )
        if (
            self._pending_permission_prompt_line is not None
            and prompt_grace_exceeded
            and not self._auto_response_menu_seen
        ):
            self._handle.terminate(grace_period_s=0.5)
            raise InteractivePermissionPromptError(
                self._agent_name,
                [self._pending_permission_prompt_line],
            )

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
            self._input_writer.close()
        if self._stop_sentinel_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._stop_sentinel_path.unlink()
        unsubscribe()

    def _handle_queued_line(
        self, queued_line: str, watchdog: IdleWatchdog
    ) -> Iterator[str]:
        self._observe_queued_line(queued_line)
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is not None:
            self._last_meaningful[0] = (
                activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
            )
            watchdog.record_activity()
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
        self._clock.wait_for_event(
            self._lines_event, self._policy.idle_poll_interval_seconds
        )

    def read_lines(self) -> Iterator[str]:
        reader = self._start_thread(self._read_thread)
        transcript_reader = self._start_thread(self._transcript_thread)
        sentinel_reader = self._start_thread(self._sentinel_thread)
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
        )
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
            self._cleanup(
                [reader, transcript_reader, sentinel_reader], unsubscribe, interrupted[0]
            )


def _run_pty_and_read_lines(
    cmd: list[str],
    ctx: _AgentRunCtx,
    extras: _PtyExtras | None = None,
) -> Iterator[str]:
    _extras = extras or _PtyExtras()
    expected_session_id = _extras.expected_session_id
    if _extras.stop_sentinel_path is not None:
        with contextlib.suppress(FileNotFoundError):
            _extras.stop_sentinel_path.unlink()
    clock: Clock = ctx.clock or SystemClock()
    handle = get_process_manager().spawn_pty(
        cmd,
        PtySpawnOptions(
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            label=f"invoke:{_agent_command_name(ctx.config)}",
        ),
    )
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        lines_iter = _PtyLineReader(
            handle,
            _agent_command_name(ctx.config),
            ctx,
            clock,
            _extras,
        ).read_lines()
        parsed_output: deque[str] = deque(maxlen=_MAX_PARSED_OUTPUT_LINES)
        explicit_completion_seen = False
        captured_session_id: str | None = None
        try:
            if ctx.show_progress:
                agent_name = _agent_command_name(ctx.config)
                progress_iter = cast(
                    "Iterator[str]",
                    tqdm(
                        lines_iter,
                        desc=f"[{agent_name}]",
                        unit="line",
                        leave=False,
                        file=sys.stdout,
                    ),
                )
                for line in progress_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line
            else:
                for line in lines_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line

            if captured_session_id is None:
                captured_session_id = expected_session_id

            post_exit = PostExitWatchdog(ctx.policy, clock)
            verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
            if verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
                handle.terminate(grace_period_s=0.5)
                raise _IdleStreamTimeoutError(
                    ctx.policy.process_exit_wait_seconds,
                    WatchdogFireReason.PROCESS_EXIT_HANG,
                )
        except _IdleStreamTimeoutError as exc:
            raise AgentInactivityTimeoutError(
                _agent_command_name(ctx.config),
                exc.timeout_seconds,
                _bounded_output_lines(
                    tuple(parsed_output),
                    explicit_completion_seen=explicit_completion_seen,
                ),
                InactivityTimeoutOpts(reason=exc.reason, diagnostic=exc.diagnostic),
            ) from exc

        _check_process_result(
            handle,
            _agent_command_name(ctx.config),
            list(parsed_output),
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=ctx.workspace_path,
                liveness_probe=probe,
                policy=ctx.policy,
                required_artifact=ctx.required_artifact,
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
                evaluate_completion_fn=ctx.evaluate_completion_fn,
            ),
            _clock=clock,
        )
