from __future__ import annotations

import contextlib
import functools
import os
import shlex
import subprocess
import sys
import threading
import traceback
from collections import deque
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol, TextIO, TypeGuard, cast, runtime_checkable

import psutil
from loguru import logger
from tqdm import tqdm

from ralph.agents._agy_upstream_diagnostic import (
    agy_cli_log_start_offset,
    agy_fresh_cli_log_tail,
)
from ralph.agents._broker_secret import _BROKER_ENV_VAR_NAME, parent_broker_secret
from ralph.agents.activity import AgentActivityKind
from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    with_prompt_echo_flag,
)
from ralph.agents.idle_watchdog import (
    ChannelName,
    CorroborationSnapshot,
    EvidenceSummary,
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
    _MAX_STDERR_CAPTURE_BYTES,
    CompletionCheckOptions,
    _TerminationRecorder,
    _truncation_marker,
    check_process_result,
    completion_run_id_from_extra_env,
    observed_returncode,
    read_bounded_stderr,
    termination_issuer_for_fire_reason,
)
from ralph.agents.invoke._completion_termination import (
    AttemptEnded,
    publish_attempt_facts,
)
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    BrokenAgentExitError,
    InactivityTimeoutOpts,
    SupervisionInfrastructureError,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._lines_queue_helpers import _pop_queue_line
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._session import (
    _EXPLICIT_COMPLETION_MARKER,
    _bounded_output_lines,
    extract_transport_session_id_from_line,
)
from ralph.agents.invoke._tool_call_extraction import (
    extract_tool_call_from_activity_signal as _extract_tool_call_from_activity_signal_impl,
)
from ralph.agents.invoke._types import AgentRunCtx, ProcessReaderCtx
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    BrokenAgentTimeoutObserved,
    CompletionObserved,
    PostExitHangObserved,
    ProviderErrorObserved,
    SilenceObserved,
    StartupFailureObserved,
    resolve_teardown,
)
from ralph.agents.invoke.opencode_subagent_sessions import (
    OpenCodeSubagentSessionProbe,
    SqliteOpenCodeChildPartSource,
    default_opencode_db_path,
)
from ralph.agents.invoke.termination_recorder import return_code_may_be_a_signal
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.checked_accessors import optional_float
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.display.raw_overflow import (
    get_or_create_raw_overflow_log,
    raw_log_unit_id_for,
)
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV
from ralph.mcp.server._activity_sink import (
    ActivitySink,
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.process._spawn_env import scrub_activity_relay_controls
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
from ralph.process.teardown import (
    teardown_subtree,
)
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.runtime_events import current_runtime_event
from ralph.timeout_defaults import (
    BROKEN_AGENT_EXIT_SETTLE_SECONDS,
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
)

from ._monitor_factory import _make_process_monitor
from ._quota_detection import (
    is_quota_kill_evidence,
    provider_quota_line_evidence,
    provider_quota_log_detail_matches,
)

_CURSOR_SSH_MARKERS = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Sequence
    from contextvars import Token

    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
    from ralph.mcp.server._activity_sink import ActivitySink
    from ralph.process.manager import ManagedPtyProcess
    from ralph.process.monitor import ProcessMonitor
    from ralph.process.teardown import ProcessTeardown

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


@runtime_checkable
class _EvidenceSummaryWatchdog(Protocol):
    @property
    def clock(self) -> Clock: ...

    def last_evidence_summary(self, now: float | None = None) -> EvidenceSummary: ...


def _subagent_activity_observed(watchdog: _EvidenceSummaryWatchdog) -> bool | None:
    try:
        channel = watchdog.last_evidence_summary(watchdog.clock.monotonic()).by_name(
            ChannelName.SUBAGENT_LIVENESS
        )
    except Exception:
        return None
    if channel is None:
        return None
    counter = cast("int | None", getattr(channel, "counter", None))
    if counter is None:
        return False
    return counter > 0


def _is_idle_watchdog(value: object) -> TypeGuard[IdleWatchdog]:
    summary: object = getattr(value, "last_evidence_summary", None)
    clock: object = getattr(value, "clock", None)
    return callable(summary) and clock is not None


def _watchdog_elapsed_seconds(watchdog: object) -> float | None:
    raw: object = getattr(watchdog, "invocation_elapsed_seconds", None)
    if raw is None:
        return None
    elapsed = optional_float(raw)
    if elapsed is None:
        raise TypeError(f"expected numeric watchdog elapsed seconds, got {type(raw).__name__}")
    return elapsed


def _termination_recorder_of(reader: object) -> _TerminationRecorder | None:
    raw: object = getattr(reader, "termination_recorder", None)
    if raw is None:
        return None
    if not isinstance(raw, _TerminationRecorder):
        raise TypeError(f"expected a termination recorder, got {type(raw).__name__}")
    return raw


def _record_watchdog_evidence(recorder: _TerminationRecorder, watchdog: object) -> None:
    elapsed = _watchdog_elapsed_seconds(watchdog)
    recorder.record_elapsed_to_terminal(int(elapsed * 1000) if elapsed is not None else None)
    if _is_idle_watchdog(watchdog):
        recorder.record_subagent_activity(_subagent_activity_observed(watchdog))


def _reclassify_supervisor_idle_fire(
    fire_reason: WatchdogFireReason,
    handle: object,
    recorder: _TerminationRecorder,
    *,
    completion_gated: bool,
) -> WatchdogFireReason:
    if not completion_gated:
        return fire_reason
    if not isinstance(handle, ManagedProcess):
        return fire_reason
    if termination_issuer_for_fire_reason(fire_reason) is not TerminationIssuer.SUPERVISOR_IDLE:
        return fire_reason
    exited_returncode = observed_returncode(handle)
    if exited_returncode is None or return_code_may_be_a_signal(exited_returncode):
        return fire_reason
    if not _has_live_session_orphan(handle):
        return fire_reason
    recorder.record_observed_returncode(exited_returncode)
    recorder.record_reported_returncode(exited_returncode)
    return WatchdogFireReason.DESCENDANT_HANG


def _has_live_session_orphan(handle: ManagedProcess) -> bool:
    try:
        process_group = handle.record.pgid
        for proc in psutil.process_iter(["pgid", "status"]):
            info: dict[str, object] = dict(proc.info)
            if info.get("pgid") != process_group or proc.pid == handle.pid:
                continue
            if info.get("status") != "zombie":
                return True
    except Exception:
        return False
    return False


def _attach_fire_attempt_facts(
    exc: _IdleStreamTimeoutError,
    converted: AgentInactivityTimeoutError,
    handle: ManagedProcess,
    recorder: _TerminationRecorder,
) -> None:
    exited_returncode = observed_returncode(handle)
    if (
        termination_issuer_for_fire_reason(exc.reason) is TerminationIssuer.POST_EXIT_WATCHDOG
        and exited_returncode is not None
        and not return_code_may_be_a_signal(exited_returncode)
    ):
        converted.returncode = exited_returncode
    publish_attempt_facts(converted, recorder)


def _observe_attempt_event(reader: object, activity_signal: AgentActivitySignal) -> None:
    recorder = _termination_recorder_of(reader)
    if recorder is None:
        return
    tool_name: str | None = None
    if activity_signal.kind == AgentActivityKind.TOOL_USE:
        tool_call = _extract_tool_call_from_activity_signal(activity_signal.raw)
        tool_name = tool_call[0] if tool_call is not None else None
    recorder.observe_event(str(activity_signal.kind), tool_name)


def _agent_command_name(config: AgentConfig) -> str:
    return shlex.split(config.cmd)[0]


def _deliver_prompt_on_stdin(handle: ManagedProcess, prompt: str) -> None:
    stream = handle.stdin
    if stream is None:
        return
    text_stream = cast("TextIO", stream)
    try:
        write_fd = os.dup(text_stream.fileno())
    except (AttributeError, OSError, ValueError):
        _write_prompt_to_stream(text_stream, prompt)
        return
    payload = prompt.encode(text_stream.encoding or "utf-8", text_stream.errors or "strict")
    with contextlib.suppress(OSError, ValueError):
        text_stream.close()
    threading.Thread(
        target=functools.partial(_write_prompt_to_fd, write_fd, payload),
        name="ralph-stdin-prompt",
        daemon=True,
    ).start()


def _write_prompt_to_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(fd, view)
            view = view[written:]
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _write_prompt_to_stream(stream: TextIO, prompt: str) -> None:
    try:
        stream.write(prompt)
        stream.flush()
    except (OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            stream.close()


def split_prompt_for_stdin_delivery(
    command: Sequence[str],
    config: AgentConfig,
) -> tuple[list[str], str | None]:
    if not command:
        return [], None
    if config.transport is AgentTransport.OPENCODE:
        return list(command[:-1]), command[-1]
    if config.transport is AgentTransport.CODEX:
        return [*command[:-1], "-"], command[-1]
    return list(command), None


def _effective_broken_agent_grace_seconds(watchdog: IdleWatchdog) -> float:
    configured = watchdog.no_output_at_start_seconds
    if configured is None:
        return BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    return max(BROKEN_AGENT_OUTPUT_GRACE_SECONDS, configured - 3.0)


def _effective_exit_settle_seconds(watchdog: IdleWatchdog) -> float:
    configured = watchdog.drain_window_seconds
    return min(
        max(BROKEN_AGENT_EXIT_SETTLE_SECONDS, configured),
        BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    )


def _exit_evidence_has_settled(watchdog: IdleWatchdog) -> bool:
    settled_for = watchdog.seconds_since_process_exit
    return settled_for is not None and settled_for >= _effective_exit_settle_seconds(watchdog)


def _record_broken_agent_termination(
    recorder: _TerminationRecorder | None,
    watchdog: IdleWatchdog,
    returncode: int | None,
) -> None:
    if recorder is None:
        return
    recorder.record_issuer(TerminationIssuer.SUPERVISOR_IDLE)
    recorder.record_liveness(True)
    recorder.record_observed_returncode(returncode)
    _record_watchdog_evidence(recorder, watchdog)


def check_broken_agent_timer(
    handle: ManagedProcess | ManagedPtyProcess,
    watchdog: IdleWatchdog,
    agent_name: str,
    process_teardown: ProcessTeardown | None = None,
    *,
    termination_recorder: _TerminationRecorder | None = None,
) -> None:
    if watchdog._config.profile.value == "activity_only":
        return
    grace_seconds = _effective_broken_agent_grace_seconds(watchdog)
    elapsed_seconds = watchdog.invocation_elapsed_seconds
    liveness_observed = True
    try:
        process_alive = handle.poll() is None
    except Exception:
        process_alive = True
        liveness_observed = False
    watchdog.record_process_liveness(process_alive, observed=liveness_observed)
    evidence = watchdog.circumstantial_evidence()
    if (
        elapsed_seconds > 0.0
        and not evidence.process_alive
        and _exit_evidence_has_settled(watchdog)
        and not evidence.has_meaningful_output
        and not evidence.has_session_id_captured
        and watchdog.observed_output_is_structurally_small()
    ):
        returncode = handle.poll()
        _record_broken_agent_termination(termination_recorder, watchdog, returncode)
        error = BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=elapsed_seconds,
            grace_seconds=grace_seconds,
            returncode=0 if returncode is None else int(returncode),
            stderr=read_bounded_stderr(handle),
        )
        if termination_recorder is not None:
            publish_attempt_facts(error, termination_recorder)
        raise error
    if (
        elapsed_seconds < max(BROKEN_AGENT_OUTPUT_GRACE_SECONDS, grace_seconds)
        or evidence.has_meaningful_output
    ):
        return
    if not watchdog.observed_output_is_structurally_small():
        return
    has_output = watchdog.has_any_output()
    supervisor = AttemptSupervisor.for_recorder(termination_recorder, handle)
    outcome = supervisor.act(
        BrokenAgentTimeoutObserved(has_output),
        teardown=resolve_teardown(process_teardown, teardown_subtree),
    )
    if outcome is None:
        return
    _record_watchdog_evidence(supervisor.recorder, watchdog)
    error = BrokenAgentExitError(
        agent_name,
        reason="no_llm_activity" if has_output else "no_output",
        elapsed_seconds=elapsed_seconds,
        grace_seconds=grace_seconds,
    )
    if termination_recorder is not None:
        publish_attempt_facts(error, supervisor.recorder)
    raise error


def make_line_reader(
    handle: ManagedProcess,
    ctx: ProcessReaderCtx,
    clock: Clock,
) -> ProcessLineReader:
    return ProcessLineReader(handle, ctx, clock)


def record_line_activity(
    reader: ProcessLineReader,
    watchdog: IdleWatchdog,
    queued_line: str,
) -> None:
    reader._record_line_activity(watchdog, queued_line)


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
    return reason in _RESUMABLE_FIRE_REASONS


def _raise_on_relay_health_error(reader: object, agent_name: str) -> None:
    raw_provider: object = getattr(reader, "_relay_health_error", None)
    if not callable(raw_provider):
        return
    provider = cast("Callable[[], str | None]", raw_provider)
    detail = provider()
    if isinstance(detail, str) and detail:
        raise SupervisionInfrastructureError(agent_name, detail)


def _convert_idle_stream_timeout_to_agent_error(
    agent_name: str,
    exc: _IdleStreamTimeoutError,
    parsed_output: tuple[str, ...] | list[str],
    *,
    explicit_completion_seen: bool = False,
    captured_session_id: str | None = None,
    expected_session_id: str | None = None,
) -> AgentInactivityTimeoutError:
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
            runtime_event=(
                exc.__cause__.runtime_event
                if isinstance(exc.__cause__, IdleWatchdogKilledError)
                else None
            ),
        ),
    )


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = scrub_activity_relay_controls(os.environ.copy())
    env.pop(_BROKER_ENV_VAR_NAME, None)
    if extra_env:
        env.update(extra_env)
    if extra_env and "AGENT_CLI_CREDENTIAL_STORE" in extra_env:
        for marker in _CURSOR_SSH_MARKERS:
            env.pop(marker, None)
    env.pop(_BROKER_ENV_VAR_NAME, None)
    scrub_activity_relay_controls(env)
    return env


def _extract_tool_call_from_activity_signal(
    raw: str,
) -> tuple[str, dict[str, object]] | None:
    return _extract_tool_call_from_activity_signal_impl(raw)


class ProcessLineReader:
    def __init__(
        self,
        handle: ManagedProcess,
        ctx: ProcessReaderCtx,
        clock: Clock,
        *,
        agy_cli_log: tuple[Path, int] | None = None,
        supervisor: AttemptSupervisor | None = None,
    ) -> None:
        self._handle = handle
        self._supervisor = supervisor
        self._config = ctx.config
        self._policy = ctx.policy
        self._strategy = ctx.execution_strategy or GenericExecutionStrategy()
        self._probe = ctx.liveness_probe or DefaultLivenessProbe()
        self._process_teardown = ctx.process_teardown
        self._waiting_listener = ctx.waiting_listener
        self._pre_output_listener = ctx.pre_output_listener
        self._monitor = ctx.monitor
        self._connectivity_state_provider = ctx.connectivity_state_provider
        self._is_waiting_state_provider = ctx.is_waiting_state_provider
        self._completion_is_terminal = ctx.completion_is_terminal
        self._input_prompt = ctx.input_prompt
        self._expected_session_id = ctx.expected_session_id
        self._relay_activity_sink_register = ctx.relay_activity_sink_register
        self._relay_health_error = ctx.relay_health_error
        self._remove_relay_sink: Callable[[], None] | None = None
        self._clock = clock
        self._workspace_path = ctx.workspace_path
        self._quota_error: QuotaExhaustedError | None = None
        self._stderr_lock = threading.Lock()
        self._stderr_capture = ""
        self._stderr_capture_truncated = False
        agy_cli_log_path = cast("Path | None", getattr(ctx, "agy_cli_log_path", None))
        self._agy_cli_log = (
            agy_cli_log
            if agy_cli_log is not None
            else (
                agy_cli_log_start_offset(agy_cli_log_path)
                if self._config.transport == AgentTransport.AGY
                else None
            )
        )
        self._lines_queue: BoundedLinesQueue = BoundedLinesQueue(maxlen=_MAX_PARSED_OUTPUT_LINES)
        self._lines_queue.set_eviction_sink(self._capture_evicted_line)
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_activity_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._reader_done: list[bool] = [False]
        self._cpu_baselines: dict[int, tuple[float, float]] = {}
        self._log_growth_state: dict[str, tuple[int, float]] = {}
        self._raw_overflow = get_or_create_raw_overflow_log(
            self._workspace_path or Path.cwd(),
            raw_log_unit_id_for(self._config),
            model=self._config.model,
        )
        self._last_activity_kind = "none"
        self._captured_session_id: str | None = None
        self._opencode_child_part_source = ctx.opencode_child_part_source
        self._opencode_subagent_probe: OpenCodeSubagentSessionProbe | None = None
        self._unsubscribe = get_process_manager().register_listener(self._on_process_event)

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _attempt_supervisor(self) -> AttemptSupervisor:
        stored: object = getattr(self, "_supervisor", None)
        if isinstance(stored, AttemptSupervisor):
            return stored
        supervisor = AttemptSupervisor(self._handle)
        self._supervisor = supervisor
        return supervisor

    @property
    def termination_recorder(self) -> _TerminationRecorder:
        return self._attempt_supervisor().recorder

    def _build_subagent_pid_source(self) -> ChildLivenessSubagentPidSource | None:
        registry, prefix = self._strategy_registry_and_prefix()
        if registry is None:
            return None
        return ChildLivenessSubagentPidSource(registry, prefix)

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

    def _bind_watchdog_monitors_and_sinks(
        self, watchdog: IdleWatchdog
    ) -> tuple[Token[ActivitySink | None], Token[ActivitySink | None]]:
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
        if self._relay_activity_sink_register is not None:
            self._remove_relay_sink = self._relay_activity_sink_register(_mcp_sink)
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
        oldest_secs_from_legacy = None
        try:
            monitor: ProcessMonitor | None = getattr(self, "_process_monitor", None)
            if monitor is not None:
                filtered_count: int = monitor.spawned_subagent_count()
                scoped_count = filtered_count
                scoped_active = filtered_count > 0
            else:
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

    def _capture_evicted_line(self, line: str) -> None:
        with contextlib.suppress(Exception):
            self._raw_overflow.append(line, counts_as_liveness=False)

    def _start_read_thread(self) -> threading.Thread:
        reader = threading.Thread(target=self._read_thread, daemon=True)
        reader.start()
        return reader

    def _start_stderr_thread(self) -> threading.Thread | None:
        stderr_pipe = cast("IO[str] | None", getattr(self._handle, "stderr", None))
        if stderr_pipe is None:
            return None
        reader = threading.Thread(target=self._read_stderr_thread, args=(stderr_pipe,), daemon=True)
        reader.start()
        return reader

    def _append_stderr(self, text: str) -> None:
        with self._stderr_lock:
            if self._stderr_capture_truncated:
                return
            available = _MAX_STDERR_CAPTURE_BYTES - len(self._stderr_capture)
            if available <= 0:
                self._stderr_capture += _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
                self._stderr_capture_truncated = True
                return
            if len(text) > available:
                self._stderr_capture += text[:available]
                self._stderr_capture += _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
                self._stderr_capture_truncated = True
                return
            self._stderr_capture += text

    def _publish_bounded_stderr(self) -> None:
        with self._stderr_lock:
            captured_stderr = self._stderr_capture
        object.__setattr__(self._handle, "_ralph_bounded_stderr", captured_stderr)

    def _read_stderr_thread(self, stderr_pipe: IO[str]) -> None:
        try:
            for line in stderr_pipe:
                self._append_stderr(line)
                evidence = provider_quota_line_evidence(line)
                if evidence is not None:
                    self._terminate_for_provider_quota(evidence.text)
                    break
        except (UnicodeDecodeError, ValueError):
            return
        except Exception:
            logger.warning(
                "stderr reader for {cmd} stopped early: {err}",
                cmd=_agent_command_name(self._config),
                err=traceback.format_exc(limit=1).strip(),
            )
        finally:
            self._publish_bounded_stderr()

    def _finish_terminal_completion(self) -> bool:
        _raise_on_relay_health_error(self, _agent_command_name(self._config))
        if self._completion_is_terminal is None:
            return False
        with contextlib.suppress(Exception):
            if self._completion_is_terminal():
                self._terminate_completed_process()
                return True
        return False

    def _poll_opencode_subagent_probe(self) -> None:
        if self._opencode_subagent_probe is not None:
            self._opencode_subagent_probe.poll()

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
                evidence = provider_quota_line_evidence(line)
                if evidence is not None:
                    self._terminate_for_provider_quota(evidence.text)
                    break
                session_id = extract_transport_session_id_from_line(line)
                if session_id is not None:
                    self._captured_session_id = session_id
                    watchdog: IdleWatchdog | None = getattr(self, "_watchdog", None)
                    if watchdog is not None:
                        watchdog.record_session_id_capture(session_id)
        except UnicodeDecodeError:
            logger.warning(
                "output reader for {cmd} stopped on an undecodable byte; "
                "the transcript is truncated here: {err}",
                cmd=_agent_command_name(self._config),
                err=traceback.format_exc(limit=1).strip(),
            )
        except ValueError:
            logger.debug(
                "output reader for {cmd} stopped at stream close",
                cmd=_agent_command_name(self._config),
            )
        except Exception:
            logger.warning(
                "output reader for {cmd} stopped early: {err}",
                cmd=_agent_command_name(self._config),
                err=traceback.format_exc(limit=1).strip(),
            )
        finally:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()

    def _announce_pre_output_progress(self) -> None:
        if self._pre_output_listener is not None:
            with contextlib.suppress(Exception):
                self._pre_output_listener()

    def _terminate_for_quota(self, detail: str) -> None:
        if not is_quota_kill_evidence(detail):
            return
        self._terminate_for_provider_quota(detail)

    def _terminate_for_provider_quota(self, detail: str) -> None:
        if not BROKEN_AGENT_OUTPUT_GRACE_SECONDS:
            return
        outcome = self._attempt_supervisor().act(
            ProviderErrorObserved(detail),
            teardown=resolve_teardown(self._process_teardown, teardown_subtree),
        )
        if outcome is None:
            return
        returncode = outcome.returncode
        self._quota_error = QuotaExhaustedError(
            _agent_command_name(self._config),
            detail,
            returncode=0 if returncode is None else returncode,
            issuer=TerminationIssuer.QUOTA_DETECTOR.value,
        )
        self._lines_event.set()

    def _raise_if_fresh_agy_log_has_quota(self) -> None:
        if self._agy_cli_log is None:
            return
        cli_log_path, start_offset = self._agy_cli_log
        tail = agy_fresh_cli_log_tail(cli_log_path, start_offset)
        if provider_quota_log_detail_matches(tail):
            self._terminate_for_quota(tail)

    def _raise_if_quota_exhausted(self, watchdog: IdleWatchdog | None = None) -> None:
        self._raise_if_fresh_agy_log_has_quota()
        if self._quota_error is not None:
            if watchdog is not None:
                _record_watchdog_evidence(self.termination_recorder, watchdog)
            publish_attempt_facts(self._quota_error, self.termination_recorder)
            raise self._quota_error

    def _terminate_completed_process(self) -> None:
        if self._completion_is_terminal is None:
            return
        self._attempt_supervisor().act(
            CompletionObserved(),
            teardown=resolve_teardown(self._process_teardown, teardown_subtree),
        )

    def _classify_quiet(self) -> AgentExecutionState:
        try:
            return self._strategy.classify_quiet(self._handle, self._probe)
        except Exception:
            if self._handle.poll() is not None:
                logger.opt(exception=True).debug(
                    "idle watchdog: classify_quiet raised after exit; defaulting to ACTIVE"
                )
                return AgentExecutionState.ACTIVE
            logger.opt(exception=True).debug(
                "idle watchdog: classify_quiet raised; defaulting to WAITING_ON_CHILD"
            )
            return AgentExecutionState.WAITING_ON_CHILD

    def _check_fire(
        self, watchdog: IdleWatchdog, verdict: WatchdogVerdict
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        raw_config: object = getattr(self, "_config", None)
        if isinstance(raw_config, AgentConfig):
            _raise_on_relay_health_error(self, _agent_command_name(raw_config))
        if verdict != WatchdogVerdict.FIRE:
            return None
        assert (
            self._policy.idle_timeout_seconds is not None
            or self._policy.max_session_seconds is not None
        )
        fire_reason = watchdog.last_fire_reason
        assert fire_reason is not None
        fire_reason = _reclassify_supervisor_idle_fire(
            fire_reason,
            self._handle,
            self.termination_recorder,
            completion_gated=self._completion_is_terminal is not None,
        )
        timeout_val = (
            self._policy.max_session_seconds
            if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
            else self._policy.no_progress_quiet_seconds
            if fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        captured_session_id = self._captured_session_id
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
        outcome = self._attempt_supervisor().act(
            SilenceObserved(fire_reason),
            teardown=resolve_teardown(self._process_teardown, teardown_subtree),
        )
        if outcome is None:
            return None
        _record_watchdog_evidence(self.termination_recorder, watchdog)
        hs_event = self._last_hard_stop[0]
        hard_stop_diag = hs_event.diagnostic if hs_event is not None else None
        now = self._clock.monotonic()
        evidence_block = {
            "evidence_summary": watchdog.last_evidence_summary(now).to_dict_list(),
        }
        merged_diag: dict[str, object] = dict(evidence_block)
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
        if hard_stop_diag is not None:
            for key, value in hard_stop_diag.items():
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
            diagnostic=cast(
                "dict[str, str | int | float | bool | list[object]] | None",
                merged_diag,
            ),
        )
        wrapper.__cause__ = typed_exc
        return pending, wrapper

    def _capture_pending(self, pending_lines: list[str]) -> list[str]:
        for line in pending_lines:
            with contextlib.suppress(Exception):
                self._raw_overflow.append(line)
        return pending_lines

    def _raise_timeout_result(
        self, result: tuple[list[str], _IdleStreamTimeoutError]
    ) -> Iterator[str]:
        pending_lines, exc = result
        yield from self._capture_pending(pending_lines)
        publish_attempt_facts(exc, self.termination_recorder)
        raise exc

    def _teardown_read_lines(
        self,
        watchdog: IdleWatchdog,
        sink_token: Token[Callable[[str], None] | None],
        subagent_token: Token[Callable[[str], None] | None],
        reader: threading.Thread,
        stderr_reader: threading.Thread | None,
    ) -> None:
        with contextlib.suppress(RuntimeError):
            reader.join(timeout=max(self._policy.drain_window_seconds, 0.5))
        if stderr_reader is not None:
            with contextlib.suppress(RuntimeError):
                stderr_reader.join(timeout=max(self._policy.drain_window_seconds, 0.5))
        self._publish_bounded_stderr()
        self._capture_abandoned_lines()
        _record_watchdog_evidence(self.termination_recorder, watchdog)
        watchdog.record_invocation_end()
        if self._remove_relay_sink is not None:
            self._remove_relay_sink()
            self._remove_relay_sink = None
        reset_active_sink(sink_token)
        reset_subagent_sink(subagent_token)
        if self._monitor is not None:
            self._monitor.set_on_event(None)
        if self._opencode_subagent_probe is not None:
            self._opencode_subagent_probe.close()
            self._opencode_subagent_probe = None
        self._raw_overflow.close()
        self._unsubscribe()

    def _capture_abandoned_lines(self) -> None:
        with self._lines_lock:
            pending = list(self._lines_queue)
            self._lines_queue.clear()
        for line in pending:
            with contextlib.suppress(Exception):
                self._raw_overflow.append(line)

    def _record_line_activity(self, watchdog: IdleWatchdog, queued_line: str) -> None:
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is None:
            self._last_activity_meaningful[0] = False
            return
        activity_signal = with_prompt_echo_flag(activity_signal, queued_line, self._input_prompt)
        if (
            activity_signal.kind != AgentActivityKind.LIFECYCLE
            and not activity_signal.is_harness_echo
        ):
            watchdog.record_any_output(byte_size=len(queued_line.encode("utf-8")))
        self._last_activity_kind = str(activity_signal.kind)
        _observe_attempt_event(self, activity_signal)
        self._last_activity_meaningful[0] = (
            activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
            and not activity_signal.is_harness_echo
        )
        if activity_signal.is_harness_echo:
            watchdog.record_prompt_echo(queued_line)
            return
        if activity_signal.error_message is not None:
            watchdog.record_error_activity(activity_signal.error_message)
        if activity_signal.kind == AgentActivityKind.ERROR_LINE:
            watchdog.record_error_activity(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.PROGRESS_REPORT:
            watchdog.record_progress_report(activity_signal.raw)
        elif activity_signal.kind == AgentActivityKind.LIFECYCLE:
            watchdog.record_lifecycle_activity()
        elif activity_signal.kind == AgentActivityKind.TOOL_USE:
            tool_call = _extract_tool_call_from_activity_signal(activity_signal.raw)
            if tool_call is not None:
                tool_name, tool_args = tool_call
                watchdog.record_tool_call_activity(tool_name, tool_args)
            watchdog.record_tool_use_activity()
        elif activity_signal.kind == AgentActivityKind.TOOL_RESULT:
            watchdog.record_tool_result_activity()
        else:
            watchdog.record_activity()

    def _evaluate_idle_fire(
        self, watchdog: IdleWatchdog
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        return self._check_fire(watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet))

    def _run_drain_window(
        self, watchdog: IdleWatchdog, drain_deadline: float | None
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        while True:
            self._poll_opencode_subagent_probe()
            result = self._evaluate_idle_fire(watchdog)
            if result is not None:
                return result
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

    def _finish_reader_done(
        self, watchdog: IdleWatchdog
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        _raise_on_relay_health_error(self, _agent_command_name(self._config))
        self._strategy.observe_stream_end()
        if self._finish_terminal_completion():
            return None
        drain_deadline = (
            None
            if self._policy.profile.value == "activity_only"
            else (
                self._clock.monotonic()
                + (self._policy.drain_window_seconds or 0)
                + (
                    self._policy.idle_poll_interval_seconds
                    if self._policy.drain_window_seconds
                    and self._policy.idle_timeout_seconds is not None
                    else 0
                )
            )
        )
        return self._run_drain_window(watchdog, drain_deadline)

    def _finish_parent_exit_with_open_stream(self, watchdog: IdleWatchdog) -> bool:
        returncode = observed_returncode(self._handle)
        if returncode is None:
            return False
        post_exit = PostExitWatchdog(self._policy, self._clock)

        def classify_stream_state() -> AgentExecutionState:
            fire = self._evaluate_idle_fire(watchdog)
            if fire is not None:
                pending, wrapper = fire
                self._capture_pending(pending)
                publish_attempt_facts(wrapper, self.termination_recorder)
                raise wrapper
            with self._lines_lock:
                reader_done = self._reader_done[0]
            return (
                AgentExecutionState.RESUMABLE_CONTINUE
                if reader_done
                else AgentExecutionState.WAITING_ON_CHILD
            )

        verdict = post_exit.wait_descendant_quiesce(classify_stream_state)
        if verdict != PostExitVerdict.FIRE_DESCENDANT_HANG:
            return False
        self._attempt_supervisor().act(
            PostExitHangObserved(verdict),
            teardown=resolve_teardown(self._process_teardown, teardown_subtree),
        )
        return True

    def _build_opencode_subagent_probe(
        self, watchdog: IdleWatchdog
    ) -> OpenCodeSubagentSessionProbe | None:
        strategy = self._strategy
        if not isinstance(strategy, OpenCodeExecutionStrategy):
            return None
        source = self._opencode_child_part_source
        if source is None:
            try:
                db_path = default_opencode_db_path()
            except (OSError, RuntimeError):
                logger.debug("opencode subagent probe: store path unresolved (suppressed)")
                return None
            if db_path is None:
                return None
            source = SqliteOpenCodeChildPartSource(db_path, monotonic=self._clock.monotonic)

        def _parent_session_id() -> str | None:
            return self._captured_session_id or self._expected_session_id

        def _subagent_sink(summary: str) -> None:
            watchdog.record_subagent_work(description=summary)
            watchdog.record_activity()

        return OpenCodeSubagentSessionProbe(
            source=source,
            parent_session_id=_parent_session_id,
            subagent_sink=_subagent_sink,
            child_progress_sink=strategy.record_native_child_progress,
            monotonic=self._clock.monotonic,
        )

    def _build_process_monitor(self) -> ProcessMonitor | None:
        subagent_pid_source = self._build_subagent_pid_source()
        registry, scope_prefix = self._strategy_registry_and_prefix()
        return _make_process_monitor(
            self._handle,
            self._config,
            self._policy,
            subagent_pid_source,
            registry=registry,
            scope_prefix=scope_prefix,
        )

    def read_lines(self) -> Iterator[str]:
        process_monitor = self._build_process_monitor()
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
        if self._expected_session_id is not None:
            watchdog.record_session_id_capture(self._expected_session_id)
        self._watchdog = watchdog
        if self._captured_session_id is not None:
            watchdog.record_session_id_capture(self._captured_session_id)

        sink_token, subagent_token = self._bind_watchdog_monitors_and_sinks(watchdog)
        self._opencode_subagent_probe = self._build_opencode_subagent_probe(watchdog)
        stderr_reader = self._start_stderr_thread()
        reader = self._start_read_thread()
        try:
            while True:
                _raise_on_relay_health_error(self, _agent_command_name(self._config))
                self._lines_event.clear()
                self._raise_if_quota_exhausted(watchdog)
                self._poll_opencode_subagent_probe()
                queued_line: str | None = None
                is_done = False
                with self._lines_lock:
                    if self._lines_queue:
                        queued_line = _pop_queue_line(self._lines_queue)
                    elif self._reader_done[0]:
                        is_done = True

                if queued_line is not None:
                    self._record_line_activity(watchdog, queued_line)
                    self._strategy.observe_line(queued_line)
                    self._raw_overflow.append(queued_line)
                    yield queued_line
                    if self._finish_terminal_completion():
                        break
                    result = self._check_fire(
                        watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                    )
                    if result is not None:
                        yield from self._raise_timeout_result(result)
                    continue

                if is_done:
                    result = self._finish_reader_done(watchdog)
                    if result is not None:
                        yield from self._raise_timeout_result(result)
                    break

                if self._finish_terminal_completion() or self._finish_parent_exit_with_open_stream(
                    watchdog
                ):
                    break

                result = self._check_fire(
                    watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                )
                if result is not None:
                    yield from self._raise_timeout_result(result)

                check_broken_agent_timer(
                    self._handle,
                    watchdog,
                    _agent_command_name(self._config),
                    self._process_teardown,
                    termination_recorder=self.termination_recorder,
                )
                self._clock.wait_for_event(
                    self._lines_event, self._policy.idle_poll_interval_seconds
                )

        finally:
            self._teardown_read_lines(
                watchdog,
                sink_token,
                subagent_token,
                reader,
                stderr_reader,
            )


def completion_evidence_gates_reader(ctx: AgentRunCtx) -> bool:
    return bool(ctx.requires_completion_evidence) or ctx.policy.profile.value == "activity_only"


def _append_conflict_termination_diagnostic(
    diagnostic: dict[str, object],
    watchdog: IdleWatchdog,
    fire_reason: WatchdogFireReason,
    now: float,
) -> None:
    if fire_reason not in {
        WatchdogFireReason.CONFLICT_INACTIVITY,
        WatchdogFireReason.OPERATOR_CAP_REACHED,
    }:
        return
    snapshot = watchdog.diagnostic_snapshot(now=now)
    diagnostic["last_activity_kind"] = snapshot.get("last_activity_kind")
    diagnostic["last_activity_at"] = snapshot.get("last_activity_at")
    diagnostic["invocation_elapsed_seconds"] = snapshot.get("invocation_elapsed_seconds")


def _spawn_process_with_agy_log_offset(
    argv: list[str],
    spawn_options: SpawnOptions,
    ctx: AgentRunCtx,
) -> tuple[ManagedProcess, tuple[Path, int] | None]:
    agy_cli_log = agy_cli_log_start_offset() if ctx.config.transport == AgentTransport.AGY else None
    return get_process_manager().spawn(argv, spawn_options), agy_cli_log


def _agent_process_label(ctx: AgentRunCtx) -> str:
    scope = (ctx.extra_env or {}).get(str(AGENT_LABEL_SCOPE_ENV))
    if scope:
        return f"invoke:{scope}:{_agent_command_name(ctx.config)}"
    return f"invoke:{_agent_command_name(ctx.config)}"


def _terminate_hung_process_exit(
    handle: ManagedProcess,
    ctx: AgentRunCtx,
    recorder: _TerminationRecorder,
    verdict: PostExitVerdict,
) -> None:
    if verdict != PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
        return
    supervisor = AttemptSupervisor.for_recorder(recorder, handle)
    outcome = supervisor.act(
        PostExitHangObserved(verdict),
        teardown=resolve_teardown(ctx.process_teardown, teardown_subtree),
    )
    if outcome is None:
        return
    raise _IdleStreamTimeoutError(
        ctx.policy.process_exit_wait_seconds,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    )


def _run_subprocess_and_read_lines(
    cmd: list[str],
    ctx: AgentRunCtx,
) -> Generator[str, None, AttemptEnded]:
    clock: Clock = ctx.clock or SystemClock()
    argv, stdin_prompt = split_prompt_for_stdin_delivery(cmd, ctx.config)
    spawn_options = (
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            start_new_session=True,
            label=_agent_process_label(ctx),
            text=True,
        )
        if stdin_prompt is not None
        else SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            start_new_session=True,
            label=_agent_process_label(ctx),
            text=True,
        )
    )
    handle, agy_cli_log = _spawn_process_with_agy_log_offset(argv, spawn_options, ctx)
    supervisor = AttemptSupervisor(handle)
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        stdout_pipe = handle.stdout
        if stdout_pipe is None:
            msg = "Failed to capture stdout"
            supervisor.act(
                StartupFailureObserved(msg),
                teardown=resolve_teardown(ctx.process_teardown, teardown_subtree),
            )
            raise AgentInvocationError(_agent_command_name(ctx.config), -1, msg)

        completion_run_id = completion_run_id_from_extra_env(ctx.extra_env)
        broker_secret = parent_broker_secret()

        def completion_is_terminal() -> bool:
            if (
                not completion_evidence_gates_reader(ctx)
                or ctx.workspace_path is None
                or broker_secret is None
            ):
                return False
            signals = (ctx.evaluate_completion_fn or evaluate_completion)(
                ctx.workspace_path,
                required_artifact=ctx.required_artifact,
                run_id=completion_run_id,
                sentinel_secret=broker_secret,
                receipt_secret=broker_secret,
            )
            return completion_signals_terminal(signals)

        reader_ctx = ProcessReaderCtx(
            config=ctx.config,
            policy=ctx.policy,
            execution_strategy=ctx.execution_strategy,
            liveness_probe=ctx.liveness_probe,
            process_teardown=ctx.process_teardown,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=ctx.pre_output_listener,
            monitor=ctx.monitor,
            expected_session_id=ctx.expected_session_id,
            workspace_path=ctx.workspace_path,
            connectivity_state_provider=ctx.connectivity_state_provider,
            is_waiting_state_provider=ctx.is_waiting_state_provider,
            completion_is_terminal=(
                completion_is_terminal if completion_evidence_gates_reader(ctx) else None
            ),
            input_prompt=ctx.input_prompt,
            relay_activity_sink_register=ctx.relay_activity_sink_register,
            relay_health_error=ctx.relay_health_error,
        )
        reader = ProcessLineReader(
            handle,
            reader_ctx,
            clock,
            agy_cli_log=agy_cli_log,
            supervisor=supervisor,
        )
        lines_iter = reader.read_lines()
        if stdin_prompt is not None:
            _deliver_prompt_on_stdin(handle, stdin_prompt)
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

            if ctx.policy.profile.value != "activity_only":
                post_exit = PostExitWatchdog(ctx.policy, clock)
                verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
                _terminate_hung_process_exit(handle, ctx, reader.termination_recorder, verdict)
        except _IdleStreamTimeoutError as exc:
            converted = _convert_idle_stream_timeout_to_agent_error(
                _agent_command_name(ctx.config),
                exc,
                tuple(parsed_output),
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
                expected_session_id=reader_ctx.expected_session_id,
            )
            _attach_fire_attempt_facts(exc, converted, handle, reader.termination_recorder)
            raise converted from exc

        (
            evidence_summary_str,
            last_tool_call_str,
            elapsed_value,
            transcript_tail,
        ) = collect_r7_diagnostic_fields(
            reader=reader,
            clock=clock,
            parsed_output=parsed_output,
        )

        check_process_result(
            handle,
            _agent_command_name(ctx.config),
            list(parsed_output),
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=ctx.workspace_path,
                liveness_probe=probe,
                policy=ctx.policy,
                required_artifact=ctx.required_artifact,
                requires_completion_evidence=ctx.requires_completion_evidence,
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
                completion_run_id=completion_run_id,
                evaluate_completion_fn=ctx.evaluate_completion_fn,
                last_observed_tool_call=last_tool_call_str,
                last_evidence_summary=evidence_summary_str,
                elapsed_seconds=elapsed_value,
                has_meaningful_output=_has_meaningful_output_from_reader(reader),
                input_prompt=ctx.input_prompt,
                transcript_tail=transcript_tail,
                sentinel_secret=parent_broker_secret(),
                receipt_secret=parent_broker_secret(),
                activity_only_supervision=ctx.policy.profile.value == "activity_only",
            ),
            _clock=clock,
            termination_recorder=supervisor.recorder,
        )
        return AttemptEnded(facts=supervisor.recorder.facts())


def _read_lines_from_process(
    handle: ManagedProcess,
    *,
    ctx: ProcessReaderCtx,
    _clock: Clock | None = None,
) -> Iterator[str]:
    clock: Clock = _clock or SystemClock()
    return ProcessLineReader(handle, ctx, clock).read_lines()


def _has_meaningful_output_from_reader(reader: object) -> bool | None:
    watchdog: IdleWatchdog | None = getattr(reader, "_watchdog", None)
    if watchdog is None:
        return None
    try:
        return bool(watchdog.has_meaningful_output())
    except Exception:
        return None


def collect_r7_diagnostic_fields(
    *,
    reader: object,
    clock: Clock,
    parsed_output: deque[str],
) -> tuple[str | None, str | None, float | None, tuple[str, ...]]:
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
        last_tool_call_obj: object = watchdog_for_diag.diagnostic_snapshot(now=diag_now).get(
            "current_subagent_tool_call"
        )
        last_tool_call_str: str | None = (
            str(last_tool_call_obj) if last_tool_call_obj is not None else None
        )
    except Exception:
        last_tool_call_str = None
    try:
        elapsed_value: float | None = round(watchdog_for_diag.invocation_elapsed_seconds, 1)
    except Exception:
        elapsed_value = None
    transcript_tail: tuple[str, ...] = tuple(list(parsed_output)[-10:])
    return (
        evidence_summary_str,
        last_tool_call_str,
        elapsed_value,
        transcript_tail,
    )
