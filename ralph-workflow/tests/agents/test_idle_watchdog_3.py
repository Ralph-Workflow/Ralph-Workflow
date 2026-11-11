from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger as loguru_logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    ChannelEvidenceSummary,
    ChannelName,
    CorroborationSnapshot,
    EvidenceSummary,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
    WorkspaceChangeKind,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeClassifier,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_IDLE_TIMEOUT = 0.1
_DRAIN_WINDOW = 0.0
_MAX_WAITING = 10.0
_ACTIVITY_TTL = 30.0


def _make_watchdog(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = _ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
    silent_subagent_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    kwargs: dict[str, Any] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": suspect,
        "max_waiting_on_child_no_progress_seconds": no_progress_ceiling,
        "stuck_job_sub_ceiling_seconds": None,
        "os_descendant_only_ceiling_seconds": None,
        "silent_subagent_seconds": silent_subagent_seconds,
    }
    if activity_ttl is not None:
        kwargs["activity_evidence_ttl_seconds"] = activity_ttl
    config = TimeoutPolicy(**kwargs)
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _active_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.ACTIVE


def _waiting_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.WAITING_ON_CHILD


def test_no_false_kill_on_mcp_tool_activity() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_mcp_tool_call()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via mcp_tool channel), got {verdict}"
    )
    assert wd.channel_evidence_active(clock.monotonic()) is True

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE (channel stale past TTL), got {verdict}"
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_no_false_kill_on_subagent_work() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_no_false_kill_on_workspace_changes() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_dead_subagent_detected_within_idle_window() -> None:
    wd, clock = _make_watchdog()
    wd.record_activity()
    wd.record_subagent_work()
    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_truly_idle_still_fires_on_time() -> None:
    wd, clock = _make_watchdog()
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_session_ceiling_unaffected_by_activity() -> None:
    wd, clock = _make_watchdog(idle_timeout=1.0, max_session=10.0, activity_ttl=30.0)
    for _t in range(0, 12):
        wd.record_mcp_tool_call()
        clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_max_waiting_ceiling_unaffected_by_activity() -> None:
    wd, clock = _make_watchdog(idle_timeout=0.1, max_waiting=2.0, activity_ttl=0.0)
    wd.record_activity()
    clock.advance(0.1)
    for _ in range(30):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_activity_evidence_ttl_zero_disables_feature() -> None:
    wd, clock = _make_watchdog(activity_ttl=0.0)
    wd.record_activity()
    clock.advance(0.2)
    wd.record_mcp_tool_call()
    clock.advance(0.1)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_evidence_summary_in_hard_stop_diagnostic() -> None:
    events: list[WaitingStatusEvent] = []
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=2.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=0.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock, listener=events.append)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    verdict = WatchdogVerdict.CONTINUE
    for _ in range(25):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert hard_stops, "no HARD_STOP event captured"
    diag = hard_stops[0].diagnostic
    assert "evidence_summary" in diag
    assert isinstance(diag["evidence_summary"], list)
    assert len(diag["evidence_summary"]) == 5
    channel_names = {entry["channel"] for entry in diag["evidence_summary"]}
    assert channel_names == {
        "stdout",
        "mcp_tool",
        "subagent_output",
        "subagent_liveness",
        "workspace",
    }


def test_session_ceiling_fire_carries_evidence_summary() -> None:
    wd, clock = _make_watchdog(max_session=5.0, start=0.0)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    clock.advance(6.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED

    fire_records = [
        m for m in captured if "FIRE reason=session_ceiling_exceeded" in str(m.record["message"])
    ]
    assert fire_records, "no SESSION_CEILING_EXCEEDED fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "session_ceiling_exceeded"


def test_repeated_error_loop_fire_carries_evidence_summary() -> None:
    wd, clock = _make_watchdog(idle_timeout=300.0, max_waiting=600.0, start=0.0)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        wd.record_error_activity(msg)
        clock.advance(34.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd.record_error_activity(msg)
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP

    fire_records = [
        m for m in captured if "FIRE reason=repeated_error_loop" in str(m.record["message"])
    ]
    assert fire_records, "no REPEATED_ERROR_LOOP fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "repeated_error_loop"


def test_stalled_after_tool_result_fire_carries_evidence_summary() -> None:
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=100.0,
        post_tool_result_progression_seconds=0.1,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock)
    wd.record_activity()
    wd.record_tool_result_activity()
    clock.advance(1.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT

    fire_records = [
        m for m in captured if "FIRE reason=stalled_after_tool_result" in str(m.record["message"])
    ]
    assert fire_records, "no STALLED_AFTER_TOOL_RESULT fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "stalled_after_tool_result"


def test_workspace_monitor_records_last_event_at(tmp_path: Path) -> None:
    clock_value = [0.0]

    def fake_now() -> float:
        return clock_value[0]

    monitor = WorkspaceMonitor(tmp_path, now=fake_now)
    assert monitor.last_event_at is None
    assert monitor.event_count == 0
    monitor.record_event("/tmp/file_a.py")
    assert monitor.last_event_at == 0.0
    assert monitor.event_count == 1
    clock_value[0] = 5.0
    monitor.record_event("/tmp/file_b.py")
    assert monitor.last_event_at == 5.0
    assert monitor.event_count == 2

    monitor.reset_last_event_at()
    assert monitor.last_event_at is None
    assert monitor.event_count == 0


def _default_classifier() -> WorkspaceChangeClassifier:
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


def test_workspace_monitor_to_watchdog_integration(tmp_path: Path) -> None:
    wd, clock = _make_watchdog()
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    assert wd.last_workspace_event_at is None
    assert wd.workspace_event_count == 0
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    clock.advance(100.5)
    monitor.record_event("/tmp/foo.py")
    assert wd.last_workspace_event_at == 100.5
    assert wd.workspace_event_count == 1
    assert wd.workspace_kind_counts == {"source": 1}


def test_workspace_monitor_to_watchdog_defers_verdict(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    clock.advance(5.0)
    monitor.record_event("/tmp/foo.py")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via workspace channel), got {verdict}"
    )
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_workspace_monitor_smart_filter_source_defers_log_does_not(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))

    clock.advance(5.0)
    monitor.record_event("/tmp/agent.log")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, (
        "expected FIRE for log-only change (dropped by default classifier)"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert wd.workspace_kind_counts.get("log", 0) == 0, (
        "log events must not increment the per-kind counter when dropped"
    )

    wd2, clock2 = _make_watchdog(activity_ttl=1000.0)
    monitor2 = WorkspaceMonitor(
        tmp_path,
        now=clock2.monotonic,
        classifier=_default_classifier(),
    )
    monitor2.set_on_event(lambda kind, weight: wd2.record_workspace_event(kind=kind, weight=weight))
    clock2.advance(5.0)
    monitor2.record_event("/tmp/foo.py")
    verdict2 = wd2.evaluate(classify_quiet=_active_classifier())
    assert verdict2 == WatchdogVerdict.CONTINUE, (
        "expected CONTINUE for source change (counts as activity by default)"
    )
    assert wd2.workspace_kind_counts == {"source": 1}


def test_last_evidence_summary_channel_order() -> None:
    wd, _ = _make_watchdog()
    summary = wd.last_evidence_summary(0.0)
    assert isinstance(summary, EvidenceSummary)
    assert len(summary.channels) == 5
    assert [s.channel_name for s in summary.channels] == [
        ChannelName.STDOUT,
        ChannelName.MCP_TOOL,
        ChannelName.SUBAGENT_OUTPUT,
        ChannelName.SUBAGENT_LIVENESS,
        ChannelName.WORKSPACE,
    ]
    for entry in summary.channels:
        assert isinstance(entry, ChannelEvidenceSummary)


def test_recorders_do_not_mutate_last_activity() -> None:
    wd, clock = _make_watchdog()
    baseline = wd.last_activity
    clock.advance(1.0)
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    assert wd.last_activity == baseline
    wd.record_activity()
    assert wd.last_activity != baseline
    assert wd.last_activity > baseline


def test_corroboration_snapshot_carries_per_channel_fields() -> None:
    s = CorroborationSnapshot()
    assert s.mcp_tool_call_count is None
    assert s.subagent_progress_count is None
    assert s.last_mcp_tool_call_at is None
    assert s.last_subagent_progress_at is None
    assert s.last_workspace_event_at is None
    assert s.current_run_idle_elapsed_seconds is None
    assert s.workspace_event_count is None
    assert s.alive_by is None


def test_activity_evidence_ttl_none_is_allowed() -> None:
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        activity_evidence_ttl_seconds=None,
    )
    assert config.activity_evidence_ttl_seconds is None


def test_recorders_accept_custom_now_timestamp() -> None:
    wd, _ = _make_watchdog()
    wd.record_mcp_tool_call(now=42.0)
    wd.record_subagent_work(now=43.0)
    wd.record_workspace_event(now=44.0)
    assert wd.last_mcp_tool_call_at == 42.0
    assert wd.last_subagent_progress_at == 43.0
    assert wd.last_workspace_event_at == 44.0
    assert wd.mcp_tool_call_count == 1
    assert wd.subagent_progress_count == 1
    assert wd.workspace_event_count == 1


def test_handle_evidence_deferral_returns_continue() -> None:
    wd, clock = _make_watchdog()
    wd.record_mcp_tool_call()
    verdict = wd.handle_evidence_deferral(clock.monotonic(), 0.5)
    assert verdict == WatchdogVerdict.CONTINUE


def test_handle_evidence_deferral_debug_log_names_channel_age() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_subagent_work()
    clock.advance(50.0)
    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd.handle_evidence_deferral(clock.monotonic(), 50.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    assert "channel=subagent" in line, f"channel label must be 'subagent', got: {line}"
    assert "age=50.0s" in line, f"age= field must be 50.0s, got: {line}"
    assert "idle_elapsed=50.0s" in line, f"idle_elapsed= field must be 50.0s, got: {line}"


def test_handle_evidence_deferral_debug_log_age_differs_from_idle_elapsed() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(60.0)
    wd.record_mcp_tool_call()
    clock.advance(55.0)

    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd.handle_evidence_deferral(clock.monotonic(), 115.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    assert "channel=mcp_tool" in line, f"channel label must be 'mcp_tool', got: {line}"
    assert "age=55.0s" in line, (
        f"age= field must reflect the mcp_tool channel age (55.0s), not "
        f"the stdout idle elapsed (115.0s); got: {line}"
    )
    assert "idle_elapsed=115.0s" in line, (
        f"idle_elapsed= field must reflect the stdout baseline age (115.0s), got: {line}"
    )
    assert "age=55.0s" in line and "idle_elapsed=115.0s" in line, (
        f"age= must differ from idle_elapsed= when the channel age is "
        f"fresher than the stdout baseline; got: {line}"
    )


def test_build_evidence_summary_diag_returns_freshest_age() -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(60.0)
    wd.record_mcp_tool_call()
    clock.advance(55.0)

    diag, freshest_age = wd.build_evidence_summary_diag(clock.monotonic())
    assert isinstance(diag, dict)
    assert "evidence_summary" in diag
    assert diag["active_channel"] == "mcp_tool"
    assert freshest_age == 55.0, (
        f"freshest_age must be the mcp_tool channel age (55.0s), got {freshest_age}"
    )

    wd2, clock2 = _make_watchdog(activity_ttl=10.0)
    wd2.record_activity()
    clock2.advance(20.0)
    diag2, freshest_age2 = wd2.build_evidence_summary_diag(clock2.monotonic())
    assert diag2["active_channel"] == "none"
    assert freshest_age2 is None
