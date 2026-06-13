"""Subprocess-based process line reader."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from collections import deque
from typing import IO, TYPE_CHECKING, cast

from loguru import logger
from tqdm import tqdm

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, GenericExecutionStrategy
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
    AgentInvocationError,
    InactivityTimeoutOpts,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._session import (
    _EXPLICIT_COMPLETION_MARKER,
    _bounded_output_lines,
    extract_transport_session_id_from_line,
)
from ralph.agents.invoke._types import _AgentRunCtx, _ProcessReaderCtx
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.server._activity_sink import (
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.process.child_liveness import AliveBy, ChildLivenessRegistry, classify_child_snapshot
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import (
    ManagedProcess,
    ProcessEvent,
    ProcessStatus,
    SpawnOptions,
    get_process_manager,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.config.models import AgentConfig

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
    return config.cmd.split()[0]


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return env


class _ProcessLineReader:
    """Reads lines from a subprocess stdout in a background thread."""

    def __init__(self, handle: ManagedProcess, ctx: _ProcessReaderCtx, clock: Clock) -> None:
        self._handle = handle
        self._policy = ctx.policy
        self._strategy = ctx.execution_strategy or GenericExecutionStrategy()
        self._probe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._pre_output_listener = ctx.pre_output_listener
        self._monitor = ctx.monitor
        self._clock = clock
        self._lines_queue: list[str] = []
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_activity_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._reader_done: list[bool] = [False]
        self._last_activity_kind = "none"
        self._unsubscribe = get_process_manager().register_listener(self._on_process_event)

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
        last_workspace_event_at: float | None = (
            self._monitor.last_event_at if self._monitor is not None else None
        )
        oldest_secs: float | None = None
        scoped_active: bool | None = None
        scoped_count: int | None = None
        try:
            desc_count, desc_oldest = self._handle.descendant_snapshot()
            scoped_count = desc_count
            scoped_active = desc_count > 0
            oldest_secs = desc_oldest
        except Exception:
            logger.debug("corroborator: process scan failed (suppressed)")
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
        return CorroborationSnapshot(
            workspace_event_count=ws_count,
            last_workspace_event_at=last_workspace_event_at,
            oldest_child_seconds=oldest_secs,
            scoped_child_active=scoped_active,
            scoped_child_count=scoped_count,
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
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        logger.warning(
            "idle watchdog firing reason={} idle_elapsed={}s cumulative_waiting={}s "
            "last_activity_kind={} resume_safe=false",
            fire_reason,
            round(watchdog.idle_elapsed_seconds(self._clock.monotonic()), 1),
            round(watchdog.cumulative_waiting_on_child_seconds, 1),
            self._last_activity_kind,
        )
        with self._lines_lock:
            pending = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
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
            "evidence_summary": [entry.to_dict() for entry in watchdog.last_evidence_summary(now)],
        }
        merged_diag: dict[str, object] = dict(evidence_block)
        if hard_stop_diag is not None:
            for key, value in hard_stop_diag.items():
                if key not in merged_diag:
                    merged_diag[key] = value
        return pending, _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=cast(
                "dict[str, str | int | float | bool | list[object]] | None",
                merged_diag,
            ),
        )

    def _record_line_activity(self, watchdog: IdleWatchdog, queued_line: str) -> None:
        """Classify a line and route it to the matching watchdog activity sink.

        ERROR_LINE and repeated PROGRESS_REPORT lines feed the repeated-error
        circuit breaker without resetting the idle baseline; LIFECYCLE frames
        reset idle only; everything else is genuine forward progress.
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
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def read_lines(self) -> Iterator[str]:
        reader = threading.Thread(target=self._read_thread, daemon=True)
        reader.start()
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
        )

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
                    self._record_line_activity(watchdog, queued_line)
                    self._strategy.observe_line(queued_line)
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
            policy=ctx.policy,
            execution_strategy=ctx.execution_strategy,
            liveness_probe=ctx.liveness_probe,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=ctx.pre_output_listener,
            monitor=ctx.monitor,
            expected_session_id=ctx.expected_session_id,
        )
        lines_iter = _ProcessLineReader(handle, reader_ctx, clock).read_lines()
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
                raise _IdleStreamTimeoutError(
                    ctx.policy.process_exit_wait_seconds,
                    WatchdogFireReason.PROCESS_EXIT_HANG,
                )
        except _IdleStreamTimeoutError as exc:
            session_resume_safe = exc.reason in {
                WatchdogFireReason.NO_OUTPUT_DEADLINE,
                WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
                WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
            }
            raise AgentInactivityTimeoutError(
                _agent_command_name(ctx.config),
                exc.timeout_seconds,
                _bounded_output_lines(
                    tuple(parsed_output),
                    explicit_completion_seen=explicit_completion_seen,
                ),
                InactivityTimeoutOpts(
                    reason=exc.reason,
                    session_resume_safe=session_resume_safe,
                    resumable_session_id=captured_session_id or reader_ctx.expected_session_id,
                    diagnostic=exc.diagnostic,
                ),
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


def _read_lines_from_process(
    handle: ManagedProcess,
    *,
    ctx: _ProcessReaderCtx,
    _clock: Clock | None = None,
) -> Iterator[str]:
    clock: Clock = _clock or SystemClock()
    return _ProcessLineReader(handle, ctx, clock).read_lines()
