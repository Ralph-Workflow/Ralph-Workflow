"""End-to-end tests for the class-aware workspace channel (AC #7).

These tests prove the full AC #7 contract:

  1. Source-code changes defer the NO_OUTPUT_DEADLINE verdict.
  2. Log-file changes do NOT defer the verdict (default policy).
  3. Cache-directory changes do NOT defer the verdict.
  4. Artifact-directory changes do NOT defer the verdict.
  5. A mix of source + log events still defers (only source counts).
  6. An operator opting in via ``agent_workspace_change_weights``
     can defer the verdict via log files.
  7. A dead subagent is still detected when only a log file is
     written (the log file alone does not defer the verdict).
  8. A truly idle session (no activity on any channel) is
     terminated on time.
  9. The fire log carries the per-kind breakdown in the loguru
     ``extra=`` dict (PA-014 fix: use the loguru sink pattern,
     NOT pytest's caplog).
 10. The production-style 2-arg lambda binding threads the real
     kind to the watchdog's per-kind counter (PA-003 closure).

Each test uses ``FakeClock`` and a fresh ``WorkspaceMonitor`` with
a real ``WorkspaceChangeClassifier`` plus the production-style
``lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight)``
binding. No real sleep, no real subprocess, no real I/O. Total
wall-clock for the file is well under 2s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
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
        # Disable suspicion (the default suspect=600s is greater
        # than the small max_waiting=10s used in these tests).
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        workspace_change_weights=workspace_change_weights,
    )
    clock = FakeClock()
    return IdleWatchdog(config, clock), clock


def _make_production_monitor(
    watchdog: IdleWatchdog,
    tmp_path: Path,
    *,
    weights: dict[str, float] | None = None,
) -> WorkspaceMonitor:
    """Construct a WorkspaceMonitor with the production-style 2-arg
    lambda binding and a real ``WorkspaceChangeClassifier``."""
    effective_weights = _normalize_workspace_change_weights(weights)
    return WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=effective_weights),
    )


# ---------------------------------------------------------------------------
# (1) Source changes defer
# ---------------------------------------------------------------------------


def test_source_changes_defer_no_output_deadline(tmp_path: Path) -> None:
    """A source-code change defers the NO_OUTPUT_DEADLINE verdict.

    Sequence:
      - watchdog with TTL=1000s, idle=0.1s
      - record_activity at t=0 (stdout baseline)
      - advance 1s past idle
      - source event (kicks workspace channel fresh)
      - evaluate -> CONTINUE (deferred via workspace)
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/src/foo.py")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    # The per-kind counter received the real SOURCE kind.
    assert wd.workspace_kind_counts == {"source": 1}


# ---------------------------------------------------------------------------
# (2) Log changes do NOT defer
# ---------------------------------------------------------------------------


def test_log_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A log-file change does NOT defer the NO_OUTPUT_DEADLINE
    verdict under the default conservative policy (log=0.0)."""
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The per-kind counter did NOT receive the dropped log event.
    assert wd.workspace_kind_counts == {}


# ---------------------------------------------------------------------------
# (3) Cache changes do NOT defer
# ---------------------------------------------------------------------------


def test_cache_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A ``__pycache__`` file change does NOT defer the verdict
    under the default conservative policy (cache=0.0)."""
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/__pycache__/foo.cpython-312.pyc")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


# ---------------------------------------------------------------------------
# (4) Artifact changes do NOT defer
# ---------------------------------------------------------------------------


def test_artifact_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A ``.agent/artifacts`` file change does NOT defer the verdict
    under the default conservative policy (artifact=0.0).

    This is the PA-001 closure: pre-fix, the ``.agent`` top-level
    was in ``CACHE_PARENT_DIRS`` and this test failed. The fixed
    rule order checks ``.agent/tmp``/``.agent/raw`` explicitly and
    reserves ``.agent/artifacts`` for ARTIFACT.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


# ---------------------------------------------------------------------------
# (5) Mixed source + log: only source counts
# ---------------------------------------------------------------------------


def test_mixed_source_and_log_only_source_counts(tmp_path: Path) -> None:
    """A log event (dropped) followed by a source event (deferred)
    results in CONTINUE; the dropped log event does not block the
    source event's deferral."""
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")  # dropped (log, weight 0.0)
    monitor.record_event("/repo/src/foo.py")  # deferred (source, weight 1.0)
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    # Only the source event was recorded.
    assert wd.workspace_kind_counts == {"source": 1}


# ---------------------------------------------------------------------------
# (6) Custom weights: log can be activated
# ---------------------------------------------------------------------------


def test_custom_weights_can_count_logs(tmp_path: Path) -> None:
    """An operator opts log files in by setting
    ``weights['log'] = 1.0``; the watchdog defers the verdict
    on a log event under the custom policy."""
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
    # The log event is recorded under the custom policy.
    assert wd.workspace_kind_counts == {"log": 1}


# ---------------------------------------------------------------------------
# (7) Dead subagent still detected
# ---------------------------------------------------------------------------


def test_dead_subagent_still_detected_when_log_file_written(tmp_path: Path) -> None:
    """When a subagent is alive but only writes a log file (no
    stdout, no tool calls, no source-code changes), the watchdog
    still detects the dead subagent at the regular idle window
    (NOT only at the cumulative WAITING_ON_CHILD ceiling).

    The log file alone does NOT defer the verdict (default policy).
    """
    wd, clock = _make_watchdog(
        idle_timeout=0.1,
        max_waiting=10.0,
        activity_ttl=30.0,
    )
    wd.record_activity()
    # A subagent work signal at t=0.
    wd.record_subagent_work()
    monitor = _make_production_monitor(wd, tmp_path)
    # Advance past the 30s default TTL so the subagent channel is stale.
    clock.advance(31.0)
    # A log file is written; dropped under default policy.
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    # The fire is NO_OUTPUT_DEADLINE (regular idle path), not
    # CHILDREN_PERSIST_TOO_LONG. Pre-fix, the log file alone would
    # have deferred the verdict (every file counted).
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The log event was dropped.
    assert wd.workspace_kind_counts == {}


# ---------------------------------------------------------------------------
# (8) Truly idle session terminated on time
# ---------------------------------------------------------------------------


def test_truly_idle_session_terminated_on_time(tmp_path: Path) -> None:
    """A session with no activity on any channel is terminated
    no later than today. The new class-aware verdict does not
    make the watchdog more lenient toward truly dead sessions."""
    wd, clock = _make_watchdog()
    # No record_* calls. Advance past idle timeout.
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (9) Fire log carries per-kind breakdown in loguru extra= (PA-014 fix)
# ---------------------------------------------------------------------------


def test_fire_log_carries_per_kind_breakdown_in_extra(tmp_path: Path) -> None:
    """The NO_OUTPUT_DEADLINE fire log carries the per-kind
    workspace breakdown in the loguru ``extra=`` dict so the
    post-mortem reader sees WHICH kinds were active at the moment
    of the fire.

    PA-014: pytest's caplog does NOT capture loguru's bound
    record.extra dict. We use the loguru sink pattern (the same
    pattern as tests/agents/test_idle_watchdog_3.py:620-680)
    so the structured fields are observable via
    ``message.record['extra']``.
    """
    wd, clock = _make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    # Drive a source event so the per-kind counter has data.
    wd.record_activity()
    monitor.record_event("/repo/src/foo.py")
    clock.advance(1.0)
    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd._handle_active_branch(clock.monotonic())
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

    # The single source event triggered the fire path; the per-kind
    # breakdown in the embedded diagnostic shows the source event.
    assert workspace_entry["kind_breakdown"] == {"source": 1}


# ---------------------------------------------------------------------------
# (10) Production binding threads real kind to counter (PA-003 closure)
# ---------------------------------------------------------------------------


def test_production_binding_threads_real_kind_to_counter(tmp_path: Path) -> None:
    """End-to-end: a WorkspaceMonitor with a real classifier AND
    the production-style 2-arg lambda binding threads the REAL
    kind to the watchdog's per-kind counter.

    PA-003 closure: pre-fix, the 0-arg bound-method form
    ``set_on_event(watchdog.record_workspace_event)`` meant the
    per-kind counter always received (OTHER, 1.0) defaults in
    production. This test proves the production-style 2-arg
    lambda binding (now used in both ``_process_reader.py`` and
    ``_pty_line_reader.py``) threads the real classification.
    """
    wd, _ = _make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    # Source event.
    monitor.record_event("/repo/src/foo.py")
    assert wd.workspace_kind_counts == {"source": 1}
    # Log event (dropped).
    monitor.record_event("/repo/agent.log")
    assert wd.workspace_kind_counts == {"source": 1}
    # Cache event (dropped).
    monitor.record_event("/repo/__pycache__/foo.pyc")
    assert wd.workspace_kind_counts == {"source": 1}
    # Artifact event (dropped).
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    assert wd.workspace_kind_counts == {"source": 1}


# ---------------------------------------------------------------------------
# Smoke test: WorkspaceChangeKind and classifier are importable from the
# canonical leaf module (so a refactor that moves the enum is caught).
# ---------------------------------------------------------------------------


def test_workspace_change_kind_canonical_module() -> None:
    """``WorkspaceChangeKind`` lives in its canonical leaf module
    so both the classifier and ``TimeoutPolicy`` can import from
    it without triggering a circular import via
    ``ralph.agents.invoke.__init__``."""
    canonical_kind = WorkspaceChangeKind

    assert canonical_kind is WorkspaceChangeKind
    assert WorkspaceChangeKind.SOURCE.value == "source"
    assert WorkspaceChangeKind.LOG.value == "log"
    assert WorkspaceChangeKind.CACHE.value == "cache"
    assert WorkspaceChangeKind.ARTIFACT.value == "artifact"
    assert WorkspaceChangeKind.OTHER.value == "other"


def test_default_weights_match_module_constant() -> None:
    """The classifier's default policy is identical to the
    module-level ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` constant
    so the policy and the classifier cannot drift independently."""
    classifier = WorkspaceChangeClassifier()
    assert classifier.weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
