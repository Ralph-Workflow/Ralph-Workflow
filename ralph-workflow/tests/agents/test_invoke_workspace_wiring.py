"""Black-box tests for the production-style WorkspaceMonitor -> Watchdog wiring.

These tests pin the production behavior:

  - The default ``WorkspaceMonitor`` constructed by ``invoke_agent``
    carries a real ``WorkspaceChangeClassifier`` built from the
    ``GeneralConfig.agent_workspace_change_weights`` dict (NOT
    ``None``, which would have given the legacy every-file-counts
    behavior).

  - The two production binding sites in ``_process_reader.py`` and
    ``_pty_line_reader.py`` wrap the watchdog's
    ``record_workspace_event`` in a 2-arg lambda that forwards
    ``(kind, weight)`` so the watchdog's per-kind counter receives
    real classifications (NOT the 0-arg bound method form, which
    would always yield ``(OTHER, 1.0)`` defaults and miss the
    AC #7 contract).

  - A ``_normalize_workspace_change_weights`` helper merges a
    partial operator dict over the conservative defaults so a TOML
    change of ``agent_workspace_change_weights = { log = 1.0 }``
    produces a complete dict (the missing kinds fall back to the
    default).
"""

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

# ---------------------------------------------------------------------------
# (a) Default classifier is constructed from the operator's config
# ---------------------------------------------------------------------------


def test_general_config_carries_default_classifier_weights() -> None:
    """A fresh ``GeneralConfig`` carries the documented default
    ``agent_workspace_change_weights`` dict (only source=1.0)."""
    config = GeneralConfig()
    assert config.agent_workspace_change_weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


def test_general_config_partial_override_flows_through_classifier() -> None:
    """An operator's partial override ``{log = 1.0, source = 1.0}``
    is plumbed through to a ``WorkspaceChangeClassifier`` with the
    full dict (missing kinds fall back to the defaults via
    ``_normalize_workspace_change_weights``)."""
    config = GeneralConfig(agent_workspace_change_weights={"source": 1.0, "log": 1.0})
    normalized = _normalize_workspace_change_weights(config.agent_workspace_change_weights)
    classifier = WorkspaceChangeClassifier(weights=normalized)
    # Log is now opted in.
    kind, weight = classifier.classify("/repo/agent.log")
    assert kind is WorkspaceChangeKind.LOG
    assert weight == 1.0
    # Cache is still dropped.
    kind, weight = classifier.classify("/repo/__pycache__/foo.pyc")
    assert kind is WorkspaceChangeKind.CACHE
    assert weight == 0.0


def test_normalize_helper_merges_over_defaults() -> None:
    """``_normalize_workspace_change_weights`` fills missing keys
    from ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` and preserves
    operator overrides."""
    normalized = _normalize_workspace_change_weights({"log": 1.0})
    assert normalized == {
        "source": 1.0,
        "log": 1.0,
        "cache": 0.0,
        "artifact": 0.0,
        "other": 0.0,
    }


# ---------------------------------------------------------------------------
# (b) WorkspaceMonitor accepts a classifier and threads it through
# ---------------------------------------------------------------------------


def test_workspace_monitor_classifier_threads_kind_and_weight(tmp_path: Path) -> None:
    """A ``WorkspaceMonitor`` with a real classifier threads the
    real ``(kind, weight)`` pair to the 2-arg ``on_event`` callback."""
    received: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        received.append((kind, weight))

    classifier = WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))
    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=classifier)
    monitor.record_event("/repo/src/foo.py")
    assert received == [(WorkspaceChangeKind.SOURCE, 1.0)]


def test_workspace_monitor_with_custom_weights_can_count_logs(tmp_path: Path) -> None:
    """A custom classifier with ``weights['log'] = 1.0`` does count
    log files as activity (the operator opt-in path)."""
    received: list[tuple[WorkspaceChangeKind, float]] = []

    def on_event(kind: WorkspaceChangeKind, weight: float) -> None:
        received.append((kind, weight))

    classifier = WorkspaceChangeClassifier(weights={"source": 1.0, "log": 1.0})
    monitor = WorkspaceMonitor(tmp_path, on_event=on_event, classifier=classifier)
    monitor.record_event("/repo/agent.log")
    assert received == [(WorkspaceChangeKind.LOG, 1.0)]


# ---------------------------------------------------------------------------
# (c) Production binding site: 2-arg lambda forwards kind and weight
# ---------------------------------------------------------------------------


def test_production_binding_threads_kind_and_weight_to_watchdog(tmp_path: Path) -> None:
    """The production-style 2-arg binding
    ``lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight)``
    is what the two reader files wire up. The watchdog's per-kind
    counter receives the real classification (not the OTHER default
    that the 0-arg bound-method form would yield)."""

    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    monitor = WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)),
    )
    monitor.record_event("/repo/src/foo.py")
    # The watchdog's per-kind counter has the REAL kind (source),
    # not the OTHER default that the 0-arg binding would yield.
    assert watchdog.workspace_kind_counts == {"source": 1}


def test_legacy_binding_uses_other_default() -> None:
    """The 0-arg legacy binding ``watchdog.record_workspace_event``
    (the pre-fix production code) records the event as
    ``WorkspaceChangeKind.OTHER / 1.0``. This is the diagnostic
    that proves the production-style 2-arg binding is needed to
    thread the real classification to the watchdog's per-kind
    counter."""

    policy = TimeoutPolicy(idle_timeout_seconds=0.1)
    clock = FakeClock()
    watchdog = IdleWatchdog(policy, clock)
    # 0-arg binding (legacy): no kind/weight forward; the watchdog
    # defaults to (OTHER, 1.0).
    watchdog.record_workspace_event()
    assert watchdog.workspace_kind_counts == {"other": 1}


# ---------------------------------------------------------------------------
# (d) End-to-end: WorkspaceMonitor -> Watchdog with classifier
# ---------------------------------------------------------------------------


def test_end_to_end_log_change_does_not_defer_verdict(tmp_path: Path) -> None:
    """End-to-end: a log file change with the default classifier
    does NOT defer the NO_OUTPUT_DEADLINE verdict (the conservative
    default policy drops log events)."""

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
    clock.advance(1.0)  # past idle
    monitor.record_event("/repo/agent.log")  # log event; dropped
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE  # log event was dropped


def test_end_to_end_source_change_defers_verdict(tmp_path: Path) -> None:
    """End-to-end: a source file change with the default classifier
    DOES defer the NO_OUTPUT_DEADLINE verdict (source=1.0 by default)."""

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
    clock.advance(1.0)  # past idle
    monitor.record_event("/repo/src/foo.py")  # source event
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE  # source event deferred the fire


# ---------------------------------------------------------------------------
# (e) PA-003 regression: production binding site does NOT use the 0-arg form
# ---------------------------------------------------------------------------


def test_production_binding_files_use_two_arg_lambda() -> None:
    """The two reader files (``_process_reader.py`` and
    ``_pty_line_reader.py``) MUST use a 2-arg lambda to bind
    the workspace event callback to the watchdog. This is the
    PA-003 regression test: pre-fix, the 0-arg bound-method form
    meant the per-kind counter always received (OTHER, 1.0)
    defaults in production."""

    repo_root = Path(__file__).resolve().parents[2]
    for relative in (
        "ralph/agents/invoke/_process_reader.py",
        "ralph/agents/invoke/_pty_line_reader.py",
    ):
        path = repo_root / relative
        text = path.read_text()
        # The 2-arg lambda form is the only allowed production binding.
        # The pre-fix 0-arg form ``set_on_event(watchdog.record_workspace_event)``
        # is NOT allowed.
        assert "set_on_event(watchdog.record_workspace_event)" not in text, (
            f"{relative} uses the pre-fix 0-arg binding;"
            f" must use a 2-arg lambda forwarding (kind, weight)"
        )

        # The 2-arg lambda must be present (whitespace-tolerant).
        pattern = re.compile(
            r"record_workspace_event\s*\(\s*kind\s*=\s*kind\s*,\s*weight\s*=\s*weight\s*\)"
        )

        assert pattern.search(text), f"{relative} does not use the 2-arg lambda form"
