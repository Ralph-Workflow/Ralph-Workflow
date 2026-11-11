from __future__ import annotations

import json
import re
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WaitingStatusListener,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke import (
    IdleStreamTimeoutError,
    ProcessReaderCtx,
    read_lines_from_process,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.child_liveness import ChildLivenessRegistry, MutableRecord
from ralph.process.liveness import FakeLivenessProbe
from tests.agents._invoke_timeout_integration_helper__raisingstrategy import _RaisingStrategy
from tests.agents._invoke_timeout_integration_helper__waitingstrategy import _WaitingStrategy


class _FakeManagedHandle:
    def __init__(
        self,
        stdout_lines: object,
        *,
        descendant_count: int = 0,
        descendant_oldest_seconds: float = 0.0,
        on_terminate: Callable[[], None] | None = None,
    ) -> None:
        self._stdout = stdout_lines
        self._stderr = None
        self._returncode: int | None = None
        self._terminated = False
        self._descendant_count = descendant_count
        self._descendant_oldest_seconds = descendant_oldest_seconds
        self._on_terminate = on_terminate
        self._pid = 999_999

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def stdout(self) -> object | None:
        return self._stdout

    @property
    def stderr(self) -> object | None:
        return self._stderr

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self._terminated = True
        if self._on_terminate is not None:
            self._on_terminate()

    def has_live_descendants(self) -> bool:
        return self._descendant_count > 0

    def descendant_snapshot(self) -> tuple[int, float]:
        return (self._descendant_count, self._descendant_oldest_seconds)

    def __enter__(self) -> _FakeManagedHandle:
        return self

    def __exit__(self, *args: object) -> None:
        pass


_MAX_SESSION_SECONDS = 5.0
_CLOCK_ADVANCE_PER_LINE = 0.5
_TOTAL_LINES_IN_STDOUT = 10


def _read_lines(
    handle: _FakeManagedHandle,
    *,
    policy: TimeoutPolicy,
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None,
    liveness_probe: FakeLivenessProbe | None = None,
    waiting_listener: WaitingStatusListener | None = None,
    _clock: FakeClock | None = None,
) -> Iterator[str]:
    return read_lines_from_process(
        handle,
        ctx=ProcessReaderCtx(
            config=AgentConfig(
                cmd="opencode",
                transport=AgentTransport.OPENCODE,
            ),
            policy=policy,
            execution_strategy=execution_strategy,
            liveness_probe=liveness_probe,
            waiting_listener=waiting_listener,
        ),
        _clock=_clock,
    )


def test_opencode_regression_verified_completion_stops_live_stdout_stream() -> None:
    blocked = threading.Event()

    def live_stdout() -> Iterator[str]:
        yield '{"type":"text","part":{"text":"complete"}}\n'
        assert blocked.wait(timeout=2.0)

    class _CompletionHandle(_FakeManagedHandle):
        def terminate(self, grace_period_s: float | None = None) -> None:
            super().terminate(grace_period_s)
            blocked.set()

    handle = _CompletionHandle(live_stdout())
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        max_session_seconds=5.0,
        idle_poll_interval_seconds=0.01,
        drain_window_seconds=0.0,
    )
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
        policy=policy,
        execution_strategy=OpenCodeExecutionStrategy(),
        completion_is_terminal=lambda: True,
    )

    lines = list(read_lines_from_process(handle, ctx=ctx, _clock=FakeClock()))

    assert lines == ['{"type":"text","part":{"text":"complete"}}\n']
    assert handle._terminated is True


def test_opencode_regression_verified_completion_stops_parent_after_stdout_eof() -> None:
    handle = _FakeManagedHandle(iter(['{"type":"text","part":{"text":"complete"}}\n']))
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        max_session_seconds=5.0,
        idle_poll_interval_seconds=0.01,
        drain_window_seconds=0.0,
    )
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
        policy=policy,
        execution_strategy=OpenCodeExecutionStrategy(),
        completion_is_terminal=lambda: True,
    )

    lines = list(read_lines_from_process(handle, ctx=ctx, _clock=FakeClock()))

    assert lines == ['{"type":"text","part":{"text":"complete"}}\n']
    assert handle._terminated is True


def test_session_ceiling_fires_under_continuous_output() -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=_MAX_SESSION_SECONDS,
        max_session_seconds=_MAX_SESSION_SECONDS,
        idle_poll_interval_seconds=0.01,
        drain_window_seconds=0.0,
    )
    clock = FakeClock(start=0.0)

    def _stdout_gen() -> Iterator[str]:
        for i in range(_TOTAL_LINES_IN_STDOUT):
            clock.advance(_CLOCK_ADVANCE_PER_LINE)
            yield f"line {i}\n"

    handle = _FakeManagedHandle(_stdout_gen())

    with pytest.raises(IdleStreamTimeoutError) as exc_info:
        for _ in _read_lines(handle, policy=policy, _clock=clock):
            pass

    assert exc_info.value.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    assert exc_info.value.timeout_seconds == _MAX_SESSION_SECONDS


def test_reader_fire_exception_carries_evidence_summary() -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=_MAX_SESSION_SECONDS,
        max_session_seconds=_MAX_SESSION_SECONDS,
        idle_poll_interval_seconds=0.01,
        drain_window_seconds=0.0,
    )
    clock = FakeClock(start=0.0)

    def _stdout_gen() -> Iterator[str]:
        for i in range(_TOTAL_LINES_IN_STDOUT):
            clock.advance(_CLOCK_ADVANCE_PER_LINE)
            yield f"line {i}\n"

    handle = _FakeManagedHandle(_stdout_gen())

    with pytest.raises(IdleStreamTimeoutError) as exc_info:
        for _ in _read_lines(handle, policy=policy, _clock=clock):
            pass

    assert exc_info.value.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    assert exc_info.value.timeout_seconds == _MAX_SESSION_SECONDS

    diag = exc_info.value.diagnostic
    assert diag is not None
    assert "evidence_summary" in diag
    assert isinstance(diag["evidence_summary"], list)
    assert len(diag["evidence_summary"]) == 5
    channels = {entry["channel"] for entry in diag["evidence_summary"]}
    assert channels == {
        "stdout",
        "mcp_tool",
        "subagent_output",
        "subagent_liveness",
        "workspace",
    }


def test_watchdog_fires_even_when_classify_quiet_raises() -> None:
    idle_timeout = 0.05
    max_waiting = 0.1
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.01,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        on_terminate=_reader_release.set,
    )

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=_RaisingStrategy(),
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert clock.monotonic() >= idle_timeout + max_waiting


def test_classify_quiet_exception_defers_not_fires() -> None:
    idle_timeout = 0.05
    max_waiting = 0.1
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.05,
        idle_poll_interval_seconds=0.01,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        on_terminate=_reader_release.set,
    )

    with pytest.raises(IdleStreamTimeoutError) as exc_info:
        for _ in _read_lines(
            handle,
            policy=policy,
            execution_strategy=_RaisingStrategy(),
            _clock=clock,
        ):
            pass

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert exc_info.value.reason != WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_post_yield_evaluate_uses_real_classify_quiet() -> None:
    idle_timeout = 0.5
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.5,
        idle_poll_interval_seconds=0.01,
    )
    clock = FakeClock(start=0.0)

    def _stdout_gen() -> Iterator[str]:
        clock.advance(3.0)
        yield "   \n"

    handle = _FakeManagedHandle(_stdout_gen())

    lines = list(
        _read_lines(
            handle,
            policy=policy,
            execution_strategy=_WaitingStrategy(),
            _clock=clock,
        )
    )
    assert "   \n" in lines


def test_cumulative_ceiling_fires_with_oscillating_heartbeat() -> None:
    idle_timeout = 1.0
    max_waiting = 2.0
    drain_window = 0.05
    poll_interval = 0.25

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=drain_window,
        idle_poll_interval_seconds=poll_interval,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _release = threading.Event()
    _max_heartbeats = 20

    def _oscillating_stdout() -> Iterator[str]:
        for heartbeat_num in range(_max_heartbeats):
            target_t = (heartbeat_num + 1) * (idle_timeout + 0.1)
            while clock.monotonic() < target_t:
                time.sleep(0)
            yield f"heartbeat {heartbeat_num}\n"
        _release.wait(timeout=1.0)
        yield from ()

    handle = _FakeManagedHandle(
        _oscillating_stdout(),
        on_terminate=_release.set,
    )

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                _clock=clock,
            ):
                pass
    finally:
        _release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


_MAX_PROGRESS_EVENTS = 3
_MAX_TOTAL_EVENTS = 6


def test_invoke_emits_waiting_listener_events_not_per_tick_log() -> None:
    idle_timeout = 0.05
    max_waiting = 0.2
    status_interval = 0.05

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        on_terminate=_reader_release.set,
    )
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    entered = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    progress = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]

    assert len(entered) == 1, f"Expected 1 ENTERED, got {len(entered)}"
    assert len(progress) >= 1, f"Expected >=1 PROGRESS, got {len(progress)}"
    assert len(progress) <= _MAX_PROGRESS_EVENTS, (
        f"Expected <={_MAX_PROGRESS_EVENTS} PROGRESS (throttled), got {len(progress)}"
    )
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    assert len(captured_events) <= _MAX_TOTAL_EVENTS, (
        f"Expected <={_MAX_TOTAL_EVENTS} total events, got {len(captured_events)}"
    )


def test_children_persist_hard_stop_includes_corroboration_diagnostic() -> None:
    idle_timeout = 0.05
    max_waiting = 0.15
    status_interval = 100.0

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        on_terminate=_reader_release.set,
    )
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    err_msg = str(exc_info.value)
    assert "cumulative=" in err_msg, f"Expected 'cumulative=' in: {err_msg}"
    assert "scoped_child_active=" in err_msg, f"Expected 'scoped_child_active=' in: {err_msg}"

    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    diag = hard_stops[0].diagnostic
    assert "cumulative" in diag, f"Expected 'cumulative' key in diagnostic: {diag}"
    assert "evidence" in diag, f"Expected 'evidence' key in HARD_STOP diagnostic: {diag}"


def test_no_progress_ceiling_fires_on_stale_child_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idle_timeout = 0.1
    max_waiting = 20.0
    no_progress_ceiling = 10.0
    status_interval = 100.0
    import ralph.agents.invoke._process_reader as _process_reader_module

    monkeypatch.setattr(_process_reader_module, "BROKEN_AGENT_OUTPUT_GRACE_SECONDS", 900.0)

    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=status_interval,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        process_monitor_enabled=False,
        cpu_idle_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        descendant_count=1,
        descendant_oldest_seconds=5.0,
        on_terminate=_reader_release.set,
    )
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=_WaitingStrategy(),
                waiting_listener=_listener,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    err_msg = str(exc_info.value)
    assert "cumulative=" in err_msg, f"Expected 'cumulative=' in: {err_msg}"
    match = re.search(r"cumulative=([\d.]+)s", err_msg)
    assert match is not None, f"Could not find cumulative value in: {err_msg}"
    cumulative = float(match.group(1))
    assert cumulative < max_waiting, (
        f"Expected to fire before full ceiling ({max_waiting}s), but cumulative={cumulative}s"
    )
    assert cumulative >= no_progress_ceiling, (
        f"Expected to fire at or after no-progress ceiling ({no_progress_ceiling}s), "
        f"but cumulative={cumulative}s"
    )

    hard_stops = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1, f"Expected 1 HARD_STOP, got {len(hard_stops)}"
    diag = hard_stops[0].diagnostic
    assert "effective_ceiling_label" in diag, (
        f"Expected 'effective_ceiling_label' key in HARD_STOP diagnostic: {diag}"
    )
    assert diag.get("effective_ceiling_label") == "no_progress", (
        f"Expected effective_ceiling_label='no_progress', got {diag.get('effective_ceiling_label')}"
    )
    assert "alive_by" in diag, f"Expected 'alive_by' key in diagnostic: {diag}"
    assert diag["alive_by"] == "os_descendant_only_stale_progress", (
        f"Expected alive_by='os_descendant_only_stale_progress', got {diag.get('alive_by')}"
    )


def test_stale_scoped_child_evidence_fires_no_output_deadline() -> None:
    idle_timeout = 0.1
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=20.0,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=100.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)

    _reader_release = threading.Event()

    def _blocking_stdout() -> Iterator[str]:
        _reader_release.wait(timeout=5.0)
        yield from ()

    handle = _FakeManagedHandle(
        _blocking_stdout(),
        descendant_count=1,
        descendant_oldest_seconds=5.0,
        on_terminate=_reader_release.set,
    )

    stale_registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
        now=lambda: 1000.0,
    )
    stale_registry._records["child-x"] = MutableRecord(
        child_id="child-x",
        scope_prefix="agent:test:",
        pid=12345,
        started_at=900.0,
        last_progress_at=960.0,
        last_heartbeat_at=960.0,
        last_known_phase="running",
    )

    strategy = OpenCodeExecutionStrategy(label_scope="test", registry=stale_registry)
    probe = FakeLivenessProbe(active=False)

    try:
        with pytest.raises(IdleStreamTimeoutError) as exc_info:
            for _ in _read_lines(
                handle,
                policy=policy,
                execution_strategy=strategy,
                liveness_probe=probe,
                _clock=clock,
            ):
                pass
    finally:
        _reader_release.set()

    assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_DEADLINE, (
        f"Expected NO_OUTPUT_DEADLINE (normal idle path), got {exc_info.value.reason!r}"
    )


def test_fresh_then_stale_scoped_child_evidence_fires_no_output_deadline() -> None:
    idle_timeout = 0.1
    max_waiting = 20.0
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=max_waiting,
        drain_window_seconds=0.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=100.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    registry = ChildLivenessRegistry(
        progress_ttl=0.2,
        heartbeat_ttl=0.2,
        stale_label_ttl=0.1,
        exit_reconcile=5.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope="test", registry=registry)
    strategy.observe_line(json.dumps({"type": "child_started", "child_id": "child-x"}))
    strategy.observe_line(
        json.dumps({"type": "child_progress", "child_id": "child-x", "phase": "running"})
    )
    handle = _FakeManagedHandle(iter(()), descendant_count=1, descendant_oldest_seconds=5.0)
    probe = FakeLivenessProbe(active=False)
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_invocation_start()
    watchdog.record_activity()

    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    clock.advance(idle_timeout + 0.2)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE
    assert watchdog.evaluate(classify_quiet=lambda: strategy.classify_quiet(handle, probe)) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
