"""Subprocess-based process line reader."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

import psutil
from loguru import logger
from tqdm import tqdm

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    PostExitVerdict,
    PostExitWatchdog,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._completion import (
    _check_process_result,
    _CompletionCheckOptions,
    completion_run_id_from_extra_env,
)
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._lines_queue_helpers import _pop_queue_line
from ralph.agents.invoke._session import (
    _EXPLICIT_COMPLETION_MARKER,
    _bounded_output_lines,
    extract_transport_session_id_from_line,
)
from ralph.agents.invoke._tool_call_extraction import (
    extract_tool_call_from_activity_signal as _extract_tool_call_from_activity_signal_impl,
)
from ralph.agents.invoke._types import _AgentRunCtx, _ProcessReaderCtx
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.display.raw_overflow import RawOverflowLog
from ralph.mcp.server._activity_sink import (
    ActivitySink,
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
    ManagedProcess,
    ProcessEvent,
    ProcessStatus,
    SpawnOptions,
    get_process_manager,
)
from ralph.process.teardown import teardown_subtree

from ._monitor_factory import _make_process_monitor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextvars import Token

    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.config.models import AgentConfig
    from ralph.mcp.server._activity_sink import ActivitySink
    from ralph.process.monitor import ProcessMonitor

_MAX_PARSED_OUTPUT_LINES = 256
_NON_MEANINGFUL_ACTIVITY_KINDS: frozenset[AgentActivityKind] = frozenset(
    {
        AgentActivityKind.LIFECYCLE,
        AgentActivityKind.ERROR_LINE,
        AgentActivityKind.PROGRESS_REPORT,
    }
)
_TERMINAL_PROCESS_STATUSES: frozenset[ProcessStatus] = frozenset(
    {ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED}
)


def _agent_command_name(config: AgentConfig) -> str:
    return shlex.split(config.cmd)[0]


# Canonical resumable-fire-reason set for the watchdog-kill -> resume
# flow.  This set is the single source of truth consulted by BOTH
# line readers (subprocess ``_process_reader.py`` AND PTY
# ``_pty_runner.py``) so the recovery controller's
# ``recovery_action_for_failure_reason`` returns ``'resume'`` for the
# same in-set reasons end-to-end.  ``CHILDREN_PERSIST_TOO_LONG`` is
# deliberately EXCLUDED: a long cumulative child-wait is not safe to
# resume (the child may have side effects outside the agent session),
# so the recovery must restart from a fresh session.  Any new
# ``WatchdogFireReason`` member that should resume MUST be added here
# AND in ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``.
_RESUMABLE_FIRE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_AT_START,
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        WatchdogFireReason.REPEATED_ERROR_LOOP,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
    }
)


def _is_resumable_fire_reason(reason: WatchdogFireReason) -> bool:
    """Return True when the watchdog fire reason is safe to resume.

    Mirrors the contract pinned in
    ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``:
    only the six production reasons plus
    ``REPEATED_IDENTICAL_TOOL_CALL`` are resumable; everything else
    (``PROCESS_EXIT_HANG``, ``DESCENDANT_HANG``,
    ``SESSION_CEILING_EXCEEDED``, ``CHILDREN_PERSIST_TOO_LONG``,
    ``DEFERRED_BY_STUCK_CLASSIFIER``) must restart from a fresh
    session.
    """
    return reason in _RESUMABLE_FIRE_REASONS


def _convert_idle_stream_timeout_to_agent_error(
    agent_name: str,
    exc: _IdleStreamTimeoutError,
    parsed_output: tuple[str, ...] | list[str],
    *,
    explicit_completion_seen: bool = False,
    captured_session_id: str | None = None,
    expected_session_id: str | None = None,
) -> AgentInactivityTimeoutError:
    """Convert an in-stream ``_IdleStreamTimeoutError`` into ``AgentInactivityTimeoutError``.

    This is the canonical invocation-layer seam that the line readers
    use when the idle watchdog fires (or post-exit detects a hang). The
    wrapper exception carries the watchdog fire reason; this helper
    threads the session-resume metadata (``session_resume_safe`` and
    ``resumable_session_id``) that the recovery controller consults
    when deciding whether to resume the prior agent session.
    """
    session_resume_safe = _is_resumable_fire_reason(exc.reason)
    return AgentInactivityTimeoutError(
        agent_name,
        exc.timeout_seconds,
        _bounded_output_lines(
            tuple(parsed_output),
            explicit_completion_seen=explicit_completion_seen,
        ),
        InactivityTimeoutOpts(
            reason=exc.reason,
            session_resume_safe=session_resume_safe,
            resumable_session_id=captured_session_id or expected_session_id,
            diagnostic=exc.diagnostic,
        ),
    )


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    # ``RALPH_BROKER_SECRET`` is the broker-owned HMAC secret used by the
    # orchestrator to bind completion sentinels / receipts to forge-proof
    # claims. It MUST NOT leak to spawned agent subprocesses — the
    # anti-forgery boundary is enforced by keeping the secret on the
    # parent side only. Strip from both the inherited environment AND
    # any caller-supplied ``extra_env`` so neither path can smuggle the
    # secret into the child process.
    env.pop("RALPH_BROKER_SECRET", None)
    if extra_env:
        env.update(extra_env)
    env.pop("RALPH_BROKER_SECRET", None)
    return env


def _extract_tool_call_from_activity_signal(
    raw: str,
) -> tuple[str, dict[str, object]] | None:
    """Backward-compat alias re-exported from ``_tool_call_extraction``.

    The implementation was extracted to
    :mod:`ralph.agents.invoke._tool_call_extraction` to keep this
    module under the 1000-line policy cap. The import path is
    preserved for callers and tests.
    """
    return _extract_tool_call_from_activity_signal_impl(raw)


class _ProcessLineReader:
    """Reads lines from a subprocess stdout in a background thread."""

    def __init__(self, handle: ManagedProcess, ctx: _ProcessReaderCtx, clock: Clock) -> None:
        self._handle = handle
        self._config = ctx.config
        self._policy = ctx.policy
        self._strategy = ctx.execution_strategy or GenericExecutionStrategy()
        self._probe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._pre_output_listener = ctx.pre_output_listener
        self._monitor = ctx.monitor
        self._connectivity_state_provider = ctx.connectivity_state_provider
        self._is_waiting_state_provider = ctx.is_waiting_state_provider
        self._clock = clock
        self._workspace_path = ctx.workspace_path
        self._lines_queue: BoundedLinesQueue = BoundedLinesQueue(maxlen=_MAX_PARSED_OUTPUT_LINES)
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_activity_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._reader_done: list[bool] = [False]
        self._cpu_baselines: dict[int, tuple[float, float]] = {}  # bounded-accumulator-ok: drained
        self._log_growth_state: dict[str, tuple[int, float]] = {}  # bounded-accumulator-ok: drained
        self._raw_overflow = RawOverflowLog(
            self._workspace_path or Path.cwd(),
            _agent_command_name(self._config),
        )
        self._last_activity_kind = "none"
        # Captured transport-level session id from the most recent
        # ``Session ID:`` / ``--resume`` / JSON-shaped session event on
        # the subprocess stdout. Mirrors ``captured_session_id`` in
        # ``_run_subprocess_and_read_lines`` but is stored at the
        # reader level so ``_check_fire`` can read it at the moment of
        # the watchdog fire WITHOUT re-walking the stdout pipe. The
        # canonical seam is still ``extract_transport_session_id_from_line``
        # called from ``_read_thread`` below; this field is the
        # cache so the watchdog-kill path does not need to retain
        # ``_lines_queue`` content for re-scanning.
        self._captured_session_id: str | None = None
        self._unsubscribe = get_process_manager().register_listener(self._on_process_event)

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _build_subagent_pid_source(self) -> ChildLivenessSubagentPidSource | None:
        """Build a PID source from the strategy's child-liveness registry, if any.

        OpenCode emits structured child lifecycle events on stdout. The
        strategy ingests those events into a ``ChildLivenessRegistry`` whose
        records include the child PID. Returning those PIDs as a
        ``SubagentPidSource`` lets ``DefaultProcessMonitor`` classify real
        spawned subagents as ``SPAWNED_SUBAGENT`` instead of guessing from
        the command line.
        """
        registry, prefix = self._strategy_registry_and_prefix()
        if registry is None:
            return None
        return ChildLivenessSubagentPidSource(registry, prefix)

    def _strategy_registry_and_prefix(
        self,
    ) -> tuple[ChildLivenessRegistry | None, str]:
        """Return ``(registry, scope_prefix)`` from the strategy, or ``(None, "")``.

        Reads the strategy's ``_registry`` attribute and resolves the
        active label scope prefix via ``_active_label_prefix``. The prefix
        lookup is wrapped in a try/except so a strategy stub that omits
        ``_label_scope`` (used by some test fakes) does not crash the
        caller. The returned ``scope_prefix`` is ``""`` when the registry
        is missing or when the prefix lookup raises so downstream
        ``OpenCodeRegistryDiscoveryStrategy`` calls always receive a
        well-typed string.
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

    def _bind_watchdog_monitors_and_sinks(
        self, watchdog: IdleWatchdog
    ) -> tuple[Token[ActivitySink | None], Token[ActivitySink | None]]:
        """Bind workspace monitor and MCP/subagent sinks to the watchdog."""
        if self._monitor is not None:

            def _forward_event(kind: WorkspaceChangeKind, weight: float) -> None:
                watchdog.record_workspace_event(kind=kind, weight=weight)

            self._monitor.set_on_event(_forward_event)

        def _mcp_sink(_tool_name: str) -> None:
            watchdog.record_mcp_tool_call()

        def _subagent_sink(line: str) -> None:
            watchdog.record_subagent_work(description=line)

        sink_token = set_active_sink(_mcp_sink)
        subagent_token = set_subagent_sink(_subagent_sink)
        return sink_token, subagent_token

    def _on_waiting_event(self, evt: WaitingStatusEvent) -> None:
        if evt.kind == WaitingStatusKind.HARD_STOP:
            self._last_hard_stop[0] = evt
        if self._waiting_listener is not None:
            self._waiting_listener(evt)

    def _probe_cpu_idle(
        self,
        scoped_active: bool | None,
        alive_by: AliveBy | None,
    ) -> bool:
        if (
            self._policy.cpu_idle_seconds is None
            or not scoped_active
            or alive_by not in (None, AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS)
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
        if self._policy.log_growth_seconds is None:
            return False
        _is_initial_state = bool(
            alive_by is None or alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        )
        if not _is_initial_state:
            return False
        if self._raw_overflow.is_disabled:
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
        ws_count: int | None = self._monitor.event_count if self._monitor is not None else None
        last_workspace_event_at: float | None = (
            self._monitor.last_event_at if self._monitor is not None else None
        )
        scoped_active: bool | None = None
        scoped_count: int | None = None
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
        oldest_secs_from_legacy = None
        try:
            monitor: ProcessMonitor | None = getattr(self, "_process_monitor", None)
            if monitor is not None:
                filtered_count: int = monitor.spawned_subagent_count()
                scoped_count = filtered_count
                scoped_active = filtered_count > 0
            else:
                # Legacy escape hatch: ``process_monitor_enabled=False``
                # (e.g. integration tests pre-dating the R5 registry
                # seam). Reads the broader descendant count so the
                # watchdog still has a signal. Production callers
                # always have a monitor.
                try:
                    desc_count, desc_oldest = self._handle.descendant_snapshot()
                    scoped_count = desc_count
                    scoped_active = desc_count > 0
                    oldest_secs_from_legacy = desc_oldest
                except Exception:
                    logger.debug(
                        "corroborator: legacy descendant_snapshot fallback failed (suppressed)"
                    )
        except Exception:
            logger.debug("corroborator: process monitor count failed (suppressed)")
            scoped_count = None
            scoped_active = None
        oldest_secs: float | None = oldest_secs_from_legacy
        # Type assertion for mypy: after the try/except above,
        # scoped_count is guaranteed to be int (when the monitor
        # produced a value) or None (when no monitor was injected
        # or the call raised). The expression below reads
        # ``scoped_count`` as ``int | None`` so the snapshot's
        # typed ``scoped_child_count`` argument matches.
        scoped_count_int: int | None = scoped_count
        alive_by: AliveBy | None = None
        reg = cast("ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None))
        if reg is not None:
            try:
                label_prefix = cast(
                    "str | None",
                    getattr(self._strategy, "_active_label_prefix", lambda: None)(),
                )
                reg_snap = reg.snapshot(label_prefix or "")
                verdict = classify_child_snapshot(reg_snap, has_os_descendants=bool(scoped_active))
                alive_by = verdict.alive_by
            except Exception:
                logger.debug("corroborator: registry snapshot failed (suppressed)")
        elif scoped_active:
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS

        _cpu_idle = self._probe_cpu_idle(scoped_active, alive_by)
        _log_stale = self._probe_log_growth(alive_by)

        if _cpu_idle or _log_stale:
            _override: AliveBy = (
                AliveBy.LOG_STALE_WHILE_ALIVE if _log_stale else AliveBy.CPU_IDLE_WHILE_ALIVE
            )
            if alive_by in (None, AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS):
                alive_by = _override

        return CorroborationSnapshot(
            workspace_event_count=ws_count,
            last_workspace_event_at=last_workspace_event_at,
            oldest_child_seconds=oldest_secs,
            scoped_child_active=scoped_active,
            scoped_child_count=scoped_count_int,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_activity_meaningful[0],
            alive_by=alive_by,
        )

    def _read_thread(self) -> None:
        stdout_pipe = cast("IO[str] | None", self._handle.stdout)
        if stdout_pipe is None:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()
            return

        self._announce_pre_output_progress()
        try:
            for line in stdout_pipe:
                with self._lines_lock:
                    self._lines_queue.append(line)
                    self._lines_event.set()
                # Per-line session id capture mirrors the canonical
                # extraction in ``_run_subprocess_and_read_lines``
                # (``captured_session_id`` accumulator there) so the
                # watchdog-kill -> resume path sees the same id at the
                # reader level. The two captures are independent
                # (reader vs. outer generator) but the extraction is
                # idempotent so both end up with the same value.
                session_id = extract_transport_session_id_from_line(line)
                if session_id is not None:
                    self._captured_session_id = session_id
        except Exception:
            pass
        finally:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()

    def _announce_pre_output_progress(self) -> None:
        if self._pre_output_listener is not None:
            with contextlib.suppress(Exception):
                self._pre_output_listener()

    def _classify_quiet(self) -> AgentExecutionState:
        try:
            return self._strategy.classify_quiet(self._handle, self._probe)
        except Exception:
            logger.opt(exception=True).debug(
                "idle watchdog: classify_quiet raised; defaulting to WAITING_ON_CHILD"
            )
            return AgentExecutionState.WAITING_ON_CHILD

    def _check_fire(
        self, watchdog: IdleWatchdog, verdict: WatchdogVerdict
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
            else self._policy.no_progress_quiet_seconds
            if fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        # The captured transport session id (mirrors
        # ``captured_session_id`` in ``_run_subprocess_and_read_lines``
        # but reads the reader-level cache so the value is available
        # at fire time without re-scanning the stdout pipe).
        captured_session_id = self._captured_session_id
        # Real resume-safety from the canonical helper at
        # ``_process_reader._is_resumable_fire_reason`` (NOT the
        # hardcoded ``false`` that pre-fix this method logged).
        resume_safe = _is_resumable_fire_reason(fire_reason)
        logger.warning(
            "idle watchdog firing reason={} idle_elapsed={}s cumulative_waiting={}s "
            "last_activity_kind={} resume_safe={} resumable_session_id={}",
            fire_reason,
            round(watchdog.idle_elapsed_seconds(self._clock.monotonic()), 1),
            round(watchdog.cumulative_waiting_on_child_seconds, 1),
            self._last_activity_kind,
            resume_safe,
            captured_session_id,
        )
        with self._lines_lock:
            pending = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
        pid = cast("int | None", getattr(self._handle, "pid", None))
        if pid is not None:
            teardown_subtree(pid)
        hs_event = self._last_hard_stop[0]
        hard_stop_diag = hs_event.diagnostic if hs_event is not None else None
        # Always merge the watchdog's per-channel evidence summary into
        # the diagnostic so a post-mortem (or the on-call operator) can
        # see exactly which evidence channels were fresh and which
        # were stale at the moment the watchdog fired. The watchdog's
        # own ``last_evidence_summary`` produces a list of
        # ``ChannelEvidenceSummary.to_dict()`` entries; we surface that
        # under ``evidence_summary`` alongside any HARD_STOP diagnostic
        # the watchdog already populated.
        now = self._clock.monotonic()
        evidence_block = {
            "evidence_summary": watchdog.last_evidence_summary(now).to_dict_list(),
        }
        merged_diag: dict[str, object] = dict(evidence_block)
        # Surface the captured session id and the real resume-safety
        # signal in the post-mortem diagnostic. The
        # ``FailureClassifier.classify`` consults ``exc.__cause__``
        # first (which already carries ``resumable_session_id`` on the
        # ``IdleWatchdogKilledError``), but a future reader of the
        # diagnostic that does not walk the exception chain (e.g. an
        # on-call grep of the merged_diag payload) must see the
        # captured id here as well.
        merged_diag["resumable_session_id"] = captured_session_id
        merged_diag["resume_safe"] = resume_safe
        # The diagnostic_snapshot adds the full watchdog state at the
        # moment of the fire for post-mortem reconstruction. It is
        # optional because a watchdog mock without ``diagnostic_snapshot``
        # must not crash the fire path.
        snapshot_method: object = getattr(watchdog, "diagnostic_snapshot", None)
        if callable(snapshot_method):
            try:
                snapshot_obj: dict[str, object] | None = snapshot_method(now=now)
            except Exception:
                snapshot_obj = None
            if snapshot_obj:
                merged_diag["watchdog_snapshot"] = snapshot_obj
        if hard_stop_diag is not None:
            for key, value in hard_stop_diag.items():
                if key not in merged_diag:
                    merged_diag[key] = value
        # The watchdog's typed exception ``IdleWatchdogKilledError``
        # (AC-05: the failure_classifier's typed-attribute branch in
        # ``failure_classifier.py`` consults ``exc.reason`` and
        # ``exc.signal`` directly instead of substring-matching the
        # message) is raised by this method, but the existing
        # ``except _IdleStreamTimeoutError as exc:`` block in
        # ``_run_subprocess_and_read_lines`` is the conversion seam
        # to ``AgentInactivityTimeoutError`` for the recovery
        # layer. The typed exception is attached to the wrapped
        # payload via ``__cause__`` so a downstream
        # ``failure_classifier.classify`` call that walks the
        # ``__cause__`` chain (and the future ``isinstance`` check
        # on the typed exception) sees the typed cause first. The
        # post-exit ``PROCESS_EXIT_HANG`` path
        # (post_exit_watchdog.py) still raises
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
                "dict[str, str | int | float | bool | list[object]] | None",
                merged_diag,
            ),
        )
        wrapper.__cause__ = typed_exc
        return pending, wrapper

    def _record_line_activity(self, watchdog: IdleWatchdog, queued_line: str) -> None:
        """Classify a line and route it to the matching watchdog activity sink.

        ERROR_LINE and repeated PROGRESS_REPORT lines feed the repeated-error
        circuit breaker without resetting the idle baseline; LIFECYCLE frames
        reset idle only; everything else is genuine forward progress.
        TOOL_USE activity also feeds the tool-call circuit breaker so an
        agent wedged in an identical-tool-call retry loop trips
        REPEATED_IDENTICAL_TOOL_CALL.
        """
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is None:
            self._last_activity_meaningful[0] = False
            return
        self._last_activity_kind = str(activity_signal.kind)
        self._last_activity_meaningful[0] = (
            activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
        )
        if activity_signal.kind == AgentActivityKind.ERROR_LINE:
            watchdog.record_error_activity(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.PROGRESS_REPORT:
            watchdog.record_progress_report(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.LIFECYCLE:
            watchdog.record_lifecycle_activity()
        else:
            if activity_signal.kind == AgentActivityKind.TOOL_USE:
                # NEW BEHAVIOR: feed the tool-call circuit breaker so
                # the watchdog can fire REPEATED_IDENTICAL_TOOL_CALL
                # when an agent re-issues the same (tool_name,
                # tool_args) combination.  The extraction is
                # best-effort -- non-JSON or unknown envelopes are
                # silently skipped so the breaker sees only stable
                # (name, args) fingerprints.
                tool_call = _extract_tool_call_from_activity_signal(activity_signal.raw)
                if tool_call is not None:
                    tool_name, tool_args = tool_call
                    watchdog.record_tool_call_activity(tool_name, tool_args)
            watchdog.record_activity()

    def _run_drain_window(
        self, watchdog: IdleWatchdog, drain_deadline: float | None
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        while True:
            result = self._check_fire(
                watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
            )
            if result is not None:
                return result
            if drain_deadline is None and self._handle.poll() is not None:
                return None
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def read_lines(self) -> Iterator[str]:
        reader = threading.Thread(target=self._read_thread, daemon=True)
        reader.start()
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
        # reference on the reader so the line-reader layer can
        # populate the R7 diagnostic fields on
        # ``_CompletionCheckOptions`` at the construction site
        # AFTER the iterator exhausts (post-read at
        # ``_process_reader.py:945``). The watchdog is also
        # closed-loop in the ``finally`` block below; the reader
        # only consumes the reference while it is in scope.
        self._watchdog = watchdog

        sink_token, subagent_token = self._bind_watchdog_monitors_and_sinks(watchdog)
        try:
            while True:
                self._lines_event.clear()
                queued_line: str | None = None
                is_done = False
                with self._lines_lock:
                    if self._lines_queue:
                        # BoundedLinesQueue exposes O(1) popleft; raw lists
                        # (used by tests) fall back to O(n) pop(0).
                        queued_line = _pop_queue_line(self._lines_queue)
                    elif self._reader_done[0]:
                        is_done = True

                if queued_line is not None:
                    self._record_line_activity(watchdog, queued_line)
                    self._strategy.observe_line(queued_line)
                    self._raw_overflow.append(queued_line)
                    yield queued_line
                    result = self._check_fire(
                        watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                    )
                    if result is not None:
                        pending_lines, exc = result
                        yield from pending_lines
                        raise exc
                    continue

                if is_done:
                    drain_secs = self._policy.drain_window_seconds or 0
                    if drain_secs > 0 and self._policy.idle_timeout_seconds is not None:
                        drain_secs += self._policy.idle_poll_interval_seconds
                    drain_deadline = (
                        self._clock.monotonic() + drain_secs if drain_secs > 0 else None
                    )
                    result = self._run_drain_window(watchdog, drain_deadline)
                    if result is not None:
                        pending_lines, exc = result
                        yield from pending_lines
                        raise exc
                    break

                result = self._check_fire(
                    watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                )
                if result is not None:
                    pending_lines, exc = result
                    yield from pending_lines
                    raise exc

                self._clock.wait_for_event(
                    self._lines_event, self._policy.idle_poll_interval_seconds
                )

            reader.join(timeout=10)
        finally:
            reset_active_sink(sink_token)
            reset_subagent_sink(subagent_token)
            if self._monitor is not None:
                self._monitor.set_on_event(None)
            self._raw_overflow.close()
            self._unsubscribe()


def _run_subprocess_and_read_lines(
    cmd: list[str],
    ctx: _AgentRunCtx,
) -> Iterator[str]:
    """Run subprocess and yield output lines.

    Args:
        cmd: Command to execute.
        ctx: Agent run context with configuration and options.

    Yields:
        Output lines from the subprocess.
    """
    clock: Clock = ctx.clock or SystemClock()
    handle = get_process_manager().spawn(
        cmd,
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=None,
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            start_new_session=True,
            label=f"invoke:{_agent_command_name(ctx.config)}",
            text=True,
        ),
    )
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        stdout_pipe = handle.stdout
        if stdout_pipe is None:
            msg = "Failed to capture stdout"
            raise AgentInvocationError(_agent_command_name(ctx.config), -1, msg)

        reader_ctx = _ProcessReaderCtx(
            config=ctx.config,
            policy=ctx.policy,
            execution_strategy=ctx.execution_strategy,
            liveness_probe=ctx.liveness_probe,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=ctx.pre_output_listener,
            monitor=ctx.monitor,
            expected_session_id=ctx.expected_session_id,
            workspace_path=ctx.workspace_path,
            connectivity_state_provider=ctx.connectivity_state_provider,
            is_waiting_state_provider=ctx.is_waiting_state_provider,
        )
        reader = _ProcessLineReader(handle, reader_ctx, clock)
        lines_iter = reader.read_lines()
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
                    session_id = extract_transport_session_id_from_line(stripped_line)
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
                    session_id = extract_transport_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line

            post_exit = PostExitWatchdog(ctx.policy, clock)
            verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
            if verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
                handle.terminate(grace_period_s=0.5)
                exit_pid = cast("int | None", getattr(handle, "pid", None))
                if exit_pid is not None:
                    teardown_subtree(exit_pid)
                raise _IdleStreamTimeoutError(
                    ctx.policy.process_exit_wait_seconds,
                    WatchdogFireReason.PROCESS_EXIT_HANG,
                )
        except _IdleStreamTimeoutError as exc:
            raise _convert_idle_stream_timeout_to_agent_error(
                _agent_command_name(ctx.config),
                exc,
                tuple(parsed_output),
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
                expected_session_id=reader_ctx.expected_session_id,
            ) from exc

        # R7 (Trustworthy Idle Watchdog): populate the diagnostic
        # fields on ``_CompletionCheckOptions`` from the watchdog
        # state held on the reader (``reader._watchdog`` was set
        # at the start of ``read_lines()``). The helper at
        # ``_collect_r7_diagnostic_fields`` extracts the four
        # fields into a tuple so this function stays under the
        # PLR0912 / PLR0915 branch / statement limits.
        (
            evidence_summary_str,
            last_tool_call_str,
            elapsed_value,
            transcript_tail,
        ) = _collect_r7_diagnostic_fields(
            reader=reader,
            clock=clock,
            parsed_output=parsed_output,
        )

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
                completion_run_id=completion_run_id_from_extra_env(ctx.extra_env),
                evaluate_completion_fn=ctx.evaluate_completion_fn,
                last_observed_tool_call=last_tool_call_str,
                last_evidence_summary=evidence_summary_str,
                elapsed_seconds=elapsed_value,
                transcript_tail=transcript_tail,
                # di-seam-allowlist: composition-root reads broker secret for completion validation.
                sentinel_secret=os.environ.get("RALPH_BROKER_SECRET"),
                receipt_secret=os.environ.get("RALPH_BROKER_SECRET"),
            ),
            _clock=clock,
        )


def _read_lines_from_process(
    handle: ManagedProcess,
    *,
    ctx: _ProcessReaderCtx,
    _clock: Clock | None = None,
) -> Iterator[str]:
    clock: Clock = _clock or SystemClock()
    return _ProcessLineReader(handle, ctx, clock).read_lines()


def _collect_r7_diagnostic_fields(
    *,
    reader: object,
    clock: Clock,
    parsed_output: deque[str],
) -> tuple[str | None, str | None, float | None, tuple[str, ...]]:
    """Extract the four R7 diagnostic fields from the reader's watchdog.

    R7 (Trustworthy Idle Watchdog spec) requires the
    ``OpenCodeResumableExitError`` to carry the captured watchdog
    state at the moment of the rc=0 exit so a logged traceback is
    actionable. The helper returns a tuple of
    ``(last_evidence_summary, last_observed_tool_call, elapsed_seconds,
    transcript_tail)`` extracted from the watchdog instance held
    on the line reader (``reader._watchdog``, set at the start of
    ``read_lines()``). The ``reader`` parameter is typed as
    ``object`` so both ``_ProcessLineReader`` (subprocess transport,
    ``_process_reader.py``) and ``PtyLineReader`` (PTY transport,
    ``_pty_line_reader.py``) can be passed in -- both expose the
    same ``_watchdog`` attribute.

    The ``transcript_tail`` is hard-capped to the last 10 entries
    of ``parsed_output`` via tuple slice so audit_resource_lifecycle
    accepts it (the dataclass field is typed as ``tuple[str, ...]``,
    never a list literal). ``last_evidence_summary`` is str-coerced
    from the ``to_dict_list()`` payload via the canonical coercion
    pattern already used at ``_process_reader.py:598`` for the
    watchdog-kill merged_diag payload.

    Every read is wrapped in ``try / except Exception`` so a
    misbehaving watchdog mock cannot crash the rc=0 path; the
    caller falls back to ``None`` / ``()`` on any exception.

    Returns:
      A tuple of ``(last_evidence_summary, last_observed_tool_call,
      elapsed_seconds, transcript_tail)``. The dataclass field
      order matches the keyword-only signature of
      ``_CompletionCheckOptions``.
    """
    watchdog_for_diag: IdleWatchdog | None = getattr(reader, "_watchdog", None)
    if watchdog_for_diag is None:
        return None, None, None, ()
    diag_now = clock.monotonic()
    try:
        evidence_summary_obj = watchdog_for_diag.last_evidence_summary(diag_now)
        evidence_summary_str: str | None = str(evidence_summary_obj.to_dict_list())
    except Exception:
        evidence_summary_str = None
    try:
        last_tool_call_obj: object = watchdog_for_diag.diagnostic_snapshot(
            now=diag_now
        ).get("current_subagent_tool_call")
        last_tool_call_str: str | None = (
            str(last_tool_call_obj) if last_tool_call_obj is not None else None
        )
    except Exception:
        last_tool_call_str = None
    try:
        elapsed_value: float | None = round(
            watchdog_for_diag.idle_elapsed_seconds(diag_now), 1
        )
    except Exception:
        elapsed_value = None
    transcript_tail: tuple[str, ...] = tuple(list(parsed_output)[-10:])
    return (
        evidence_summary_str,
        last_tool_call_str,
        elapsed_value,
        transcript_tail,
    )
