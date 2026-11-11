from __future__ import annotations

import re
from pathlib import Path

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    _normalize_workspace_change_weights,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.general_config import GeneralConfig


def test_general_config_carries_default_classifier_weights() -> None:
    config = GeneralConfig()
    assert config.agent_workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_general_config_partial_override_flows_through_classifier() -> None:
    config = GeneralConfig(agent_workspace_change_weights={"source": 1.0, "log": 1.0})
    normalized = _normalize_workspace_change_weights(config.agent_workspace_change_weights)
    classifier = WorkspaceChangeClassifier(weights=normalized)
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 1.0
    kind, weight = classifier.classify("/repo/__pycache__/foo.pyc")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_normalize_helper_merges_over_defaults() -> None:
    normalized = _normalize_workspace_change_weights({"log": 1.0})
    assert normalized == {
        "source": 1.0,
        "log": 1.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


def test_workspace_monitor_classifier_threads_kind_and_weight(tmp_path: Path) -> None:
    received: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        received.append((kind, weight))

    classifier = WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))
    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=classifier)
    monitor.record_event("/repo/src/foo.py")
    assert received == [(WorkspaceChangeKind.SOURCE, 1.0)]


def test_workspace_monitor_with_custom_weights_can_count_logs(tmp_path: Path) -> None:
    received: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        received.append((kind, weight))

    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "log": 1.0})
    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=classifier)
    monitor.record_event("/repo/agent.log")
    assert received == [(WorkspaceChangeKind.LOG, 1.0)]


def test_production_binding_threads_kind_and_weight_to_watchdog(tmp_path: Path) -> None:

    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    monitor = WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)),
    )
    monitor.record_event("/repo/src/foo.py")
    assert watchdog.workspace_kind_counts == {"source": 1}


def test_legacy_binding_uses_other_default() -> None:

    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_workspace_event()
    assert watchdog.workspace_kind_counts == {"other": 1}


def test_end_to_end_log_change_does_not_defer_verdict(tmp_path: Path) -> None:

    policy = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        activity_evidence_ttl_seconds=1000.0,
    )
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    monitor = WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)),
    )
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE


def test_end_to_end_source_change_defers_verdict(tmp_path: Path) -> None:

    policy = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        activity_evidence_ttl_seconds=1000.0,
    )
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    monitor = WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)),
    )
    clock.advance(1.0)
    monitor.record_event("/repo/src/foo.py")
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE


def test_production_binding_files_use_two_arg_lambda() -> None:

    repo_root = Path(__file__).resolve().parents[2]
    for relative in (
        "ralph/agents/invoke/_process_reader.py",
        "ralph/agents/invoke/_pty_line_reader.py",
    ):
        path = repo_root / relative
        text = path.read_text()
        assert "set_on_event(watchdog.record_workspace_event)" not in text, (
            f"{relative} uses the pre-fix 0-arg binding;"
            f" must use a 2-arg lambda forwarding (kind, weight)"
        )

        pattern = re.compile(
            r"record_workspace_event\s*\(\s*kind\s*=\s*kind\s*,\s*weight\s*=\s*weight\s*\)"
        )

        assert pattern.search(text), f"{relative} does not use the 2-arg lambda form"
