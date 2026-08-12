"""PTY line reader for interactive agent sessions."""

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

from ralph.agents._bounded_text_buffer import DEFAULT_MAX_BUFFER_CHARS, clamp_tail
from ralph.agents.activity import AgentActivityKind
from ralph.agents.completion_signals import completion_signals_terminal
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    GenericExecutionStrategy,
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
from ralph.agents.invoke._completion import completion_run_id_from_extra_env
from ralph.agents.invoke._errors import (
    AgentInvocationError,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._lines_queue_helpers import _pop_queue_line
from ralph.agents.invoke._process_reader import (
    _NON_MEANINGFUL_ACTIVITY_KINDS,
    _TERMINAL_PROCESS_STATUSES,
    _extract_tool_call_from_activity_signal,
    _is_resumable_fire_reason,
    _parent_broker_secret,
)
from ralph.agents.invoke._pty_extras import _PtyExtras
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
from ralph.agents.invoke._session import (
    _TURN_BOUNDARY_MARKER,
    extract_visible_tui_transport_session_id,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)
from ralph.display.raw_overflow import (
    RawOverflowLog,
    get_or_create_raw_overflow_log,
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
    get_process_manager,
)
from ralph.process.pty import read_master_chunk, wait_for_master_readable
from ralph.process.teardown import teardown_subtree

from ._monitor_factory import _make_process_monitor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextvars import Token

    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx, _EvalCompletionFn
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
    """Unused helper kept for backward-compat with any caller that
    relied on the prior module-level helper. Returns 0.0 so a
    watchdog mock without the full ``IdleWatchdog`` surface does
    not crash the fire path.
    """
    return 0.0


_EIO_DRAIN_MAX = 32
_EIO_DRAIN_SELECT_SECONDS = 0.005
_NANOCODER_TERMINAL_CONFIG_ERROR_PATTERNS = (
    re.compile(r"\bProvider\s+'[^']+'\s+not found in agents\.config\.json\b", re.IGNORECASE),
    re.compile(r"\bModel\s+'[^']+'\s+not found in agents\.config\.json\b", re.IGNORECASE),
)


def _terminal_interactive_startup_error(agent_name: str, line: str) -> str | None:
    """Return a terminal startup/configuration error visible in PTY output."""
    visible_line = _visible_tui_text(line).strip()
    if not visible_line:
        return None
    if agent_name.casefold() == "nanocoder" and any(
        pattern.search(visible_line) for pattern in _NANOCODER_TERMINAL_CONFIG_ERROR_PATTERNS
    ):
        return f"Nanocoder startup/configuration error: {visible_line}"
    return None


def _record_embedded_error(watchdog: IdleWatchdog, signal: AgentActivitySignal) -> None:
    """Feed the error dimension for a signal that carries an embedded error.

    A failed tool call is BOTH a call and an error; feeding only the dimension
    its ``kind`` names left the other wedge invisible. See
    :class:`~ralph.agents.activity.AgentActivitySignal`.
    """
    if signal.error_message is not None:
        watchdog.record_error_activity(signal.error_message)


def _record_tool_result_activity(watchdog: IdleWatchdog, *, is_harness_echo: bool) -> None:
    """Record a tool result without requiring legacy fakes to accept the new flag."""
    if is_harness_echo:
        watchdog.record_tool_result_activity(is_harness_echo=True)
    else:
        watchdog.record_tool_result_activity()


def _tool_use_display_name(
    raw: str,
    tool_call: tuple[str, dict[str, object]] | None,
) -> str:
    """Return the tool name to show in watchdog diagnostics for a TOOL_USE line.

    Prefers the extracted name. The plain-text ``claude tool: <name>`` split is
    the fallback for transports that still emit a marker; applying it to the
    interactive transport's JSON envelope would slice a JSON fragment.
    """
    if tool_call is not None:
        return tool_call[0]
    stripped = raw.strip()
    return stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped


class _ReadLoopSetup(NamedTuple):
    """Bundle of resources the read_lines loop creates during setup.

    Held in a NamedTuple so the setup helper has a single return value rather
    than threading ten locals back into ``read_lines`` (which would push the
    caller back over the PLR0915 too-many-statements ceiling).
    """

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
    extras: _PtyExtras, workspace_path: Path | None
) -> frozenset[str]:
    """Resolve the transcript names to exclude from live discovery.

    wt-04-claude-parsing: the orchestrating session (or any sibling
    session) already lives in the same ``~/.claude/projects/<key>``
    directory a freshly-spawned child writes into, so
    ``find_latest_claude_transcript_entry``'s "touched since start"
    floor alone cannot tell the two apart -- an already-active sibling
    keeps satisfying that floor for as long as it stays active, and
    "latest mtime wins" can lock the transcript thread onto the wrong
    file for the entire run.

    Prefer the caller's PRE-SPAWN snapshot (``extras.pre_existing_transcript_names``,
    taken by ``run_pty_and_read_lines`` before the child process
    existed) when available. A snapshot taken here instead, inside
    ``PtyLineReader.__init__`` -- which only runs AFTER the caller has
    already spawned the child -- could already see the child's own
    freshly-created transcript file and wrongly self-exclude it, so
    that live snapshot is only a best-effort default for direct
    construction (e.g. tests), not the primary path.
    """
    # ``getattr`` (not direct attribute access): some test doubles
    # duck-type ``_PtyExtras`` with a bare ``SimpleNamespace`` that
    # predates this field.
    snapshot = cast(
        "frozenset[str] | None", getattr(extras, "pre_existing_transcript_names", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        ctx: _AgentRunCtx,
        clock: Clock,
        extras: _PtyExtras | None,
        *,
        max_pending_chars: int = DEFAULT_MAX_BUFFER_CHARS,
    ) -> None:
        _extras = extras or _PtyExtras()
        self._handle = handle
        self._agent_name = agent_name
        self._started_at_wall_clock = time.time()
        self._config = ctx.config
        self._policy = ctx.policy
        self._monitor = ctx.monitor
        self._workspace_path = cast(
            "Path | None", getattr(ctx, "workspace_path", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        # wt-04-claude-parsing: the transcript names already on disk for
        # this workspace, so the transcript thread's discovery fallback
        # can exclude them (see ``_resolve_pre_existing_transcript_names``
        # and ``existing_transcript_names``).
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
        # Live pipeline signals the watchdog consults on every
        # evaluate() call so the StuckClassifier gate can return
        # DUPLICATE_KILL (when the pipeline is already in a wait
        # state) or WAITING_ON_CONNECTIVITY (when the network is
        # offline) and defer the fire. Both providers are optional;
        # the watchdog falls back to "no live signal" when they
        # are None.
        self._connectivity_state_provider = cast(
            "Callable[[], str | None] | None",
            getattr(ctx, "connectivity_state_provider", None),
        )
        self._is_waiting_state_provider = cast(
            "Callable[[], bool] | None",
            getattr(ctx, "is_waiting_state_provider", None),
        )
        self._expected_session_id = _extras.expected_session_id
        self._stop_sentinel_path = _extras.stop_sentinel_path
        self._permission_prompt_listener = _extras.permission_prompt_listener
        self._completion_run_id = completion_run_id_from_extra_env(
            cast(
                "dict[str, str] | None", getattr(ctx, "extra_env", None)
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        self._lines_queue: BoundedLinesQueue = BoundedLinesQueue(maxlen=256)
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
        self._cpu_baselines: dict[int, tuple[float, float]] = {}  # bounded-accumulator-ok: drained
        self._log_growth_state: dict[str, tuple[int, float]] = {}  # bounded-accumulator-ok: drained
        self._raw_overflow: RawOverflowLog | None = None
        self._input_writer_fd = os.dup(handle.master_fd)
        # The reader owns this dup so handle.close() cannot invalidate a select in flight.
        self._read_fd = os.dup(handle.master_fd)
        self._input_writer_lock = threading.Lock()
        self._auto_mode_prompt_seen = False
        self._auto_response_menu_seen = False
        self._auto_mode_menu_screen: str | None = None
        self._last_auto_mode_response_at: float | None = None
        self._last_auto_mode_menu_seen_at: float | None = None
        self._pending_permission_prompt_line: str | None = None
        self._pending_permission_prompt_started_at: float | None = None
        # wt-024 M9 (AC-07): bounded deques replace the previous
        # unbounded lists. ``_recent_choice_lines`` was already
        # hand-capped at 20; the deque(maxlen) conversion makes
        # the eviction O(1) and removes the manual pop(0)/len()
        # check. ``_transcript_session_ids`` was unbounded; the
        # new cap keeps the recent-session dedup window bounded.
        # module constant cap; FIFO eviction
        recent_choice_cap = _RECENT_CHOICE_LINES_MAX
        self._recent_choice_lines: deque[str] = deque(
            maxlen=recent_choice_cap
        )  # bounded-accumulator-ok: FIFO cap
        # module constant cap; FIFO eviction
        cap = _MAX_TRANSCRIPT_SESSION_IDS
        self._transcript_session_ids: deque[str] = deque(maxlen=cap)  # bounded-accumulator-ok
        self._transcript_session_ids_lock = threading.Lock()
        # Captured transport-level session id from the most recent
        # visible-TUI line (or ``expected_session_id`` if no captured
        # line has been observed yet). Mirrors
        # ``_process_reader._captured_session_id``: the canonical
        # seam is :func:`extract_visible_tui_transport_session_id`
        # called from :meth:`_record_transcript_session_id`; this
        # field is the cache so the watchdog-kill path can read it
        # at the moment of the fire WITHOUT re-walking the PTY
        # transcript queue. The id flows into the typed
        # ``IdleWatchdogKilledError.resumable_session_id`` so the
        # recovery controller can read it from the ``__cause__``
        # chain and resume the same agent session instead of
        # starting a fresh one.
        self._captured_session_id: str | None = self._expected_session_id
        if self._expected_session_id:
            self._transcript_session_ids.append(self._expected_session_id)
        # wt-04-claude-parsing (RC1 + RC3 + R7): the subagent
        # transcript tailer is constructed LAZILY by the transcript
        # thread the first time a session id is observed. The
        # attribute defaults to ``None`` so the ``_ensure_subagent_tails_for_session``
        # helper can detect a fresh construction versus a re-use.
        self._subagent_tails: ClaudeSubagentTranscriptTails | None = None

    @property
    def completion_exit_sent(self) -> bool:
        """Return whether completion evidence triggered an interactive exit."""
        return self._completion_exit_sent

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
            # wt-024 M4 (AC-01): sweep PIDs that are no longer in the
            # current child list so the baseline map does not retain
            # entries for descendants that have exited between ticks.
            # Without this sweep, every distinct PID ever observed
            # accumulates one entry for the rest of the session, which
            # is unbounded in long build/test-runner patterns.
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
        # R1 (Trustworthy Idle Watchdog spec): the corroborator MUST
        # read the FILTERED subagent count from
        # ``ProcessMonitor.spawned_subagent_count()`` (preferred) or
        # the legacy alias ``live_subagent_count()``. The BROADER
        # ``self._handle.descendant_snapshot()`` count includes shell
        # helpers like ``npm test`` / ``cargo build`` and is the bug
        # source that produced the 2365s indefinite deferral in the
        # wild (cited in the product spec).
        #
        # Production behavior: when a process monitor is injected,
        # the corroborator sources ``scoped_child_active`` from the
        # FILTERED seam. When the monitor is unavailable
        # (``process_monitor_enabled=False`` legacy escape hatch
        # used by integration tests that pre-date the R5 registry
        # seam), the corroborator falls back to
        # ``self._handle.descendant_snapshot()`` so the watchdog
        # still has a signal. This fallback is the only
        # ``descendant_snapshot`` reference the audit
        # ``check_subagent_counting_seam`` does NOT flag because it
        # is the canonical legacy escape hatch -- it MUST be the
        # only descendant_snapshot reference in the function body,
        # guarded by ``self._process_monitor is None``.
        try:
            monitor: ProcessMonitor | None = getattr(self, "_process_monitor", None)
            if monitor is not None:
                filtered_count: int = monitor.spawned_subagent_count()
                scoped_child_count = filtered_count
                scoped_child_active = filtered_count > 0
            else:
                # Legacy escape hatch: ``process_monitor_enabled=False``
                # (e.g. integration tests pre-dating the R5 registry
                # seam). Reads the broader descendant count so the
                # watchdog still has a signal. Production callers
                # always have a monitor.
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
        # Type assertion for mypy: ``scoped_child_count`` is
        # guaranteed to be int (when the monitor produced a value) or
        # None (when no monitor was injected or the call raised).
        scoped_child_count_int: int | None = scoped_child_count
        alive_by: AliveBy | None = None
        registry = cast(
            "ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        """Bounded EIO drain of the PTY master after the child has exited.

        Replaces the prior early-exit ``break`` that could drop buffered
        bytes the child flushed to the kernel's PTY queue between exit
        and the parent ``poll`` return. Reuses the existing EIO-tolerant
        ``read_master_chunk`` and is bounded by ``_EIO_DRAIN_MAX``.
        """
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
            # wt-024 M9 (AC-07): deque-backed dedup. ``remove()`` on
            # the existing entry is O(n) but n <= _MAX_TRANSCRIPT_SESSION_IDS;
            # ``appendleft()`` is O(1) and the bounded deque automatically
            # evicts the oldest session id from the right when full.
            # ``candidate_ids[0]`` (consumed by ``_transcript_thread``)
            # MUST be the most-recently-seen session id so the PTY
            # resume path always prefers the freshest visible TUI
            # session over an older one.
            if session_id in self._transcript_session_ids:
                self._transcript_session_ids.remove(session_id)
            self._transcript_session_ids.appendleft(session_id)
        # Mirror the subprocess reader's ``_captured_session_id`` cache so
        # the watchdog-kill -> resume path sees the same id without
        # re-walking the PTY queue. The id flows into
        # ``IdleWatchdogKilledError.resumable_session_id`` so the
        # recovery controller can resume the same agent session.
        self._captured_session_id = session_id

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
        # wt-04-claude-parsing (RC1 + RC3 + R7): wire the parent
        # transcript parser to the subagent transcript tailer. The
        # tailer is constructed lazily HERE on the FIRST observation
        # of a session id (the previous shape constructed it in
        # ``_build_subagent_transcript_tails`` at reader-thread start,
        # BEFORE the visible-TUI extractor populated
        # ``_captured_session_id`` -- the tailer always came back
        # ``None`` for a fresh session and the watchdog's evidence
        # channel never advanced). The tailer is then re-used across
        # all subsequent lines of the parent transcript so every
        # parsed record reaches ``note_parent_record``,
        # ``note_dispatch``, and ``note_completion`` on the same
        # tailer instance. The tailer is started EXACTLY ONCE on
        # the first line that mentions a subagent-dispatch tool
        # name (``Agent`` / ``Task``) so the discovery poll loop
        # begins immediately on dispatch rather than idling until
        # the parent session ends.
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
                assert file_obj is not None
                line = file_obj.readline()
                if not line:
                    self._monitor_stop.wait(0.1)
                    continue
                # wt-04-claude-parsing: feed the line through the
                # parser EXACTLY ONCE. ``ClaudeInteractiveTranscriptParser``
                # is stateful (``session_id``, the dedup signature
                # cache), so a second ``feed`` call on the same raw
                # line silently returns fewer or zero events -- that
                # regression previously starved the operator-facing
                # ``_lines_queue`` of every parent event (session id,
                # tool_use, tool_result, output) because this loop's
                # own ``feed`` call had already consumed them. The
                # SAME ``events`` list now feeds both the subagent-tailer
                # routing below AND ``transcript_lines_from_events``.
                events = transcript_parser.feed(line)
                for event in events:
                    active_tails = self._route_parent_event(
                        event=event,
                        active_tails=active_tails,
                    )
                # wt-04-claude-parsing: also surface the top-level
                # parent record so the tailer can capture the
                # Claude Code ``version`` from a user / assistant
                # record on the first observation. ``feed`` already
                # consumed the line; the raw line is the canonical
                # JSON the tailer needs to inspect
                # ``obj.version`` directly.
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
        """Surface one parent ``InteractiveTranscriptEvent`` to the subagent tailer.

        Extracted from ``_transcript_thread`` to keep the loop body
        below the PLR0912 branch limit. The helper is pure-ish
        (``active_tails`` flows in and out) so the call site can
        re-assign the local ``active_tails`` reference. Each
        branch is documented inline:

          - ``session``: lazy-construct the tailer the first
            time a session id is observed.
          - ``tool_use``: dispatch-driven R7 probe + tailer
            start the first time a dispatch lands.
          - ``tool_result``: drop the matching child file via
            ``note_completion`` (R1 lifecycle ownership).
        All other event kinds (``text`` / ``output`` / ``thinking``
        / ``error`` / ``lifecycle``) are intentionally ignored;
        they do not change the subagent tailer's bookkeeping.
        """
        if event.kind == "session":
            # ``session`` events are emitted by
            # ``_events_from_json`` whenever the ``sessionId``
            # field changes; the FIRST observed session id is
            # the moment to construct the tailer. Construction
            # is deferred until here because the
            # ``_captured_session_id`` cache is populated by
            # the PTY-reader thread (not the transcript-reader
            # thread), and we cannot race the reader for the
            # canonical id.
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
                # R7 (dispatch-driven probe) + R1 (start the
                # tailer the first time a dispatch is observed).
                # The tailer's ``note_dispatch`` is idempotent on
                # ``tool_use_id`` so re-firing on every tool_use
                # line is safe.
                active_tails.note_dispatch(
                    tool_use_id=tool_use_id_obj,
                    tool_name=tool_name_obj,
                )
                if not active_tails.is_started:
                    active_tails.start()
            return active_tails
        if event.kind == "tool_result":
            tool_use_id_obj = event.metadata.get("tool_use_id")
            if (
                isinstance(tool_use_id_obj, str)
                and tool_use_id_obj
                and active_tails is not None
            ):
                # R1 (lifecycle ownership): drop the child
                # file when the parent ``tool_result`` lands;
                # the tailer correlates via the
                # ``toolUseId`` parsed from
                # ``agent-*.meta.json``.
                active_tails.note_completion(tool_use_id=tool_use_id_obj)
            return active_tails
        return active_tails

    def _ensure_subagent_tails_for_session(
        self, session_id: str
    ) -> ClaudeSubagentTranscriptTails | None:
        """Lazily build (or re-use) the subagent tailer for a captured session id.

        Returns the existing tailer (started or unstarted) when the
        reader already owns one for the same session id; builds a
        new one on the first observation. The subagent sink is
        wired into the watchdog's ``record_subagent_work`` channel
        by the call site so the tailer's forwarded events
        immediately reach the watchdog's evidence channel.
        """
        existing_obj: object = getattr(self, "_subagent_tails", None)
        if isinstance(existing_obj, ClaudeSubagentTranscriptTails):
            if existing_obj.session_id == session_id:
                return existing_obj
            # Different session id observed; the prior tailer is
            # associated with a now-superseded session so stop it
            # and replace.
            with contextlib.suppress(Exception):
                existing_obj.stop()
        if self._workspace_path is None:
            return None
        try:
            project_key = (
                str(self._workspace_path.resolve()).replace("/", "-").replace(" ", "-")
            )
        except OSError:
            return None
        # The watchdog's subagent sink was bound earlier in
        # ``_register_active_sinks`` (the closure holds a contextvar
        # token that ``teardown_read_loop`` will reset). Look up the
        # same sink the watchdog wired in.
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
        """Parse the raw parent record once and surface the version to the tailer.

        The transcript parser already consumed the line (its events are
        reused for both subagent-tailer routing and
        ``transcript_lines_from_events`` -- see ``_transcript_thread``;
        the line is never fed through the parser twice). We do not want
        to double-parse the JSON here either, so this reads the
        top-level ``version`` field directly via ``json.loads`` for the
        tailer's R7 diagnostic instead of going through the parser.
        """
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
                    self._lines_queue.append(_TURN_BOUNDARY_MARKER + "\n")
                    self._lines_event.set()
                with contextlib.suppress(OSError):
                    _write_pty_input(
                        self._input_writer_fd, "/exit\r\n", lock=self._input_writer_lock
                    )
                return
            self._monitor_stop.wait(0.05)

    def _request_interactive_exit(self) -> None:
        if self._completion_exit_sent:
            return
        self._completion_exit_sent = True
        with self._lines_lock:
            self._lines_queue.append(_TURN_BOUNDARY_MARKER + "\n")
            self._lines_event.set()
        with contextlib.suppress(OSError):
            _write_pty_input(self._input_writer_fd, "/exit\r\n", lock=self._input_writer_lock)
        with contextlib.suppress(AttributeError, OSError, ProcessLookupError, RuntimeError):
            self._handle.terminate(grace_period_s=0.5)
            pid = self._handle.pid
            teardown_subtree(pid)

    def _completion_evidence_thread(self) -> None:
        if self._workspace_path is None or self._completion_run_id is None:
            return
        evaluate_completion_fn = self._evaluate_completion_fn
        if evaluate_completion_fn is None:
            return
        while not self._monitor_stop.wait(0.25):
            signals = evaluate_completion_fn(
                self._workspace_path,
                [],
                required_artifact=self._required_artifact,
                run_id=self._completion_run_id,
                sentinel_secret=_parent_broker_secret(),
                receipt_secret=_parent_broker_secret(),
            )
            if completion_signals_terminal(signals):
                self._request_interactive_exit()
                return

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
        pid = cast(
            "int | None", getattr(self._handle, "pid", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if pid is not None and self._handle.poll() is None:
            teardown_subtree(pid)
        # Real resume-safety from the canonical helper at
        # ``_process_reader._is_resumable_fire_reason`` so the PTY
        # watchdog kill path produces the same resume-safety signal
        # as the subprocess path. Mirrors the same helper used by
        # ``_process_reader._check_fire``.
        resume_safe = _is_resumable_fire_reason(fire_reason)
        # The canonical watchdog-kill warning is logged by
        # ``IdleWatchdog._emit_fire_log``; we do not duplicate it
        # here. The subprocess path also logs only the canonical
        # warning; the ``resume_safe`` / ``resumable_session_id``
        # surface is the typed ``IdleWatchdogKilledError`` attached
        # as ``__cause__`` and the merged_diag payload.
        # Captured transport session id from the reader-level cache
        # (mirrors ``_process_reader._captured_session_id``). Populated
        # by :meth:`_record_transcript_session_id` from the visible-TUI
        # extractor so PTY-visible session ids are promoted into the
        # same resumable path the subprocess reader uses.
        captured_session_id = self._captured_session_id
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
        now = self._clock.monotonic()
        merged_diag: dict[str, object] = {
            "evidence_summary": watchdog.last_evidence_summary(now).to_dict_list(),
        }
        # Surface the captured session id and the real resume-safety
        # signal in the post-mortem diagnostic so an on-call grep of
        # the merged_diag payload sees the captured id here as well
        # (the ``FailureClassifier.classify`` consults
        # ``exc.__cause__`` first, but the diagnostic carries the id
        # for log-only consumers).
        merged_diag["resumable_session_id"] = captured_session_id
        merged_diag["resume_safe"] = resume_safe
        # The diagnostic_snapshot adds the full watchdog state at the
        # moment of the fire for post-mortem reconstruction. It is
        # optional because a watchdog mock without
        # ``diagnostic_snapshot`` must not crash the fire path.
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
        # The watchdog's typed exception ``IdleWatchdogKilledError``
        # (AC-05: the failure_classifier's typed-attribute branch in
        # ``failure_classifier.py`` consults ``exc.reason`` and
        # ``exc.signal`` directly instead of substring-matching the
        # message) is built here so the PTY watchdog path matches
        # the non-PTY (``_process_reader``) watchdog path. The typed
        # exception is attached to the wrapped payload via
        # ``__cause__`` so a downstream ``failure_classifier.classify``
        # call that walks the ``__cause__`` chain (and the future
        # ``isinstance`` check on the typed exception) sees the typed
        # cause first. The post-exit ``PROCESS_EXIT_HANG`` path
        # (``post_exit_watchdog.py``) still raises
        # ``_IdleStreamTimeoutError`` directly because it is owned
        # by ``PostExitWatchdog``, not ``IdleWatchdog``.
        # Surface the watchdog's corroborator ``alive_by`` signal at
        # the moment of the fire. ``child_alive=True`` means the
        # child was alive per the corroborator (defense-in-depth;
        # normally dead code because the gate refinement in
        # ``IdleWatchdog._is_no_progress_quiet`` defers the
        # ``NO_PROGRESS_QUIET`` fire when alive_by is not None).
        # ``child_alive=False`` means the corroborator returned
        # ``alive_by=None`` (truly dead child -- Rule 2:
        # exponential backoff). The signal is consumed by
        # ``FailureClassifier._classify_unavailability_reason`` to
        # differentiate live-child from dead-child
        # ``NO_PROGRESS_QUIET`` at the typed-evidence level. The
        # ``is not None`` test maps ``None`` (no signal at all) to
        # ``False`` (dead-child / Rule 2 path) and any of the 5
        # non-``None`` ``AliveBy`` values to ``True`` (live-child /
        # Rule 1 defense-in-depth path). ``getattr(..., None)`` is
        # used for backward-compat with test mocks that do not set
        # the ``last_alive_by`` attribute.
        _alive_by_signal: object = getattr(watchdog, "last_alive_by", None)
        _child_alive = _alive_by_signal is not None
        typed_exc = IdleWatchdogKilledError(
            reason=fire_reason.value,
            signal=15,  # SIGTERM
            evidence_summary=str(merged_diag),
            child_alive=_child_alive,
            resumable_session_id=captured_session_id,
        )
        wrapper = _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=cast(
                "_MergedDiagType", merged_diag
            ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def _observe_queued_line(self, queued_line: str) -> None:
        visible_line = _visible_tui_text(queued_line)

        # Maintain a sliding window of recent lines so cross-line menu detection
        # can work even when TUI repaint sequences fragment menu options across
        # individual queued entries. wt-024 M9 (AC-07): the deque(maxlen)
        # cap is enforced automatically on append; no manual pop(0)/len().
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
        with contextlib.suppress(Exception):
            self._handle.close()
        # Mirrors the watchdog-fire path at _check_fire (lines 571-574):
        # terminate the child gracefully and tear down its descendant tree
        # so a Ctrl-C / BaseException does not leave child agents running.
        # All calls are idempotent and wrapped in suppress() so a wedged
        # close/terminate cannot leak the teardown.
        with contextlib.suppress(Exception):
            self._handle.terminate(grace_period_s=0.5)
        pid = cast(
            "int | None", getattr(self._handle, "pid", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if pid is not None:
            with contextlib.suppress(Exception):
                teardown_subtree(pid)

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

    def _raise_if_terminal_startup_error(self, queued_line: str) -> None:
        terminal_startup_error = _terminal_interactive_startup_error(
            self._agent_name,
            queued_line,
        )
        if terminal_startup_error is None:
            return
        with contextlib.suppress(AttributeError, OSError, ProcessLookupError, RuntimeError):
            self._handle.terminate(grace_period_s=0.5)
        pid = cast(
            "int | None", getattr(self._handle, "pid", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if pid is not None:
            teardown_subtree(pid)
        raise AgentInvocationError(
            self._agent_name,
            1,
            terminal_startup_error,
            parsed_output=[_visible_tui_text(queued_line).strip() or queued_line.strip()],
        )

    def _handle_queued_line(self, queued_line: str, watchdog: IdleWatchdog) -> Iterator[str]:
        self._record_transcript_session_id(queued_line)
        self._observe_queued_line(queued_line)
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is not None:
            self._last_activity_kind = activity_signal.kind
            self._last_meaningful[0] = (
                activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
                and not activity_signal.is_harness_echo
            )
            _record_embedded_error(watchdog, activity_signal)
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
                    # NEW BEHAVIOR: feed the tool-call circuit
                    # breaker so the watchdog can fire
                    # REPEATED_IDENTICAL_TOOL_CALL when an agent
                    # re-issues the same (tool_name, tool_args)
                    # combination.  Without this call the
                    # tracker dimension is unreachable in real
                    # runs and the watchdog cannot detect a
                    # tool-call wedge -- exactly the gap the
                    # analysis feedback surfaced.  We swallow
                    # extraction errors (non-JSON raw line) so
                    # the breaker is fed only when we have a
                    # stable (name, args) fingerprint to
                    # contribute.
                    tool_call = _extract_tool_call_from_activity_signal(activity_signal.raw)
                    self._last_tool_use_name = _tool_use_display_name(
                        activity_signal.raw, tool_call
                    )
                    if tool_call is not None:
                        watchdog.record_tool_call_activity(*tool_call)
                    watchdog.record_tool_use_activity()
                elif activity_signal.kind == AgentActivityKind.TOOL_RESULT:
                    self._awaiting_post_tool_result_progress = True
                    self._last_tool_result_at = self._clock.monotonic()
                    self._last_tool_result_excerpt = activity_signal.raw.strip()[:200]
                    # NOT record_activity(): its note_progress() wiped the
                    # tool-call repetition streak after every completed call,
                    # putting back exactly what record_tool_result_activity
                    # documents it must not do. That method (called below)
                    # already resets the idle baseline and marks meaningful
                    # output.
                elif activity_signal.kind == AgentActivityKind.OUTPUT_LINE:
                    self._awaiting_post_tool_result_progress = False
                    watchdog.record_activity()
                else:
                    watchdog.record_activity()
                # NEW BEHAVIOR: also record the post-tool-result
                # activity so the watchdog's new direct-fire
                # STALLED_AFTER_TOOL_RESULT path can detect the wedge
                # in ~120s by default (the post-tool-result budget)
                # rather than waiting for the full 300s idle timeout.
                if activity_signal.kind == AgentActivityKind.TOOL_RESULT:
                    _record_tool_result_activity(
                        watchdog,
                        is_harness_echo=activity_signal.is_harness_echo,
                    )
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

    def _strategy_registry_and_prefix(
        self,
    ) -> tuple[ChildLivenessRegistry | None, str]:
        """Return ``(registry, scope_prefix)`` from the strategy, or ``(None, "")``.

        Mirrors ``_process_reader._strategy_registry_and_prefix``. The
        prefix lookup is wrapped in a try/except so a strategy stub that
        omits ``_label_scope`` (used by some test fakes) does not crash
        the caller.
        """
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
        """Build a PID source from the strategy's child-liveness registry, if any.

        Mirrors ``_process_reader._build_subagent_pid_source``: the
        OpenCode strategy ingests structured child lifecycle events
        (carried on the agent's own PTY stream) into a
        ``ChildLivenessRegistry`` whose records include the child PID.
        Returning those PIDs as a ``SubagentPidSource`` lets
        ``DefaultProcessMonitor`` classify real spawned subagents as
        ``SPAWNED_SUBAGENT`` instead of guessing from the command
        line. Without this wiring the PTY/OpenCode path undercounts
        active child agents and the watchdog would make decisions
        from incomplete process classification.
        """
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
            self._lines_event.clear()
            queued_line: str | None = None
            is_done = False
            with self._lines_lock:
                if self._lines_queue:
                    # BoundedLinesQueue exposes O(1) popleft; raw lists fall back to pop(0).
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
        reader = self._start_thread(self._read_thread)
        transcript_reader = self._start_thread(self._transcript_thread)
        sentinel_reader = self._start_thread(self._sentinel_thread)
        completion_reader = self._start_thread(self._completion_evidence_thread)
        # RC1 + RC3 (wt-04-claude-parsing): the subagent transcript
        # tailer is now constructed LAZILY inside ``_transcript_thread``
        # on the first observed session id -- the previous shape
        # built the tailer here and saw ``_captured_session_id is None``
        # for every fresh invocation, so the tailer never came back
        # populated. We still pre-allocate the sink holder here so
        # the lazy constructor can find the watchdog binding once the
        # watchdog exists. ``_teardown_read_loop`` stops the
        # lazily-constructed tailer via ``self._subagent_tails``.
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
        # R1 (Trustworthy Idle Watchdog spec): store the monitor so
        # ``_corroborate`` can read the FILTERED subagent count from
        # ``spawned_subagent_count()`` (preferred) instead of the
        # broader ``handle.descendant_snapshot()`` count. The monitor
        # is reset in the ``finally`` block so a stale monitor never
        # leaks across invocations.
        self._process_monitor = process_monitor
        self._raw_overflow = get_or_create_raw_overflow_log(
            self._workspace_path or Path.cwd(),
            self._agent_name,
            model=self._config.model,
        )
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
        # R7 (Trustworthy Idle Watchdog): expose the watchdog
        # reference on the PTY line reader so the line-reader
        # layer can populate the R7 diagnostic fields on
        # ``_CompletionCheckOptions`` at the construction site
        # AFTER the iterator exhausts (post-read at
        # ``_pty_runner.py``).
        self._watchdog = watchdog
        self._bind_workspace_monitor(watchdog)
        sink_token, subagent_token, subagent_sink = self._register_active_sinks(watchdog)
        # RC1 + RC3 (wt-04-claude-parsing): bind the watchdog's
        # ``record_subagent_work`` channel into the sink holder the
        # lazy tailer constructor reads from. The tailer consults
        # ``self._subagent_sink_holder`` so the watchdog binding is
        # a one-line swap rather than a constructor re-wire. The
        # tailer's ``start()`` is fired by ``_transcript_thread`` on
        # the first ``Agent`` / ``Task`` dispatch rather than here
        # so a no-dispatch session never spins up a tailer thread.
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
        if self._monitor is None:
            return
        # Forward (kind, weight) so the watchdog's per-kind
        # counter receives the real classification; the
        # 0-arg bound method form would always yield
        # (OTHER, 1.0) and miss the AC #7 contract.
        def _forward_event(kind: WorkspaceChangeKind, weight: float) -> None:
            watchdog.record_workspace_event(kind=kind, weight=weight)

        self._monitor.set_on_event(_forward_event)

    @staticmethod
    def _register_active_sinks(
        watchdog: IdleWatchdog,
    ) -> tuple[Token[Callable[[str], None] | None], Token[Callable[[str], None] | None], Callable[[str], None]]:
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

        def _subagent_sink(line: str) -> None:
            watchdog.record_subagent_work(description=line)

        sink_token = set_active_sink(_mcp_sink)
        subagent_token = set_subagent_sink(_subagent_sink)
        return sink_token, subagent_token, _subagent_sink

    def _teardown_read_loop(self, setup: _ReadLoopSetup, interrupted: bool) -> None:
        setup.watchdog.record_invocation_end()
        reset_active_sink(setup.sink_token)
        reset_subagent_sink(setup.subagent_token)
        if self._monitor is not None:
            self._monitor.set_on_event(None)
        # RC1 + RC3 (wt-04-claude-parsing): the subagent tailer is
        # built lazily inside ``_transcript_thread``; it lives on
        # ``self._subagent_tails`` after the first observed session
        # id, so we stop the lazy-constructed instance here (the
        # setup-time ``subagent_tails`` slot is always ``None`` now).
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
        # Flush and release the raw-overflow log so buffered tail bytes
        # reach disk deterministically (RFC-013 P1: per-line open/close
        # churn on .agent/raw/*.log was a top engine event source).
        # close() is idempotent and never raises. The optional guard
        # covers the rare setup-throws-mid-init case where the log was
        # never constructed.
        if self._raw_overflow is not None:
            self._raw_overflow.close()
