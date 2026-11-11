from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
    WorkspaceChangeKind,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    _normalize_workspace_change_weights,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
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
    activity_ttl: float | None = _ACTIVITY_TTL,
    workspace_change_weights: dict[str, float] | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        workspace_change_weights=workspace_change_weights,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock()
    return IdleWatchdog(config, clock), clock


def _make_production_monitor(
    watchdog: IdleWatchdog,
    tmp_path: Path,
    *,
    weights: dict[str, float] | None = None,
) -> WorkspaceMonitor:
    effective_weights = _normalize_workspace_change_weights(weights)
    return WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=effective_weights),
    )


def test_source_changes_defer_no_output_deadline(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/src/foo.py")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd.workspace_kind_counts == {"source": 1}


def test_log_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert wd.workspace_kind_counts == {}


def test_cache_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/__pycache__/foo.cpython-312.pyc")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


def test_artifact_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


def test_mixed_source_and_log_only_source_counts(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    monitor.record_event("/repo/src/foo.py")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd.workspace_kind_counts == {"source": 1}


def test_custom_weights_can_count_logs(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(
        activity_ttl=1000.0,
        workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    wd.record_activity()
    monitor = _make_production_monitor(
        wd,
        tmp_path,
        weights={"source": 1.0, "log": 1.0},
    )
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd.workspace_kind_counts == {"log": 1}


def test_dead_subagent_still_detected_when_log_file_written(tmp_path: Path) -> None:
    wd, clock = _make_watchdog(
        idle_timeout=0.1,
        max_waiting=10.0,
        activity_ttl=30.0,
    )
    wd.record_activity()
    wd.record_subagent_work()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(31.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert wd.workspace_kind_counts == {}


def test_truly_idle_session_terminated_on_time(tmp_path: Path) -> None:
    wd, clock = _make_watchdog()
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_fire_log_carries_per_kind_breakdown_in_extra(tmp_path: Path) -> None:
    wd, clock = _make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    wd.record_activity()
    monitor.record_event("/repo/src/foo.py")
    clock.advance(1.0)
    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd.handle_active_branch(clock.monotonic())
    finally:
        loguru_logger.remove(handler_id)

    fire_records = [m for m in captured if "FIRE reason=no_output_deadline" in m.record["message"]]
    assert fire_records
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    workspace_entry = next(
        e for e in bound_extra["evidence_summary"] if e["channel"] == "workspace"
    )

    assert workspace_entry["kind_breakdown"] == {"source": 1}


def test_production_binding_threads_real_kind_to_counter(tmp_path: Path) -> None:
    wd, _ = _make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    monitor.record_event("/repo/src/foo.py")
    assert wd.workspace_kind_counts == {"source": 1}
    monitor.record_event("/repo/agent.log")
    assert wd.workspace_kind_counts == {"source": 1}
    monitor.record_event("/repo/__pycache__/foo.pyc")
    assert wd.workspace_kind_counts == {"source": 1}
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    assert wd.workspace_kind_counts == {"source": 1}


def test_workspace_change_kind_canonical_module() -> None:
    canonical_kind = WorkspaceChangeKind

    assert canonical_kind is WorkspaceChangeKind
    assert WorkspaceChangeKind.SOURCE.value == "source"
    assert WorkspaceChangeKind.LOG.value == "log"
    assert WorkspaceChangeKind.CACHE.value == "cache"
    assert WorkspaceChangeKind.ARTIFACT.value == "artifact"
    assert WorkspaceChangeKind.OTHER.value == "other"


def test_default_weights_match_module_constant() -> None:
    classifier = WorkspaceChangeClassifier()
    assert classifier.weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
