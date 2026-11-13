from __future__ import annotations

import codecs
import contextlib
import errno
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import psutil
from loguru import logger

from ralph.agents._agy_upstream_diagnostic import (
    agy_cli_log_start_offset,
    agy_fresh_cli_log_tail,
)
from ralph.agents._bounded_text_buffer import DEFAULT_MAX_BUFFER_CHARS, clamp_tail
from ralph.agents._broker_secret import _parent_broker_secret
from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.completion_signals import completion_signals_terminal
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    GenericExecutionStrategy,
    with_prompt_echo_flag,
)
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._completion import (
    completion_run_id_from_extra_env,
)
from ralph.agents.invoke._completion_termination import publish_attempt_facts
from ralph.agents.invoke._errors import (
    AgentInvocationError,
    SupervisionInfrastructureError,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._lines_queue_helpers import _pop_queue_line
from ralph.agents.invoke._process_reader import (
    _NON_MEANINGFUL_ACTIVITY_KINDS,
    _TERMINAL_PROCESS_STATUSES,
    _append_conflict_termination_diagnostic,
    _extract_tool_call_from_activity_signal,
    _is_resumable_fire_reason,
    _raise_on_relay_health_error,
    check_broken_agent_timer,
)
from ralph.agents.invoke._pty_extras import PtyExtras
from ralph.agents.invoke._pty_helpers import (
    _MAX_TRANSCRIPT_SESSION_IDS,
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
    existing_transcript_names,
    find_claude_transcript_entry,
    find_latest_claude_transcript_entry,
    transcript_lines_from_events,
)
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._session import (
    TURN_BOUNDARY_MARKER,
    extract_visible_tui_transport_session_id,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    InteractiveCompletionObserved,
    OperatorInterruptObserved,
    ProviderErrorObserved,
    SilenceObserved,
    TerminalStartupErrorObserved,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)
from ralph.config.enums import AgentTransport
from ralph.display.raw_overflow import (
    RawOverflowLog,
    get_or_create_raw_overflow_log,
    raw_log_unit_id_for,
)
from ralph.mcp.server._activity_sink import (
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.process.child_liveness import (
    AliveBy,
    ChildLivenessRegistry,
    ChildLivenessSubagentPidSource,
    classify_child_snapshot,
)
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import (
    ManagedPtyProcess,
    ProcessEvent,
    ProcessTerminationIssuer,
    get_process_manager,
)
from ralph.process.pty import read_master_chunk, wait_for_master_readable
from ralph.process.teardown import (
    teardown_subtree,
)
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.runtime_events import current_runtime_event

from ._monitor_factory import _make_process_monitor
from ._quota_detection import (
    is_provider_quota_output_line,
    is_quota_kill_evidence,
    provider_quota_log_detail_matches,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextvars import Token

    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.agents.invoke._agent_run_ctx import AgentRunCtx, _EvalCompletionFn
    from ralph.agents.invoke._subagent_transcript import R7AbsentLayoutDiagnostic
    from ralph.agents.parsers.interactive_transcript_event import (
        InteractiveTranscriptEvent,
    )
    from ralph.agents.timeout_clock import Clock
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.monitor import ProcessMonitor

from ralph.agents.invoke._subagent_transcript import ClaudeSubagentTranscriptTails

type _MergedDiagType = "dict[str, str | int | float | bool | list[object]] | None"


def _zero_idle_elapsed(_now: float) -> float:
    return 0.0


_EIO_DRAIN_MAX = 32
_EIO_DRAIN_SELECT_SECONDS = 0.005
_NANOCODER_TERMINAL_CONFIG_ERROR_PATTERNS = (
    re.compile(r"\bProvider\s+'[^']+'\s+not found in agents\.config\.json\b", re.IGNORECASE),
    re.compile(r"\bModel\s+'[^']+'\s+not found in agents\.config\.json\b", re.IGNORECASE),
)

_DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS: float = 2.0

_MAX_INTERACTIVE_EXIT_GRACE_SECONDS: float = 5.0


def _terminal_interactive_startup_error(agent_name: str, line: str) -> str | None:
    visible_line = _visible_tui_text(line).strip()
    if not visible_line:
        return None
    if agent_name.casefold() == "nanocoder" and any(
        pattern.search(visible_line) for pattern in _NANOCODER_TERMINAL_CONFIG_ERROR_PATTERNS
    ):
        return f"Nanocoder startup/configuration error: {visible_line}"
    return None


def _record_embedded_error(watchdog: IdleWatchdog, signal: AgentActivitySignal) -> None:
    if signal.error_message is not None:
        watchdog.record_error_activity(signal.error_message)


def _record_tool_result_activity(watchdog: IdleWatchdog, *, is_harness_echo: bool) -> None:
    if is_harness_echo:
        watchdog.record_tool_result_activity(is_harness_echo=True)
    else:
        watchdog.record_tool_result_activity()


def _tool_use_display_name(
    raw: str,
    tool_call: tuple[str, dict[str, object]] | None,
) -> str:
    if tool_call is not None:
        return tool_call[0]
    stripped = raw.strip()
    return stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped


class _ReadLoopSetup(NamedTuple):
    reader: threading.Thread
    transcript_reader: threading.Thread
    sentinel_reader: threading.Thread
    completion_reader: threading.Thread
    watchdog: IdleWatchdog
    subagent_tails: ClaudeSubagentTranscriptTails | None
    unsubscribe: Callable[[], None]
    sink_token: Token[Callable[[str], None] | None]
    subagent_token: Token[Callable[[str], None] | None]


def _resolve_pre_existing_transcript_names(
    extras: PtyExtras, workspace_path: Path | None
) -> frozenset[str]:
    snapshot = cast("frozenset[str] | None", getattr(extras, "pre_existing_transcript_names", None))
    if snapshot is not None:
        return snapshot
    if workspace_path is not None:
        return existing_transcript_names(workspace_path)
    return frozenset()


class PtyLineReader:
    def __init__(
        self,
        handle: ManagedPtyProcess,
        agent_name: str,
        ctx: AgentRunCtx,
        clock: Clock,
        extras: PtyExtras | None,
        *,
        max_pending_chars: int = DEFAULT_MAX_BUFFER_CHARS,
        agy_cli_log: tuple[Path, int] | None = None,
        supervisor: AttemptSupervisor | None = None,
    ) -> None:
        _extras = extras or PtyExtras()
        self._handle = handle
        self._supervisor = supervisor
        self._agent_name = agent_name
        self._started_at_wall_clock = time.time()
        self._config = ctx.config
        self._policy = ctx.policy
        self._quota_error: QuotaExhaustedError | None = None
        self._agy_cli_log = self._resolve_agy_cli_log(agy_cli_log, ctx)
        self._monitor = ctx.monitor
        self._workspace_path = cast("Path | None", getattr(ctx, "workspace_path", None))
        self._pre_existing_transcript_names: frozenset[str] = (
            _resolve_pre_existing_transcript_names(_extras, self._workspace_path)
        )
        self._clock = clock
        self._max_pending_chars = max_pending_chars
        self._strategy: BaseExecutionStrategy = ctx.execution_strategy or GenericExecutionStrategy()
        self._probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._pre_output_listener = cast(
            "Callable[[], None] | None",
            getattr(ctx, "pre_output_listener", None),
        )
        self._connectivity_state_provider = cast(
            "Callable[[], str | None] | None",
            getattr(ctx, "connectivity_state_provider", None),
        )
        self._is_waiting_state_provider = cast(
            "Callable[[], bool] | None",
            getattr(ctx, "is_waiting_state_provider", None),
        )
        self._configure_runtime_hooks(ctx, _extras)
        self._completion_run_id = completion_run_id_from_extra_env(
            cast("dict[str, str] | None", getattr(ctx, "extra_env", None))
        )
        self._evaluate_completion_fn = cast(
            "_EvalCompletionFn | None",
            getattr(ctx, "evaluate_completion_fn", None),
        )
        self._required_artifact = cast(
            "RequiredArtifact | None",
            getattr(ctx, "required_artifact", None),
        )
        self._completion_exit_sent = False
        self._lines_queue: BoundedLinesQueue = self._build_lines_queue()
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
        self._cpu_baselines: dict[int, tuple[float, float]] = {}
        self._log_growth_state: dict[str, tuple[int, float]] = {}
        self._raw_overflow: RawOverflowLog | None = None
        self._input_writer_fd = os.dup(handle.master_fd)
        self._read_fd = os.dup(handle.master_fd)
        self._input_writer_lock = threading.Lock()
        self._auto_mode_prompt_seen = False
        self._auto_response_menu_seen = False
        self._auto_mode_menu_screen: str | None = None
        self._last_auto_mode_response_at: float | None = None
        self._last_auto_mode_menu_seen_at: float | None = None
        self._pending_permission_prompt_line: str | None = None
        self._pending_permission_prompt_started_at: float | None = None
        recent_choice_cap = _RECENT_CHOICE_LINES_MAX
        self._recent_choice_lines: deque[str] = deque(maxlen=recent_choice_cap)
        cap = _MAX_TRANSCRIPT_SESSION_IDS
        self._transcript_session_ids: deque[str] = deque(maxlen=cap)
        self._transcript_session_ids_lock = threading.Lock()
        self._captured_session_id: str | None = self._expected_session_id
        if self._expected_session_id:
            self._transcript_session_ids.append(self._expected_session_id)
        self._subagent_tails: ClaudeSubagentTranscriptTails | None = None

    @staticmethod
    def _resolve_agy_cli_log(
        agy_cli_log: tuple[Path, int] | None,
        ctx: AgentRunCtx,
    ) -> tuple[Path, int] | None:
        if agy_cli_log is not None:
            return agy_cli_log
        agy_cli_log_path = cast("Path | None", getattr(ctx, "agy_cli_log_path", None))
        if ctx.config.transport == AgentTransport.AGY:
            return agy_cli_log_start_offset(agy_cli_log_path)
        return None

    def _configure_runtime_hooks(self, ctx: AgentRunCtx, extras: PtyExtras) -> None:
        self._expected_session_id = extras.expected_session_id
        self._input_prompt = extras.input_prompt
        relay_register: object = getattr(ctx, "relay_activity_sink_register", None)
        relay_health_error: object = getattr(ctx, "relay_health_error", None)
        self._relay_activity_sink_register = cast(
            "Callable[[Callable[[str], None]], Callable[[], None]] | None", relay_register
        )
        self._relay_health_error = cast("Callable[[], str | None] | None", relay_health_error)
        self._remove_relay_sink: Callable[[], None] | None = None
        self._stop_sentinel_path = extras.stop_sentinel_path
        self._permission_prompt_listener = extras.permission_prompt_listener

    @property
    def completion_exit_sent(self) -> bool:
        return self._completion_exit_sent

    def _attempt_supervisor(self) -> AttemptSupervisor:
        supervisor = cast("object", getattr(self, "_supervisor", None))
        if not isinstance(supervisor, AttemptSupervisor):
            supervisor = AttemptSupervisor(self._handle)
            self._supervisor = supervisor
        return supervisor

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

    def _probe_cpu_idle(
        self,
        scoped_active: bool | None,
        alive_by: AliveBy | None,
    ) -> bool:
        if self._policy.cpu_idle_seconds is None or not scoped_active:
            return False
        match alive_by:
            case None:
                pass
            case AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS:
                pass
            case (
                AliveBy.CPU_IDLE_WHILE_ALIVE
                | AliveBy.LOG_STALE_WHILE_ALIVE
                | AliveBy.FRESH_PROGRESS
                | AliveBy.FRESH_HEARTBEAT_ONLY
                | AliveBy.STALE_LABEL_ONLY
            ):
                return False
        _now = self._clock.monotonic()
        try:
            root_pid = self._handle.pid
            try:
                root_proc = psutil.Process(root_pid)
                child_pids = [p.pid for p in root_proc.children(recursive=True)]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return False
            for pid in child_pids:
                try:
                    cpu_times = psutil.Process(pid).cpu_times()
                    current_cpu = cpu_times.user + cpu_times.system
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    self._cpu_baselines.pop(pid, None)
                    continue
                if pid in self._cpu_baselines:
                    baseline_cpu, baseline_time = self._cpu_baselines[pid]
                    if current_cpu == baseline_cpu:
                        if _now - baseline_time >= self._policy.cpu_idle_seconds:
                            return True
                    else:
                        self._cpu_baselines[pid] = (current_cpu, _now)
                else:
                    self._cpu_baselines[pid] = (current_cpu, _now)
            live_pids = set(child_pids)
            for stale_pid in list(self._cpu_baselines.keys() - live_pids):
                self._cpu_baselines.pop(stale_pid, None)
        except Exception:
            pass
        return False

    def _probe_log_growth(self, alive_by: AliveBy | None) -> bool:
        if (
            self._policy.log_growth_seconds is None
            or not hasattr(self, "_raw_overflow")
            or self._raw_overflow is None
            or not (alive_by is None or alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS)
        ):
            return False
        if self._raw_overflow is not None and self._raw_overflow.is_disabled:
            return False
        _now = self._clock.monotonic()
        path_str = str(self._raw_overflow.path)
        current_size = self._raw_overflow.size_bytes
        if current_size == 0:
            return False
        if path_str in self._log_growth_state:
            baseline_size, baseline_time = self._log_growth_state[path_str]
            if current_size != baseline_size:
                self._log_growth_state[path_str] = (current_size, _now)
            elif _now - baseline_time >= self._policy.log_growth_seconds:
                return True
        else:
            self._log_growth_state[path_str] = (current_size, _now)
        return False

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
            monitor: ProcessMonitor | None = getattr(self, "_process_monitor", None)
            if monitor is not None:
                filtered_count: int = monitor.spawned_subagent_count()
                scoped_child_count = filtered_count
                scoped_child_active = filtered_count > 0
            else:
                try:
                    descendant_count, descendant_oldest = self._handle.descendant_snapshot()
                    scoped_child_count = descendant_count
                    scoped_child_active = descendant_count > 0
                    oldest_child_seconds = descendant_oldest
                except Exception:
                    logger.debug(
                        "corroborator: PTY legacy descendant_snapshot fallback failed (suppressed)"
                    )
        except Exception:
            logger.debug("corroborator: PTY process monitor count failed (suppressed)")
            scoped_child_count = None
            scoped_child_active = None
        scoped_child_count_int: int | None = scoped_child_count
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

        _cpu_idle = self._probe_cpu_idle(scoped_child_active, alive_by)
        _log_stale = self._probe_log_growth(alive_by)

        if _cpu_idle or _log_stale:
            _override: AliveBy = (
                AliveBy.LOG_STALE_WHILE_ALIVE if _log_stale else AliveBy.CPU_IDLE_WHILE_ALIVE
            )
            if alive_by in (None, AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS):
                alive_by = _override

        return CorroborationSnapshot(
            workspace_event_count=workspace_event_count,
            last_workspace_event_at=last_workspace_event_at,
            oldest_child_seconds=oldest_child_seconds,
            scoped_child_active=scoped_child_active,
            scoped_child_count=scoped_child_count_int,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_meaningful[0],
            alive_by=alive_by,
        )

    def _drain_after_exit(self, decoder: codecs.IncrementalDecoder, pending: str) -> str:
        for _ in range(_EIO_DRAIN_MAX):
            try:
                if not wait_for_master_readable(self._read_fd, _EIO_DRAIN_SELECT_SECONDS):
                    break
                chunk = read_master_chunk(self._read_fd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    break
                raise
            if not chunk:
                break
            pending += decoder.decode(chunk)
            pending = clamp_tail(pending, max_chars=self._max_pending_chars)
            completed, pending = _split_complete_vt_lines(pending)
            if completed:
                with self._lines_lock:
                    self._lines_queue.extend(completed)
                    self._lines_event.set()
        return pending

    def _read_available_master_chunk(self) -> tuple[bool, bytes | None]:
        try:
            if not wait_for_master_readable(self._read_fd, 0.05):
                return (False, None)
            chunk = read_master_chunk(self._read_fd)
        except BlockingIOError:
            return (False, None)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                return (True, None)
            raise
        return (True, chunk or None)

    def _read_thread(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        if self._pre_output_listener is not None:
            with contextlib.suppress(Exception):
                self._pre_output_listener()
        try:
            while True:
                if self._handle.poll() is not None:
                    pending = self._drain_after_exit(decoder, pending)
                    break
                has_chunk, chunk = self._read_available_master_chunk()
                if not has_chunk:
                    continue
                if chunk is None:
                    break
                pending += decoder.decode(chunk)
                pending = clamp_tail(pending, max_chars=self._max_pending_chars)
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
        except Exception as exc:
            logger.opt(exception=True).warning(
                "PTY read thread for agent={} raised: {}", self._agent_name, exc
            )
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
            self._transcript_session_ids.appendleft(session_id)
        self._capture_discovered_transcript_session_id(session_id)

    def _capture_discovered_transcript_session_id(self, session_id: str) -> None:
        self._captured_session_id = session_id
        watchdog: IdleWatchdog | None = getattr(self, "_watchdog", None)
        if watchdog is not None:
            watchdog.record_session_id_capture(session_id)

    def _transcript_session_id_candidates(self) -> tuple[str, ...]:
        with self._transcript_session_ids_lock:
            return tuple(self._transcript_session_ids)

    @property
    def captured_session_id(self) -> str | None:
        return self._captured_session_id

    def discover_captured_session_id(self) -> str | None:
        if self._captured_session_id is not None:
            return self._captured_session_id
        entry = find_claude_transcript_entry(self._transcript_session_id_candidates())
        if entry is None and self._workspace_path is not None:
            entry = find_latest_claude_transcript_entry(
                self._workspace_path,
                min_mtime=self._started_at_wall_clock,
                exclude_names=self._pre_existing_transcript_names,
            )
        if entry is None:
            return None
        _path, session_id = entry
        self._capture_discovered_transcript_session_id(session_id)
        return session_id

    def _transcript_thread(self) -> None:
        if self._expected_session_id is None and self._workspace_path is None:
            return
        transcript_path: Path | None = None
        transcript_session_id: str | None = None
        file_obj = None
        transcript_parser = ClaudeInteractiveTranscriptParser()
        active_tails: ClaudeSubagentTranscriptTails | None = None
        try:
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
                            exclude_names=self._pre_existing_transcript_names,
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
                    self._capture_discovered_transcript_session_id(matched_session_id)
                assert file_obj is not None
                line = file_obj.readline()
                if not line:
                    self._monitor_stop.wait(0.1)
                    continue
                events = transcript_parser.feed(line)
                for event in events:
                    active_tails = self._route_parent_event(
                        event=event,
                        active_tails=active_tails,
                    )
                if active_tails is not None:
                    self._capture_parent_record_metadata(line, active_tails)
                emitted_lines = transcript_lines_from_events(line, events)
                if emitted_lines:
                    with self._lines_lock:
                        self._lines_queue.extend(emitted_lines)
                        self._lines_event.set()
        finally:
            if file_obj is not None:
                file_obj.close()
                file_obj = None

    def _route_parent_event(
        self,
        *,
        event: InteractiveTranscriptEvent,
        active_tails: ClaudeSubagentTranscriptTails | None,
    ) -> ClaudeSubagentTranscriptTails | None:
        if event.kind == "session":
            text_value = event.text.strip()
            if text_value and active_tails is None:
                return self._ensure_subagent_tails_for_session(text_value)
            return active_tails
        if event.kind == "tool_use":
            tool_name_obj = event.metadata.get("tool")
            tool_use_id_obj = event.metadata.get("tool_use_id")
            if (
                isinstance(tool_name_obj, str)
                and isinstance(tool_use_id_obj, str)
                and active_tails is not None
            ):
                active_tails.note_dispatch(
                    tool_use_id=tool_use_id_obj,
                    tool_name=tool_name_obj,
                )
                if not active_tails.is_started:
                    active_tails.start()
            return active_tails
        if event.kind == "tool_result":
            tool_use_id_obj = event.metadata.get("tool_use_id")
            if isinstance(tool_use_id_obj, str) and tool_use_id_obj and active_tails is not None:
                active_tails.note_completion(tool_use_id=tool_use_id_obj)
            return active_tails
        return active_tails

    def _ensure_subagent_tails_for_session(
        self, session_id: str
    ) -> ClaudeSubagentTranscriptTails | None:
        existing_obj: object = getattr(self, "_subagent_tails", None)
        if isinstance(existing_obj, ClaudeSubagentTranscriptTails):
            if existing_obj.session_id == session_id:
                return existing_obj
            with contextlib.suppress(Exception):
                existing_obj.stop()
        if self._workspace_path is None:
            return None
        try:
            project_key = str(self._workspace_path.resolve()).replace("/", "-").replace(" ", "-")
        except OSError:
            return None
        holder_obj: object = getattr(self, "_subagent_sink_holder", None)
        sink: Callable[[str], None]
        if isinstance(holder_obj, dict):
            existing_sink: object = holder_obj.get("sink")
            if callable(existing_sink):
                sink = cast("Callable[[str], None]", existing_sink)
            else:

                def _noop_sink(_summary: str) -> None:
                    return None

                sink = _noop_sink
        else:

            def _noop_sink(_summary: str) -> None:
                return None

            sink = _noop_sink

        def _r7_sink(diag: R7AbsentLayoutDiagnostic) -> None:
            logger.warning(
                "R7 absent subagent layout: code={} claude_code_version={!r}"
                " project_key={!r} session_id={!r} probed_path={!r}"
                " dispatch_tool_use_id={!r} dispatch_tool_name={!r}",
                diag.code,
                diag.claude_code_version,
                diag.project_key,
                diag.session_id,
                diag.probed_path,
                diag.dispatch_tool_use_id,
                diag.dispatch_tool_name,
            )

        tails = ClaudeSubagentTranscriptTails(
            session_id=session_id,
            project_key=project_key,
            monitor_stop=self._monitor_stop,
            subagent_sink=sink,
            r7_sink=_r7_sink,
            clock=lambda: float(self._clock.monotonic()),
        )
        self._subagent_tails = tails
        return tails

    @staticmethod
    def _capture_parent_record_metadata(
        raw_line: str, tails: ClaudeSubagentTranscriptTails
    ) -> None:
        try:
            obj: object = json.loads(raw_line)
        except (ValueError, TypeError):
            return
        if not isinstance(obj, dict):
            return
        tails.note_parent_record(cast("dict[str, object]", obj))

    def _sentinel_thread(self) -> None:
        if self._stop_sentinel_path is None:
            return
        while not self._monitor_stop.is_set():
            if self._stop_sentinel_path.exists():
                with self._lines_lock:
                    self._lines_queue.append(TURN_BOUNDARY_MARKER + "\n")
                    self._lines_event.set()
                with contextlib.suppress(OSError):
                    _write_pty_input(
                        self._input_writer_fd, "/exit\r\n", lock=self._input_writer_lock
                    )
                return
            self._monitor_stop.wait(0.05)

    def _capture_pending(self, pending_lines: list[str]) -> list[str]:
        for line in pending_lines:
            with contextlib.suppress(Exception):
                if self._raw_overflow is not None:
                    self._raw_overflow.append(line)
        return pending_lines

    def _build_lines_queue(self) -> BoundedLinesQueue:
        queue = BoundedLinesQueue(maxlen=256)
        queue.set_eviction_sink(self._capture_evicted_line)
        return queue

    def _capture_evicted_line(self, line: str) -> None:
        if self._raw_overflow is None:
            return
        with contextlib.suppress(Exception):
            self._raw_overflow.append(line, counts_as_liveness=False)

    def _request_interactive_exit(self) -> None:
        if self._completion_exit_sent:
            return
        self._completion_exit_sent = True
        self._record_terminal_reason("interactive_completion")
        with self._lines_lock:
            self._lines_queue.append(TURN_BOUNDARY_MARKER + "\n")
            self._lines_event.set()
        with contextlib.suppress(OSError):
            _write_pty_input(self._input_writer_fd, "/exit\r\n", lock=self._input_writer_lock)
        self._terminate_if_still_alive_after_grace()

    def _terminate_if_still_alive_after_grace(self) -> None:
        policy = self._policy
        grace = (
            policy.parent_exit_grace_seconds
            if policy is not None
            else _DEFAULT_INTERACTIVE_EXIT_GRACE_SECONDS
        )
        grace = min(grace, _MAX_INTERACTIVE_EXIT_GRACE_SECONDS)
        self._attempt_supervisor().act(
            InteractiveCompletionObserved(grace), teardown=teardown_subtree
        )

    def _completion_evidence_enabled(self) -> bool:
        return (
            self._workspace_path is not None
            and self._completion_run_id is not None
            and self._evaluate_completion_fn is not None
        )

    def _durable_completion_is_terminal(self) -> bool:
        evaluate_completion_fn = self._evaluate_completion_fn
        if (
            evaluate_completion_fn is None
            or self._workspace_path is None
            or self._completion_run_id is None
        ):
            return False
        signals = evaluate_completion_fn(
            self._workspace_path,
            [],
            required_artifact=self._required_artifact,
            run_id=self._completion_run_id,
            sentinel_secret=_parent_broker_secret(),
            receipt_secret=_parent_broker_secret(),
        )
        return completion_signals_terminal(signals)

    def _completion_evidence_thread(self) -> None:
        if not self._completion_evidence_enabled():
            return
        while not self._monitor_stop.wait(0.25):
            if self._durable_completion_is_terminal():
                self._request_interactive_exit()
                return

    def _finish_terminal_completion(self) -> bool:
        _raise_on_relay_health_error(self, self._agent_name)
        terminal = self._completion_exit_sent
        if not terminal:
            with contextlib.suppress(Exception):
                terminal = self._durable_completion_is_terminal()
        if not terminal:
            return False
        self._attempt_supervisor().cleanup(
            ProcessTerminationIssuer.INVOKE_TEARDOWN, teardown=teardown_subtree
        )
        return True

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
        relay_health_error = cast(
            "Callable[[], str | None] | None", getattr(self, "_relay_health_error", None)
        )
        if relay_health_error is not None:
            detail = relay_health_error()
            if detail:
                raise SupervisionInfrastructureError(self._agent_name, detail)
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
        outcome = self._attempt_supervisor().act(
            SilenceObserved(fire_reason), teardown=teardown_subtree
        )
        if outcome is None:
            return None
        with self._lines_lock:
            pending_lines = list(self._lines_queue)
            self._lines_queue.clear()
        resume_safe = _is_resumable_fire_reason(fire_reason)
        captured_session_id = self._captured_session_id
        now = self._clock.monotonic()
        merged_diag: dict[str, object] = {
            "evidence_summary": watchdog.last_evidence_summary(now).to_dict_list(),
        }
        merged_diag["resumable_session_id"] = captured_session_id
        merged_diag["resume_safe"] = resume_safe
        snapshot_method: object = getattr(watchdog, "diagnostic_snapshot", None)
        if callable(snapshot_method):
            try:
                snapshot_obj: dict[str, object] | None = snapshot_method(now=now)
            except Exception:
                snapshot_obj = None
            if snapshot_obj:
                merged_diag["watchdog_snapshot"] = snapshot_obj
        repetition_diag_method: object = getattr(watchdog, "repetition_diagnostic", None)
        if callable(repetition_diag_method):
            try:
                repetition_diag: dict[str, str | int] | None = repetition_diag_method()
            except Exception:
                repetition_diag = None
            if repetition_diag:
                merged_diag.update(repetition_diag)
        if diagnostic is not None:
            for key, value in diagnostic.items():
                if key not in merged_diag:
                    merged_diag[key] = value
        _alive_by_signal: object = getattr(watchdog, "last_alive_by", None)
        _child_alive = _alive_by_signal is not None
        _append_conflict_termination_diagnostic(merged_diag, watchdog, fire_reason, now)
        typed_exc = IdleWatchdogKilledError(
            reason=fire_reason.value,
            signal=15,
            evidence_summary=str(merged_diag),
            child_alive=_child_alive,
            resumable_session_id=captured_session_id,
            issuer=fire_reason.value,
            runtime_event=current_runtime_event(),
        )
        wrapper = _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=cast("_MergedDiagType", merged_diag),
        )
        wrapper.__cause__ = typed_exc
        return pending_lines, wrapper

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
            if (
                drain_deadline is None
                and self._handle.poll() is not None
                and (
                    self._policy.profile.value != "activity_only"
                    or self._classify_quiet() != AgentExecutionState.WAITING_ON_CHILD
                )
            ):
                return None
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def _observe_queued_line(self, queued_line: str) -> None:
        visible_line = _visible_tui_text(queued_line)

        self._recent_choice_lines.append(queued_line)

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
        self._attempt_supervisor().act(OperatorInterruptObserved(), teardown=teardown_subtree)

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
            0.1 if interrupted else 1,
        )
        for reader, timeout in zip(readers, timeouts, strict=True):
            reader.join(timeout=timeout)
        with contextlib.suppress(Exception):
            os.close(self._input_writer_fd)
        with contextlib.suppress(Exception):
            os.close(self._read_fd)
        if self._stop_sentinel_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._stop_sentinel_path.unlink()
        unsubscribe()

    def _terminate_for_quota(self, detail: str) -> None:
        if not is_quota_kill_evidence(detail):
            return
        outcome = self._attempt_supervisor().act(
            ProviderErrorObserved(detail), teardown=teardown_subtree
        )
        if outcome is None:
            return
        returncode = outcome.returncode
        error = QuotaExhaustedError(
            self._agent_name,
            detail,
            returncode=0 if returncode is None else returncode,
            issuer=TerminationIssuer.QUOTA_DETECTOR.value,
        )
        publish_attempt_facts(error, self._attempt_supervisor().recorder)
        self._quota_error = error
        self._lines_event.set()

    def _record_terminal_reason(self, reason: str) -> None:
        reason_recorder = cast(
            "Callable[[str], None] | None",
            getattr(self._handle, "record_terminal_reason", None),
        )
        if reason_recorder is not None:
            reason_recorder(reason)

    def _raise_if_fresh_agy_log_has_quota(self) -> None:
        if self._agy_cli_log is None:
            return
        cli_log_path, start_offset = self._agy_cli_log
        tail = agy_fresh_cli_log_tail(cli_log_path, start_offset)
        if provider_quota_log_detail_matches(tail):
            self._terminate_for_quota(tail)

    def _raise_if_quota_exhausted(self) -> None:
        self._raise_if_fresh_agy_log_has_quota()
        if self._quota_error is not None:
            raise self._quota_error

    def _raise_if_terminal_startup_error(self, queued_line: str) -> None:
        terminal_startup_error = _terminal_interactive_startup_error(
            self._agent_name,
            queued_line,
        )
        if terminal_startup_error is None:
            return
        outcome = self._attempt_supervisor().act(
            TerminalStartupErrorObserved(terminal_startup_error),
            teardown=teardown_subtree,
        )
        if outcome is None:
            return
        raise AgentInvocationError(
            self._agent_name,
            1,
            terminal_startup_error,
            parsed_output=[_visible_tui_text(queued_line).strip() or queued_line.strip()],
        )

    def _handle_queued_line(self, queued_line: str, watchdog: IdleWatchdog) -> Iterator[str]:
        if is_provider_quota_output_line(queued_line):
            if self._raw_overflow is not None:
                self._raw_overflow.append(queued_line)
            self._terminate_for_quota(queued_line)
            self._raise_if_quota_exhausted()
        self._record_transcript_session_id(queued_line)
        self._observe_queued_line(queued_line)
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is not None:
            activity_signal = with_prompt_echo_flag(
                activity_signal, queued_line, self._input_prompt
            )
            if (
                activity_signal.kind != AgentActivityKind.LIFECYCLE
                and not activity_signal.is_harness_echo
            ):
                watchdog.record_any_output(byte_size=len(queued_line.encode("utf-8")))
            self._last_activity_kind = activity_signal.kind
            self._last_meaningful[0] = (
                activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
                and not activity_signal.is_harness_echo
            )
            if activity_signal.is_harness_echo:
                watchdog.record_prompt_echo(queued_line)
            else:
                self._record_non_echo_activity(watchdog, activity_signal)
        else:
            self._last_meaningful[0] = False
        self._strategy.observe_line(queued_line)
        if self._raw_overflow is not None:
            self._raw_overflow.append(queued_line)
        yield queued_line
        self._raise_if_terminal_startup_error(queued_line)
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from self._capture_pending(pending_lines)
            raise exc

    def _record_non_echo_activity(
        self, watchdog: IdleWatchdog, activity_signal: AgentActivitySignal
    ) -> None:
        _record_embedded_error(watchdog, activity_signal)
        if activity_signal.kind == AgentActivityKind.ERROR_LINE:
            watchdog.record_error_activity(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.PROGRESS_REPORT:
            watchdog.record_progress_report(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.LIFECYCLE:
            watchdog.record_lifecycle_activity()
        elif activity_signal.kind == AgentActivityKind.TOOL_USE:
            self._awaiting_post_tool_result_progress = False
            tool_call = _extract_tool_call_from_activity_signal(activity_signal.raw)
            self._last_tool_use_name = _tool_use_display_name(activity_signal.raw, tool_call)
            if tool_call is not None:
                watchdog.record_tool_call_activity(*tool_call)
            watchdog.record_tool_use_activity()
        elif activity_signal.kind == AgentActivityKind.TOOL_RESULT:
            self._awaiting_post_tool_result_progress = True
            self._last_tool_result_at = self._clock.monotonic()
            self._last_tool_result_excerpt = activity_signal.raw.strip()[:200]
            _record_tool_result_activity(watchdog, is_harness_echo=False)
        elif activity_signal.kind == AgentActivityKind.OUTPUT_LINE:
            self._awaiting_post_tool_result_progress = False
            watchdog.record_activity()
        else:
            watchdog.record_activity()

    def _capture_abandoned_lines(self) -> None:
        overflow = self._raw_overflow
        if overflow is None:
            return
        with self._lines_lock:
            pending = list(self._lines_queue)
            self._lines_queue.clear()
        for line in pending:
            with contextlib.suppress(Exception):
                overflow.append(line)

    def _handle_done_path(self, watchdog: IdleWatchdog) -> Iterator[str]:
        _raise_on_relay_health_error(self, self._agent_name)
        if self._finish_terminal_completion():
            return
        drain_deadline = (
            None
            if self._policy.profile.value == "activity_only"
            else self._clock.monotonic() + self._policy.drain_window_seconds
            if self._policy.drain_window_seconds
            else None
        )
        drain_result = self._run_drain_window(watchdog, drain_deadline)
        if drain_result is not None:
            pending_lines, exc = drain_result
            yield from self._capture_pending(pending_lines)
            raise exc

    def _idle_check_and_wait(self, watchdog: IdleWatchdog) -> Iterator[str]:
        self._maybe_send_auto_response()
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from self._capture_pending(pending_lines)
            raise exc
        check_broken_agent_timer(self._handle, watchdog, self._agent_name)
        self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def _strategy_registry_and_prefix(
        self,
    ) -> tuple[ChildLivenessRegistry | None, str]:
        registry = cast(
            "ChildLivenessRegistry | None",
            getattr(self._strategy, "_registry", None),
        )
        if registry is None:
            return None, ""
        active_prefix_fn = cast(
            "Callable[[], str | None] | None",
            getattr(self._strategy, "_active_label_prefix", None),
        )
        try:
            prefix = active_prefix_fn() if active_prefix_fn is not None else ""
        except AttributeError:
            prefix = ""
        return registry, prefix or ""

    def _build_subagent_pid_source(self) -> ChildLivenessSubagentPidSource | None:
        registry, prefix = self._strategy_registry_and_prefix()
        if registry is None:
            return None
        return ChildLivenessSubagentPidSource(registry, prefix)

    def read_lines(self) -> Iterator[str]:
        setup = self._setup_read_loop()
        interrupted = [False]
        try:
            yield from self._run_read_loop(setup.watchdog)
        except BaseException:
            interrupted[0] = True
            self._on_interrupt()
            raise
        finally:
            self._teardown_read_loop(setup, interrupted[0])

    def _run_read_loop(self, watchdog: IdleWatchdog) -> Iterator[str]:
        while True:
            _raise_on_relay_health_error(self, self._agent_name)
            self._lines_event.clear()
            self._raise_if_quota_exhausted()
            queued_line: str | None = None
            is_done = False
            with self._lines_lock:
                if self._lines_queue:
                    queued_line = _pop_queue_line(self._lines_queue)
                elif self._reader_done[0]:
                    is_done = True

            if queued_line is not None:
                yield from self._handle_queued_line(queued_line, watchdog)
                continue

            if is_done:
                yield from self._handle_done_path(watchdog)
                break

            yield from self._idle_check_and_wait(watchdog)

    def _setup_read_loop(self) -> _ReadLoopSetup:
        self._raw_overflow = get_or_create_raw_overflow_log(
            self._workspace_path or Path.cwd(),
            raw_log_unit_id_for(self._config),
            model=self._config.model,
        )
        reader = self._start_thread(self._read_thread)
        transcript_reader = self._start_thread(self._transcript_thread)
        sentinel_reader = self._start_thread(self._sentinel_thread)
        completion_reader = self._start_thread(self._completion_evidence_thread)
        self._subagent_sink_holder: dict[str, Callable[[str], None]] = {}
        subagent_pid_source = self._build_subagent_pid_source()
        registry, scope_prefix = self._strategy_registry_and_prefix()
        process_monitor = _make_process_monitor(
            self._handle,
            self._config,
            self._policy,
            subagent_pid_source,
            registry=registry,
            scope_prefix=scope_prefix,
        )
        self._process_monitor = process_monitor
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
            process_monitor=process_monitor,
            connectivity_state_provider=self._connectivity_state_provider,
        )
        if self._is_waiting_state_provider is not None:
            watchdog.set_is_waiting_state(self._is_waiting_state_provider())
        watchdog.record_invocation_start()
        self._watchdog = watchdog
        if self._captured_session_id is not None:
            watchdog.record_session_id_capture(self._captured_session_id)
        self._bind_workspace_monitor(watchdog)
        sink_token, subagent_token, subagent_sink = self._register_active_sinks(watchdog)
        holder: dict[str, Callable[[str], None]] = self._subagent_sink_holder
        holder["sink"] = subagent_sink
        unsubscribe = get_process_manager().register_listener(self._on_process_event)
        return _ReadLoopSetup(
            reader=reader,
            transcript_reader=transcript_reader,
            sentinel_reader=sentinel_reader,
            completion_reader=completion_reader,
            watchdog=watchdog,
            subagent_tails=None,
            unsubscribe=unsubscribe,
            sink_token=sink_token,
            subagent_token=subagent_token,
        )

    def _bind_workspace_monitor(self, watchdog: IdleWatchdog) -> None:
        if self._monitor is None:
            return

        def _forward_event(kind: WorkspaceChangeKind, weight: float) -> None:
            watchdog.record_workspace_event(kind=kind, weight=weight)

        self._monitor.set_on_event(_forward_event)

    def _register_active_sinks(
        self,
        watchdog: IdleWatchdog,
    ) -> tuple[
        Token[Callable[[str], None] | None],
        Token[Callable[[str], None] | None],
        Callable[[str], None],
    ]:
        def _mcp_sink(_tool_name: str) -> None:
            watchdog.record_mcp_tool_call()

        def _subagent_sink(line: str) -> None:
            watchdog.record_subagent_work(description=line)

        sink_token = set_active_sink(_mcp_sink)
        subagent_token = set_subagent_sink(_subagent_sink)
        relay_register = cast(
            "Callable[[Callable[[str], None]], Callable[[], None]] | None",
            getattr(self, "_relay_activity_sink_register", None),
        )
        if relay_register is not None:
            self._remove_relay_sink = relay_register(_mcp_sink)
        return sink_token, subagent_token, _subagent_sink

    def _teardown_read_loop(self, setup: _ReadLoopSetup, interrupted: bool) -> None:
        setup.watchdog.record_invocation_end()
        remove_relay_sink = cast(
            "Callable[[], None] | None", getattr(self, "_remove_relay_sink", None)
        )
        if remove_relay_sink is not None:
            remove_relay_sink()
            del self._remove_relay_sink
        reset_active_sink(setup.sink_token)
        reset_subagent_sink(setup.subagent_token)
        if self._monitor is not None:
            self._monitor.set_on_event(None)
        lazy_tails_obj: object = getattr(self, "_subagent_tails", None)
        if isinstance(lazy_tails_obj, ClaudeSubagentTranscriptTails):
            with contextlib.suppress(Exception):
                lazy_tails_obj.stop()
        self._cleanup(
            [
                setup.reader,
                setup.transcript_reader,
                setup.sentinel_reader,
                setup.completion_reader,
            ],
            setup.unsubscribe,
            interrupted,
        )
        if self._raw_overflow is not None:
            self._capture_abandoned_lines()
            self._raw_overflow.close()
