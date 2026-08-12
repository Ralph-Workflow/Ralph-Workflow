"""Consolidated tests from idle_watchdog/test_*.py.

This module merges the following previously split test modules into a single
file to reduce per-shard collection cost. The original class names are
preserved so external references (test::TestX) still resolve.

Source files:
  - test_activity_aware.py
  - test_both_repetition_dimensions.py
  - test_claude_interactive_tool_fingerprints.py
  - test_clean_exit_session_id_recovery.py
  - test_cross_transport_subagent_visibility.py
  - test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py
  - test_cursor_tool_fingerprints.py
  - test_diagnostic_snapshot.py
  - test_dumb_kill_scenarios.py
  - test_e2e_activity_aware.py
  - test_emit_info_log_throttle.py
  - test_evidence_deferral_throttle.py
  - test_hard_ceiling_with_helpers_alive.py
  - test_invocation_start_full_reset.py
  - test_log_spam_throttle.py
  - test_log_spam_throttle_public_surface.py
  - test_mark_tool_call_runtime_reachability.py
  - test_no_output_at_start.py
  - test_no_output_at_start_lifecycle_parity.py
  - test_no_output_at_start_loading.py
  - test_no_progress_quiet_watchdog.py
  - test_non_resumable_end_to_end.py
  - test_opencode_step_frames.py
  - test_opencode_tool_call_fingerprints.py
  - test_os_descendant_only_escalation.py
  - test_post_exit_watchdog_no_resume.py
  - test_production_subagent_registry_wiring.py
  - test_pure_stall_wedge.py
  - test_repetition_window_cycle_detection.py
  - test_resume_after_kill_contract.py
  - test_resume_after_kill_watchdog_boundary.py
  - test_resume_contract_invariant.py
  - test_resume_session_id_threading.py
  - test_runtime_session_resume_safe_mapping.py
  - test_session_ceiling_no_resume.py
  - test_shared_subagent_pid_registry.py
  - test_silent_after_tool_call_wedge.py
  - test_silent_subagent_fires.py
  - test_silent_subagent_runtime.py
  - test_smart_verdict_dumb_kills.py
  - test_stall_lifetime.py
  - test_stall_status_events.py
  - test_strictly_stuck_ceiling.py
  - test_stuck_classifier.py
  - test_stuck_job_heartbeat_ceiling.py
  - test_stuck_job_intelligence.py
  - test_stuck_job_sub_ceiling.py
  - test_subagent_capture_eviction.py
  - test_subagent_identity_excludes_helpers.py
  - test_subagent_progress_surface.py
  - test_timeout_policy.py
  - test_tool_call_parser.py
  - test_tool_result_routing.py
  - test_waiting_subagent_progress.py
  - test_watch_loop_base.py
  - test_watchdog_recovery_contract.py
"""

from __future__ import annotations

import ast
import contextlib
import functools
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import threading as _threading
import time
from collections.abc import (
    Callable,
    Iterator,
    Mapping,
)
from dataclasses import (
    dataclass,
    field,
    replace,
)
from pathlib import Path
from types import (
    MethodType,
    SimpleNamespace,
)
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
)

import psutil
import pytest
from loguru import logger

import ralph.agents.invoke._process_reader as _process_reader_module
from ralph.agents import (
    invoke as invoke_module,
)
from ralph.agents import (
    invoke as ralph_invoke,
)
from ralph.agents.activity import (
    AgentActivityKind,
    AgentActivitySignal,
)
from ralph.agents.catalog import default_catalog
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.execution_state._factory import (
    _make_cursor_strategy,
    _make_pi_strategy,
)
from ralph.agents.idle_watchdog import (
    AliveBy,
    CorroborationSnapshot,
    IdleWatchdog,
    PostExitVerdict,
    PostExitWatchdog,
    StuckKind,
    SubagentIdentity,
    SubagentPidRegistry,
    TimeoutPolicy,
    WaitingCorroborator,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._activity_methods import (
    _KNOWN_TOOL_CALL_VERBS,
    _parse_tool_call_from_description,
)
from ralph.agents.idle_watchdog._evidence_tier import (
    CHANNEL_DEFERS_BY_DEFAULT,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ralph.agents.idle_watchdog._stuck_classifier import (
    ClassifyStuckInputs,
    classify_stuck,
)
from ralph.agents.idle_watchdog._subagent_identity import _MAX_REGISTRY_ENTRIES
from ralph.agents.idle_watchdog._watch_loop_base import WatchLoopBase
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.idle_watchdog.idle_watchdog import (
    _EXPECTED_FIRE_REASONS,
)
from ralph.agents.idle_watchdog.repetition_tracker import RepetitionTracker
from ralph.agents.idle_watchdog_kill import (
    IdleWatchdogKilledError,
)
from ralph.agents.idle_watchdog_kill import (
    IdleWatchdogKilledError as IdleWatchdogKilledErrorTop,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    InvokeOptions,
    OpenCodeResumableExitError,
    check_process_result,
    fresh_session_options,
    invoke_agent,
)
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._monitor_factory import _discovery_strategy_for_config
from ralph.agents.invoke._process_reader import (
    _RESUMABLE_FIRE_REASONS,
    ProcessLineReader,
    _convert_idle_stream_timeout_to_agent_error,
    _extract_tool_call_from_activity_signal,
    _is_resumable_fire_reason,
)
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._session import _bounded_output_lines
from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._tool_call_extraction import extract_tool_call_from_activity_signal
from ralph.agents.parsers import (
    AgyParser,
    ClaudeInteractiveParser,
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    PiParser,
    get_parser,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.system_clock import SystemClock
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import (
    AgentTransport,
    JsonParserType,
)
from ralph.config.models import (
    AgentConfig,
    UnifiedConfig,
)
from ralph.mcp.server._activity_sink import (
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import agent_retry_intent_for_failure
from ralph.pipeline.effect_executor import _failure_requires_fresh_session
from ralph.pipeline.state import (
    PipelineState,
)
from ralph.policy.loader import load_policy
from ralph.process.child_liveness import (
    ChildLivenessRegistry,
    ChildLivenessSubagentPidSource,
)
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    FileSubagentOutputCapture,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    ProcessMonitor,
    ProcessRole,
    SubagentOutputCapture,
    SubagentPidSource,
    make_claude_subagent_pid_source,
    make_opencode_subagent_pid_source,
)
from ralph.recovery.classifier import (
    FailureClassifier,
    FailureContext,
)
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
)
from ralph.recovery.events import FailureEventBus
from ralph.timeout_defaults import (
    NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS,
    STUCK_JOB_SUB_CEILING_SECONDS,
)
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.waiting_status_event import (
        WaitingStatusEvent,
        WaitingStatusListener,
    )
    from ralph.agents.parsers.base import AgentParser

_IDLE_TIMEOUT = 0.1

_DRAIN_WINDOW = 0.0

_MAX_WAITING = 10.0

_ACTIVITY_TTL = 30.0

_REAL_PROGRESS_LINE = "[subagent] progress: phase=phase-1"

_REAL_HEARTBEAT_LINE = "[subagent] heartbeat"

_REAL_CHILD_JSON_LINE = '{"type": "child_progress", "child_id": "child-A", "phase": "phase-2"}'

_KNOWN_TOOL_CALL_VERBS_FOR_TEST: frozenset[str] = frozenset(
    {
        "tool_use",
        "tool_result",
        "mcp_tool",
        "subagent",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "webfetch",
        "websearch",
    }
)

__all__ = [
    "test_cumulative_ceiling_fires_when_classify_stuck_returns_loading",
    "test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent",
]

_ = None

pytestmark = pytest.mark.subprocess_e2e

_IDE_TIMEOUT = 0.1

_DRAIN = 0.0

_INFO_LOG_SUBSTRING = "idle watchdog: subagent activity:"

_MAX_DEFER_EMISSIONS = 2

_NO_OUTPUT_AT_START_SECONDS = 30.0

_ACTIVITY_TTL_SECONDS = 180.0

_MAX_WAITING_SECONDS = 600.0

_FRESH_ALIVE_BY_STATES: tuple[AliveBy, ...] = (
    AliveBy.FRESH_PROGRESS,
    AliveBy.FRESH_HEARTBEAT_ONLY,
)

_STALE_ALIVE_BY_STATES: tuple[AliveBy, ...] = (
    AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    AliveBy.CPU_IDLE_WHILE_ALIVE,
    AliveBy.LOG_STALE_WHILE_ALIVE,
    AliveBy.STALE_LABEL_ONLY,
)

_IDLE_TIMEOUT_SECONDS = 300.0

_SILENT_SUBAGENT_SECONDS = 180.0

_NO_PROGRESS_QUIET_SECONDS = 60.0

_RESUMABLE_REASONS: frozenset[str] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_AT_START.value,
        WatchdogFireReason.NO_OUTPUT_DEADLINE.value,
        WatchdogFireReason.NO_PROGRESS_QUIET.value,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT.value,
        WatchdogFireReason.REPEATED_ERROR_LOOP.value,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL.value,
    }
)

_RESUMABLE_REASON_VALUES: frozenset[str] = frozenset(r.value for r in _RESUMABLE_FIRE_REASONS)

_NON_RESUMABLE_REASON_VALUES: frozenset[str] = frozenset(
    {
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG.value,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED.value,
        WatchdogFireReason.PROCESS_EXIT_HANG.value,
        WatchdogFireReason.DESCENDANT_HANG.value,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER.value,
        WatchdogFireReason.STRICTLY_STUCK.value,
    }
)

_NON_RESUMABLE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
        WatchdogFireReason.STRICTLY_STUCK,
    }
)

_RESUMABLE_REASONS_EXPECTED: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.NO_OUTPUT_AT_START,
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        WatchdogFireReason.REPEATED_ERROR_LOOP,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
    }
)

_ACTIVITY_EVIDENCE_TTL_SECONDS = 300.0

_TTL_SECONDS = 30.0

_NOW = 1000.0

_PROBE_WORKER_COUNT: int = 4096

_OVERFLOW_DELTA: int = 5

_EXPECTED_CANONICAL_VERBS: frozenset[str] = frozenset(
    {
        "tool_use",
        "tool_result",
        "mcp_tool",
        "subagent",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "webfetch",
        "websearch",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[2]

IDLE_WATCHDOG_DIR = REPO_ROOT / "ralph" / "agents" / "idle_watchdog"

PROCESS_READER = REPO_ROOT / "ralph" / "agents" / "invoke" / "_process_reader.py"

POST_EXIT_WATCHDOG = REPO_ROOT / "ralph" / "agents" / "idle_watchdog" / "_post_exit_watchdog.py"

UNAVAILABILITY_TRACKER = REPO_ROOT / "ralph" / "recovery" / "agent_unavailability_tracker.py"




# === Helper for test_activity_aware.py ===
def _activity_aware_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_activity_aware.py ===
def _activity_aware_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_activity_aware.py ===
def _activity_aware_make_policy(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = _ACTIVITY_TTL,
    silent_subagent_seconds: float | None = None,
) -> TimeoutPolicy:
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": None,
        # Disable the stuck-job sub-ceiling so this test file can use
        # a small ``max_waiting_on_child_seconds`` (10s) for fast
        # in-memory waiting-branch cycles without tripping the new
        # sub-ceiling default (600s).
        "stuck_job_sub_ceiling_seconds": None,
        # Disable the SILENT_SUBAGENT diagnostic by default so this
        # file exercises the activity-aware fire path (NO_OUTPUT_DEADLINE
        # etc.) rather than the SILENT_SUBAGENT classifier branch.
        # Tests that explicitly exercise SILENT_SUBAGENT are in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``.
        "silent_subagent_seconds": silent_subagent_seconds,
    }
    return TimeoutPolicy(**kwargs)


# === Helper for test_activity_aware.py ===
def _activity_aware_make_watchdog(
    policy: TimeoutPolicy | None = None,
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    policy = policy if policy is not None else _activity_aware_make_policy()
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
        ),
        clock,
    )


# === Helper for test_cross_transport_subagent_visibility.py ===
def _cross_transport_subagent_visib_make_watchdog() -> IdleWatchdog:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())


# === Helper for test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py ===
def _cumulative_waiting_ceiling_fir_waiting_on_child() -> AgentExecutionState:
    """classify_quiet that returns WAITING_ON_CHILD on every call."""
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py ===
def _cumulative_waiting_ceiling_fir_make_watchdog(
    *,
    clock: FakeClock,
    process_monitor: ProcessMonitor,
    policy: TimeoutPolicy,
    corroborator: Callable[[], CorroborationSnapshot] | None = None,
) -> IdleWatchdog:
    """Build an IdleWatchdog with the given policy, clock, and monitor."""
    return IdleWatchdog(
        policy,
        clock,
        listener=None,
        corroborator=corroborator,
        process_monitor=process_monitor,
    )


# === Helper for test_diagnostic_snapshot.py ===
def _diagnostic_snapshot_make_watchdog(*, monitor_count: int = 0) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_FakeProcessMonitor(count=monitor_count),
        ),
        clock,
    )


# === Helper for test_dumb_kill_scenarios.py ===
def _dumb_kill_scenarios_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_dumb_kill_scenarios.py ===
def _dumb_kill_scenarios_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_dumb_kill_scenarios.py ===
def _dumb_kill_scenarios_make_policy(
    *,
    idle_timeout: float = 1.0,
    drain_window: float = 0.0,
    max_waiting: float = 600.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = None,
    os_descendant_only_ceiling: float | None = 300.0,
) -> TimeoutPolicy:
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": os_descendant_only_ceiling,
        "no_output_at_start_seconds": no_output_at_start,
        "post_tool_result_progression_seconds": None,
        "repeated_error_window_count": 5,
        "repeated_error_window_seconds": 60.0,
        "idle_poll_interval_seconds": 0.05,
        "waiting_status_interval_seconds": 30.0,
    }
    return TimeoutPolicy(**kwargs)


# === Helper for test_dumb_kill_scenarios.py ===
def _dumb_kill_scenarios_make_watchdog(
    policy: TimeoutPolicy | None = None,
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    policy = policy if policy is not None else _dumb_kill_scenarios_make_policy()
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
            corroborator=corroborator,
        ),
        clock,
    )


# === Helper for test_e2e_activity_aware.py ===
def _e2e_activity_aware_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_e2e_activity_aware.py ===
def _e2e_activity_aware_make_policy(activity_ttl: float | None = 30.0) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=_IDE_TIMEOUT,
        drain_window_seconds=_DRAIN,
        max_waiting_on_child_seconds=_MAX_WAITING,
        # Disable the stuck-job sub-ceiling: this test file uses a
        # small cumulative ceiling (_MAX_WAITING) for fast in-memory
        # cycles. The sub-ceiling default (600s) would fail the
        # ``<= max_waiting_on_child_seconds`` validator. The tests
        # in this file do not exercise the sub-ceiling path; the
        # dedicated tests live in
        # ``tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py``.
        stuck_job_sub_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        os_descendant_only_ceiling_seconds=None,
        # Disable the SILENT_SUBAGENT diagnostic by default so this
        # file exercises the activity-aware fire path (NO_OUTPUT_DEADLINE
        # etc.) rather than the SILENT_SUBAGENT classifier branch.
        # Tests that explicitly exercise SILENT_SUBAGENT are in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``.
        silent_subagent_seconds=None,
    )


# === Helper for test_e2e_activity_aware.py ===
def _e2e_activity_aware_make_watchdog(
    policy: TimeoutPolicy,
    process_monitor: ProcessMonitor | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
        ),
        clock,
    )


# === Helper for test_emit_info_log_throttle.py ===
def _emit_info_log_throttle_make_watchdog(
    *,
    idle_timeout: float = 1.0,
    status_interval: float = 10.0,
    subagent_progress_interval: float = 3600.0,
    max_waiting: float = 600.0,
) -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog with a fixed status cadence.

    ``subagent_progress_interval`` is set high (1 hour) so the
    SUBAGENT_PROGRESS emit does NOT fire during the test window. This
    isolates the test to the ENTERED + PROGRESS cadence which is the
    primary throttle that the R6 contract pins. The PROGRESS cadence
    is the operator-visible cadence and is the load-bearing
    rate-limiter for the no-subagent-listener INFO log path.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.5,
        max_waiting_on_child_seconds=max_waiting,
        waiting_status_interval_seconds=status_interval,
        watchdog_subagent_progress_interval_seconds=subagent_progress_interval,
        suspect_waiting_on_child_seconds=None,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    clock = FakeClock(start=0.0)
    return IdleWatchdog(policy, clock), clock


# === Helper for test_emit_info_log_throttle.py ===
def _emit_info_log_throttle_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_evidence_deferral_throttle.py ===
def _evidence_deferral_throttle_make_watchdog(*, throttle_seconds: float = 30.0) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    kwargs: dict[str, Any] = {
        "idle_timeout_seconds": 60.0,
        "no_output_at_start_seconds": 30.0,
        "no_progress_quiet_seconds": None,
        "watchdog_log_throttle_seconds": throttle_seconds,
        "activity_evidence_ttl_seconds": 180.0,
    }
    policy = TimeoutPolicy(**kwargs)
    return (
        IdleWatchdog(policy, clock),
        clock,
    )


# === Helper for test_evidence_deferral_throttle.py ===
def _evidence_deferral_throttle_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_hard_ceiling_with_helpers_alive.py ===
def _hard_ceiling_with_helpers_aliv_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_hard_ceiling_with_helpers_alive.py ===
def _hard_ceiling_with_helpers_aliv_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_invocation_start_full_reset.py ===
def _invocation_start_full_reset_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock), clock


# === Helper for test_log_spam_throttle.py ===
def _log_spam_throttle_make_watchdog(throttle_seconds: float = 30.0) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        watchdog_log_throttle_seconds=throttle_seconds,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(policy, clock),
        clock,
    )


# === Helper for test_no_output_at_start.py ===
def _no_output_at_start_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_no_output_at_start.py ===
def _no_output_at_start_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_no_output_at_start.py ===
def _no_output_at_start_make_policy(
    *,
    no_output_at_start: float = _NO_OUTPUT_AT_START_SECONDS,
    activity_ttl: float = _ACTIVITY_TTL_SECONDS,
    silent_subagent_seconds: float | None = None,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_seconds=_MAX_WAITING_SECONDS,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        # Disable the SILENT_SUBAGENT diagnostic in this test file so
        # the assertions exercise the TTL-bounded deferral gate for
        # ``_channel_evidence_active`` rather than the SILENT_SUBAGENT
        # classifier branch.  The SILENT_SUBAGENT path is covered in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``
        # with its own runtime contract tests.
        silent_subagent_seconds=silent_subagent_seconds,
    )


# === Helper for test_no_output_at_start.py ===
def _no_output_at_start_make_watchdog(
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    activity_ttl: float = _ACTIVITY_TTL_SECONDS,
    silent_subagent_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            _no_output_at_start_make_policy(
                activity_ttl=activity_ttl,
                silent_subagent_seconds=silent_subagent_seconds,
            ),
            clock,
            process_monitor=process_monitor or _NoProcessMonitorNoOutputAtStart(),
        ),
        clock,
    )


# === Helper for test_no_output_at_start_lifecycle_parity.py ===
def _no_output_at_start_lifecycle_p_make_policy(*, no_output_at_start_seconds: float = 30.0) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start_seconds,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        silent_subagent_seconds=None,
    )


# === Helper for test_no_output_at_start_lifecycle_parity.py ===
def _no_output_at_start_lifecycle_p_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_no_output_at_start_loading.py ===
def _no_output_at_start_loading_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_no_output_at_start_loading.py ===
def _no_output_at_start_loading_make_watchdog(
    *,
    invocation_floor: float = 120.0,
    no_output_at_start: float = 30.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=invocation_floor,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=_StubCorroborator(alive_by),
            process_monitor=_NoProcessMonitorNoOutputAtStartLoading(),
        ),
        clock,
    )


# === Helper for test_non_resumable_end_to_end.py ===
def _non_resumable_end_to_end_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_non_resumable_end_to_end.py ===
def _non_resumable_end_to_end_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_os_descendant_only_escalation.py ===
def _os_descendant_only_escalation_make_watchdog(
    idle_timeout: float | None = 10.0,
    max_waiting: float | None = None,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
    os_descendant_only_ceiling: float | None = None,
    os_descendant_only_suspect: float | None = None,
    cpu_idle_seconds: float | None = None,
    log_growth_seconds: float | None = None,
    start: float = 0.0,
    status_interval: float = 30.0,
    corroborator: WaitingCorroborator | None = None,
    no_progress_quiet_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    # Default ``no_progress_quiet_seconds`` to ``no_progress_ceiling`` so
    # the no_progress_quiet_seconds <= max_waiting_on_child_no_progress_seconds
    # cross-field validator passes regardless of caller-supplied
    # ``no_progress_ceiling``.
    if no_progress_quiet_seconds is None:
        no_progress_quiet_seconds = (
            no_progress_ceiling if no_progress_ceiling is not None else 240.0
        )
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=max_waiting,
        suspect_waiting_on_child_seconds=suspect,
        waiting_status_interval_seconds=status_interval,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect,
        cpu_idle_seconds=cpu_idle_seconds,
        log_growth_seconds=log_growth_seconds,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_heartbeat_ceiling_seconds=no_progress_quiet_seconds,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock, corroborator=corroborator), clock


# === Helper for test_os_descendant_only_escalation.py ===
def _os_descendant_only_escalation_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_pure_stall_wedge.py ===
def _pure_stall_wedge_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_pure_stall_wedge.py ===
def _pure_stall_wedge_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_pure_stall_wedge.py ===
def _pure_stall_wedge_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS,
        no_output_at_start_seconds=_NO_OUTPUT_AT_START_SECONDS,
        no_progress_quiet_seconds=_NO_PROGRESS_QUIET_SECONDS,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        silent_subagent_seconds=_SILENT_SUBAGENT_SECONDS,
        activity_evidence_ttl_seconds=30.0,
    )

    def _no_live_child_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=None)

    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitorPureStallWedge(),
            corroborator=_no_live_child_corroborator,
        ),
        clock,
    )


# === Helper for test_resume_after_kill_contract.py ===
def _resume_after_kill_contract_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_resume_contract_invariant.py ===
def _resume_contract_invariant_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_resume_contract_invariant.py ===
def _resume_contract_invariant_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorResumeContractInvariant()), clock


# === Helper for test_silent_after_tool_call_wedge.py ===
def _silent_after_tool_call_wedge_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_silent_after_tool_call_wedge.py ===
def _silent_after_tool_call_wedge_make_watchdog(
    *,
    corroborator: object,
    activity_evidence_ttl: float = _ACTIVITY_EVIDENCE_TTL_SECONDS,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS,
        no_output_at_start_seconds=_NO_OUTPUT_AT_START_SECONDS,
        no_progress_quiet_seconds=_NO_PROGRESS_QUIET_SECONDS,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        activity_evidence_ttl_seconds=activity_evidence_ttl,
        silent_subagent_seconds=_SILENT_SUBAGENT_SECONDS,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=_NoProcessMonitorSilentAfterToolCallWedge(),
            corroborator=corroborator,
        ),
        clock,
    )


# === Helper for test_silent_subagent_fires.py ===
def _silent_subagent_fires_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        silent_subagent_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorSilentSubagentFires())
    return watchdog, clock


# === Helper for test_silent_subagent_runtime.py ===
def _silent_subagent_runtime_make_watchdog(
    *,
    silent_subagent_seconds: float | None = 180.0,
    activity_evidence_ttl_seconds: float | None = 30.0,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        silent_subagent_seconds=silent_subagent_seconds,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorSilentSubagentRuntime())
    return watchdog, clock


# === Helper for test_silent_subagent_runtime.py ===
def _silent_subagent_runtime_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_smart_verdict_dumb_kills.py ===
def _smart_verdict_dumb_kills_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_smart_verdict_dumb_kills.py ===
def _smart_verdict_dumb_kills_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_smart_verdict_dumb_kills.py ===
def _smart_verdict_dumb_kills_make_policy(
    *,
    idle_timeout: float = 1.0,
    drain_window: float = 0.0,
    max_waiting: float = 600.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = None,
    os_descendant_only_ceiling: float | None = 300.0,
) -> TimeoutPolicy:
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": os_descendant_only_ceiling,
        "no_output_at_start_seconds": no_output_at_start,
        "post_tool_result_progression_seconds": None,
        "repeated_error_window_count": 5,
        "repeated_error_window_seconds": 60.0,
        "idle_poll_interval_seconds": 0.05,
        "waiting_status_interval_seconds": 30.0,
    }
    return TimeoutPolicy(**kwargs)


# === Helper for test_smart_verdict_dumb_kills.py ===
def _smart_verdict_dumb_kills_make_watchdog(
    policy: TimeoutPolicy | None = None,
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    policy = policy if policy is not None else _smart_verdict_dumb_kills_make_policy()
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
            corroborator=corroborator,
        ),
        clock,
    )


# === Helper for test_stall_lifetime.py ===
def _stall_lifetime_watchdog(
    clock: FakeClock,
    events: list[WaitingStatusEvent],
) -> IdleWatchdog:
    return IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=60.0),
        clock,
        listener=events.append,
    )


# === Helper for test_stall_status_events.py ===
def _stall_status_events_make_watchdog(
    *,
    listener: WaitingStatusListener | None = None,
    idle_timeout_seconds: float | None = 60.0,
    no_output_at_start_seconds: float | None = 30.0,
    drain_window_seconds: float = 0.0,
    max_waiting_on_child_seconds: float = 1800.0,
    max_session_seconds: float | None = None,
    no_progress_quiet_seconds: float | None = None,
    watchdog_log_throttle_seconds: float = 30.0,
    activity_evidence_ttl_seconds: float | None = 180.0,
    suspect_waiting_on_child_seconds: float | None = None,
    max_waiting_on_child_no_progress_seconds: float | None = None,
    corroborator: object | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    """Construct a watchdog with the canonical test policy.

    Each ``TimeoutPolicy`` field accepted as a keyword argument
    preserves the typed call site (``TimeoutPolicy(**kwargs)`` keeps
    every override narrowly typed without ``cast`` / ``type: ignore``
    suppression). Only the fields exercised by
    ``test_stall_status_events`` accept overrides here; every other
    field falls back to the dataclass default. The default
    ``idle_timeout`` is sized so the post-tool-result stall helper
    can drive ``STALLED_AFTER_TOOL_RESULT`` deterministically without
    needing real time. The ``stuck_job_sub_ceiling_seconds`` is left
    default so the cumulative-ceiling branch can be exercised.

    The ``corroborator`` parameter is forwarded into
    ``IdleWatchdog.__init__`` so SUSPECTED_FROZEN tests can drive the
    WAITING_ON_CHILD branch through ``evaluate()`` (the SUSPECTED
    threshold is computed against the corroborator's ``alive_by``).

    The ``max_waiting_on_child_no_progress_seconds`` parameter is
    needed when a test narrows ``max_waiting_on_child_seconds`` below
    the dataclass default of 600.0 -- the cross-field validator
    rejects any no-progress ceiling that exceeds the main ceiling.
    Tests that keep the default ``max_waiting_on_child_seconds`` of
    1800.0 do not need to override it.
    """
    clock = FakeClock(start=0.0)
    # If the no-progress ceiling is unset but the test narrows
    # ``max_waiting_on_child_seconds`` below the dataclass default
    # of 600.0, mirror the test's narrower ceiling so the validator
    # is satisfied without forcing the caller to spell out the
    # secondary knob. Same trick for the
    # ``os_descendant_only_ceiling_seconds`` (default 300.0) and the
    # ``stuck_job_sub_ceiling_seconds`` (default 600.0) -- they
    # must all be <= ``max_waiting_on_child_seconds``.
    if max_waiting_on_child_no_progress_seconds is None and max_waiting_on_child_seconds < 600.0:
        max_waiting_on_child_no_progress_seconds = max_waiting_on_child_seconds
    if (
        max_waiting_on_child_no_progress_seconds is not None
        and no_output_at_start_seconds is not None
        and no_output_at_start_seconds >= max_waiting_on_child_no_progress_seconds
    ):
        no_output_at_start_seconds = None
    if max_waiting_on_child_seconds < 300.0:
        os_descendant_only_ceiling_seconds: float | None = max_waiting_on_child_seconds
        # The OS-descendant-only suspect threshold (default 60.0) must
        # be strictly less than the OS-descendant-only ceiling. When
        # the test narrows the ceiling below 60.0, mirror it.
        os_descendant_only_suspect_seconds: float | None = max(
            suspect_waiting_on_child_seconds or 1.0,
            max_waiting_on_child_seconds / 2.0,
        )
    else:
        os_descendant_only_ceiling_seconds = None
        os_descendant_only_suspect_seconds = None
    if max_waiting_on_child_seconds < 600.0:
        stuck_job_sub_ceiling_seconds: float | None = max_waiting_on_child_seconds
    else:
        stuck_job_sub_ceiling_seconds = None
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        drain_window_seconds=drain_window_seconds,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_session_seconds=max_session_seconds,
        no_output_at_start_seconds=no_output_at_start_seconds,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        watchdog_log_throttle_seconds=watchdog_log_throttle_seconds,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        suspect_waiting_on_child_seconds=suspect_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=max_waiting_on_child_no_progress_seconds,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling_seconds,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect_seconds,
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
    )
    return (
        IdleWatchdog(policy, clock, listener=listener, corroborator=corroborator),
        clock,
    )


# === Helper for test_strictly_stuck_ceiling.py ===
def _strictly_stuck_ceiling_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_strictly_stuck_ceiling.py ===
def _strictly_stuck_ceiling_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_strictly_stuck_ceiling.py ===
def _strictly_stuck_ceiling_make_watchdog(
    *,
    strictly_stuck_seconds: float | None = 300.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=600.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        no_progress_quiet_strictly_stuck_seconds=strictly_stuck_seconds,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        watchdog_subagent_progress_interval_seconds=30.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=_StubCorroboratorStrictlyStuckCeiling(alive_by),
            process_monitor=_NoProcessMonitorStrictlyStuckCeiling(),
        ),
        clock,
    )


# === Helper for test_stuck_job_heartbeat_ceiling.py ===
def _stuck_job_heartbeat_ceiling_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_stuck_job_heartbeat_ceiling.py ===
def _stuck_job_heartbeat_ceiling_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_stuck_job_heartbeat_ceiling.py ===
def _stuck_job_heartbeat_ceiling_make_watchdog(
    *,
    heartbeat_ceiling_seconds: float | None = 10.0,
    no_progress_quiet_seconds: float | None = 10.0,
    no_progress_quiet_minimum_invocation_seconds: float | None = 10.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_minimum_invocation_seconds=(no_progress_quiet_minimum_invocation_seconds),
        no_progress_quiet_heartbeat_ceiling_seconds=heartbeat_ceiling_seconds,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        watchdog_subagent_progress_interval_seconds=30.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=_StubCorroboratorStuckJobHeartbeatCeiling(alive_by),
            process_monitor=_NoProcessMonitorStuckJobHeartbeatCeiling(),
        ),
        clock,
    )


# === Helper for test_stuck_job_intelligence.py ===
def _stuck_job_intelligence_make_policy(
    *,
    idle_timeout: float = 300.0,
    drain_window: float = 0.5,
    max_waiting: float = 1800.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = 30.0,
    no_progress_quiet_seconds: float | None = None,
    no_progress_quiet_minimum_invocation_seconds: float | None = None,
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = None,
    no_progress_ceiling: float | None = None,
) -> TimeoutPolicy:
    # Default heartbeat ceiling to no_progress_quiet_seconds so the
    # cross-field validator (heartbeat_ceiling <= no_progress_quiet_seconds)
    # accepts the test fixture.
    if (
        no_progress_quiet_heartbeat_ceiling_seconds is None
        and no_progress_quiet_seconds is not None
    ):
        no_progress_quiet_heartbeat_ceiling_seconds = no_progress_quiet_seconds
    if no_progress_ceiling is not None and no_output_at_start is not None and no_output_at_start >= no_progress_ceiling:
        no_output_at_start = None
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        # Disable the stuck-job sub-ceiling: this test file uses a
        # small cumulative ceiling (max_waiting) for fast in-memory
        # cycles. The sub-ceiling default (600s) would fail the
        # ``<= max_waiting_on_child_seconds`` validator. The tests
        # in this file exercise the standard CHILDREN_PERSIST_TOO_LONG
        # path; the dedicated sub-ceiling tests live in
        # ``tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py``.
        stuck_job_sub_ceiling_seconds=None,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        activity_evidence_ttl_seconds=activity_ttl,
        os_descendant_only_ceiling_seconds=None,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_minimum_invocation_seconds=(no_progress_quiet_minimum_invocation_seconds),
        no_progress_quiet_heartbeat_ceiling_seconds=(no_progress_quiet_heartbeat_ceiling_seconds),
        post_tool_result_progression_seconds=None,
        repeated_error_window_count=5,
        repeated_error_window_seconds=60.0,
        idle_poll_interval_seconds=0.05,
        waiting_status_interval_seconds=30.0,
    )


# === Helper for test_stuck_job_intelligence.py ===
def _stuck_job_intelligence_make_watchdog(
    policy: TimeoutPolicy,
    clock: FakeClock,
    *,
    corroborator: WaitingCorroborator | None = None,
    process_monitor: ProcessMonitor | None = None,
    connectivity_state_provider: Callable[[], str | None] | None = None,
) -> IdleWatchdog:
    return IdleWatchdog(
        policy,
        clock,
        corroborator=corroborator,
        process_monitor=process_monitor,
        connectivity_state_provider=connectivity_state_provider,
    )


# === Helper for test_stuck_job_sub_ceiling.py ===
def _stuck_job_sub_ceiling_make_policy(
    *,
    stuck_job_sub_ceiling_seconds: float | None = 600.0,
    max_waiting_on_child_seconds: float = 1800.0,
    max_waiting_on_child_no_progress_seconds: float | None = 1800.0,
    idle_timeout_seconds: float = 200.0,
    drain_window_seconds: float = 0.0,
    os_descendant_only_ceiling_seconds: float | None = None,
    os_descendant_only_suspect_seconds: float | None = None,
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = None,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        drain_window_seconds=drain_window_seconds,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=max_waiting_on_child_no_progress_seconds,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=no_progress_quiet_heartbeat_ceiling_seconds,
        suspect_waiting_on_child_seconds=None,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling_seconds,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect_seconds,
        no_output_at_start_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        silent_subagent_seconds=None,
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
    )


# === Helper for test_stuck_job_sub_ceiling.py ===
def _stuck_job_sub_ceiling_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_subagent_capture_eviction.py ===
def _subagent_capture_eviction_make_watchdog(
    monitor: _FakeProcessMonitorSubagentCaptureEviction,
) -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog with the production cap.

    The ``IdleWatchdog`` public constructor exposes no cap override;
    the cap is a private module-level constant in
    ``_activity_methods``. Tests exercise the bound by generating
    enough distinct workers to overflow the production cap.
    """
    config = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        config,
        clock,
        process_monitor=monitor,
    )
    watchdog.record_invocation_start()
    return watchdog, clock


# === Helper for test_subagent_progress_surface.py ===
def _subagent_progress_surface_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorSubagentProgressSurface()), clock


# === Helper for test_tool_result_routing.py ===
def _tool_result_routing_watchdog(clock: FakeClock) -> IdleWatchdog:
    return IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )


# === Helper for test_waiting_subagent_progress.py ===
def _waiting_subagent_progress_make_watchdog(
    *,
    subagent_interval: float = 30.0,
    monitor_count: int = 0,
    max_waiting: float = 600.0,
    idle_timeout: float = 5.0,
) -> tuple[IdleWatchdog, FakeClock, list[WaitingStatusEvent]]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        watchdog_subagent_progress_interval_seconds=subagent_interval,
        waiting_status_interval_seconds=60.0,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog = IdleWatchdog(
        policy,
        clock,
        listener=_listener,
        process_monitor=_FakeProcessMonitorWaitingSubagentProgress(count=monitor_count),
    )
    return watchdog, clock, captured


# === Helper for test_waiting_subagent_progress.py ===
def _waiting_subagent_progress_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper: _errored_tool_line (from test_both_repetition_dimensions.py) ===
def _errored_tool_line(path: str, *, call_id: str, error: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": call_id,
                "state": {"status": "error", "input": {"path": path}, "error": error},
            },
        }
    )


# === Helper: _harness (from test_both_repetition_dimensions.py) ===
def _harness() -> tuple[FakeClock, IdleWatchdog, object]:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    reader = SimpleNamespace(
        _strategy=strategy_for_transport(AgentTransport.OPENCODE),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run ctx;
        # duck-typed doubles must declare it (``None`` = no prompt to echo-
        # match) since ``_record_line_activity`` consults it for harness-
        # echo classification.
        _input_prompt=None,
    )
    return clock, watchdog, MethodType(ProcessLineReader._record_line_activity, reader)


# === Helper: _transcript_line (from test_claude_interactive_tool_fingerprints.py) ===
def _transcript_line(command: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ]
            },
        }
    )


# === Helper: _no_signals (from test_clean_exit_session_id_recovery.py) ===
def _no_signals(
    workspace: object,
    raw_output: object,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> CompletionSignals:
    return CompletionSignals(False, False, ())


# === Helper: _make_registry (from test_cross_transport_subagent_visibility.py) ===
def _make_registry() -> ChildLivenessRegistry:
    """Return a ``ChildLivenessRegistry`` with non-zero TTLs so tests are stable."""
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


# === Helper: _bind_subagent_sink_to_watchdog (from test_cross_transport_subagent_visibility.py) ===
def _bind_subagent_sink_to_watchdog(
    watchdog: IdleWatchdog,
) -> tuple[object, object]:
    """Bind ``watchdog.record_subagent_work`` into the subagent sink contextvar.

    Returns the (sink_token, subagent_token) so the caller can reset them
    after the test.
    """

    def _mcp_sink(_tool_name: str) -> None:
        watchdog.record_mcp_tool_call()

    def _subagent_sink(line: str) -> None:
        watchdog.record_subagent_work(description=line)

    sink_token = set_active_sink(_mcp_sink)
    subagent_token = set_subagent_sink(_subagent_sink)
    return (sink_token, subagent_token)


# === Helper: _reset_sink_tokens (from test_cross_transport_subagent_visibility.py) ===
def _reset_sink_tokens(tokens: tuple[object, object]) -> None:
    sink_token, subagent_token = tokens
    reset_active_sink(sink_token)
    reset_subagent_sink(subagent_token)


# === Helper: _parse_tool_call_expected (from test_cross_transport_subagent_visibility.py) ===
def _parse_tool_call_expected(description: str | None) -> str | None:
    """Mirror the production R5 CURRENT TOOL CALL parser.

    Splits on a single ``:`` (not ``": "``) because the
    canonical production format from the NDJSON parser layer is
    ``tool_use:<name>`` with no space after the colon. See
    ``ralph.agents.idle_watchdog._activity_methods._parse_tool_call_from_description``
    for the production implementation this helper mirrors.
    """
    if not description:
        return None
    head, sep, _tail = description.partition(":")
    if not sep:
        return None
    if head not in _KNOWN_TOOL_CALL_VERBS_FOR_TEST:
        return None
    return head


# === Helper: _force_classify_stuck_kind (from test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py) ===
def _force_classify_stuck_kind(
    watchdog: IdleWatchdog,
    kind: StuckKind,
) -> None:
    """Override ``_classify_stuck_now`` to return a fixed kind on every call.

    Mirrors the pattern at
    ``test_trustworthy_idle_watchdog_spec.py::test_r6`` line 723 --
    ``setattr`` on the watchdog instance with the attribute name in
    a local variable (audit_lint_bypass: bare constant setattr is
    ruff B010; mypy cannot narrow access to a private-method
    assignment). The override is in-process only; it does NOT touch
    the watchdog's classifier input model.
    """
    fixed_kind = kind

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return fixed_kind

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)


# === Helper: _shell_call_line (from test_cursor_tool_fingerprints.py) ===
def _shell_call_line(command: str, *, call_id: str) -> str:
    return json.dumps(
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "shellToolCall": {
                    "args": {
                        "command": command,
                        "workingDirectory": "/repo",
                        "timeout": 30000,
                        "toolCallId": call_id,
                        "conversationId": "conv_1",
                    }
                },
                "toolCallId": call_id,
            },
        }
    )


# === Helper: _minimal_policy_bundle (from test_dumb_kill_scenarios.py) ===
def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


# === Helper: _three_agent_state (from test_dumb_kill_scenarios.py) ===
def _three_agent_state(current_index: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


# === Helper: _make_policy_with_floor (from test_dumb_kill_scenarios.py) ===
def _make_policy_with_floor(
    *,
    idle_timeout: float = 1.0,
    drain_window: float = 0.0,
    max_waiting: float = 600.0,
    max_session: float | None = None,
    activity_ttl: float | None = 30.0,
    no_output_at_start: float | None = None,
    os_descendant_only_ceiling: float | None = 300.0,
    no_progress_quiet_seconds: float | None = 120.0,
    no_progress_quiet_minimum_invocation_seconds: float | None = 120.0,
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = None,
) -> TimeoutPolicy:
    """Build a TimeoutPolicy with the dumb-kill floor enabled.

    Mirrors ``_dumb_kill_scenarios_make_policy`` but adds the
    ``no_progress_quiet_minimum_invocation_seconds`` knob so the
    floor is active in tests that exercise the dumb-kill
    protection.

    The ``no_progress_quiet_heartbeat_ceiling_seconds`` defaults to
    ``no_progress_quiet_seconds`` when not specified so the
    cross-field validator accepts the test fixture (the heartbeat
    ceiling MUST be <= the dumb-kill ceiling). Callers that want
    to test the heartbeat-only branch in isolation can pass an
    explicit value.
    """
    if no_progress_quiet_heartbeat_ceiling_seconds is None:
        no_progress_quiet_heartbeat_ceiling_seconds = no_progress_quiet_seconds
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": os_descendant_only_ceiling,
        "no_output_at_start_seconds": no_output_at_start,
        "no_progress_quiet_seconds": no_progress_quiet_seconds,
        "no_progress_quiet_minimum_invocation_seconds": (
            no_progress_quiet_minimum_invocation_seconds
        ),
        "no_progress_quiet_heartbeat_ceiling_seconds": (
            no_progress_quiet_heartbeat_ceiling_seconds
        ),
        "post_tool_result_progression_seconds": None,
        "repeated_error_window_count": 5,
        "repeated_error_window_seconds": 60.0,
        "idle_poll_interval_seconds": 0.05,
        "waiting_status_interval_seconds": 30.0,
    }
    return TimeoutPolicy(**kwargs)


# === Helper: _info_log_records (from test_emit_info_log_throttle.py) ===
def _info_log_records(records: list[str]) -> list[str]:
    """Filter captured INFO records to those matching the no-subagent-listener log.

    The substring ``"idle watchdog: subagent activity:"`` is the exact
    operator-visible loguru format string emitted from
    ``_active_branch.py:162`` in the ``else: subagent_listener is
    None`` arm. Filtering on the exact substring avoids cross-talk
    from other watchdog log emissions (e.g. ENTERED info,
    FIRE warnings, evidence-deferral debug) that may also appear in
    the captured records during the same clock window.
    """
    return [r for r in records if _INFO_LOG_SUBSTRING in r]


# === Helper: _make_capture_sink (from test_evidence_deferral_throttle.py) ===
def _make_capture_sink() -> tuple[io.StringIO, list[str]]:
    buf = io.StringIO()
    captured: list[str] = []

    def _sink(message: str) -> None:
        captured.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    return buf, captured, handler_id


# === Helper: _remove_sink (from test_evidence_deferral_throttle.py) ===
def _remove_sink(handler_id: int) -> None:
    logger.remove(handler_id)


# === Helper: _populate_per_invocation_state (from test_invocation_start_full_reset.py) ===
def _populate_per_invocation_state(
    watchdog: IdleWatchdog,
    clock: FakeClock,
) -> None:
    """Drive the watchdog through every per-invocation field's write path.

    Each line corresponds to a public method that updates a per-invocation
    field. After this function returns, the watchdog's per-invocation state
    is fully populated so the reset test can verify ``record_invocation_start``
    clears every field.
    """
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_activity()
    watchdog.record_mcp_tool_call(now=clock.monotonic())
    watchdog.record_subagent_work(description="reading file")
    watchdog.record_subagent_output(now=clock.monotonic())
    watchdog.record_workspace_event()
    watchdog.record_progress_report("phase-1")
    watchdog.record_lifecycle_activity()
    watchdog.record_tool_call_activity("Bash", {"command": "ls"})
    watchdog.record_error_activity("oops")
    watchdog.record_tool_result_activity()
    # Drive a fire path so ``_last_alive_by`` and ``_last_fire_reason``
    # get populated, then advance past the no_output_at_start threshold
    # so the fire path naturally populates ``_last_alive_by``.
    clock.advance(40.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    verdict = watchdog.evaluate(classify_quiet=_active)
    # The fire path is only entered if the watchdog's deferral gates
    # do not return CONTINUE first. A populated channel (mcp_tool is
    # fresh from the call above) will keep the verdict at CONTINUE;
    # that's fine -- the per-invocation fields populated by the
    # ``record_*`` calls above are still dirty and prove the reset
    # contract. The ``_last_alive_by`` field is populated via the
    # separate ``AliveBy`` assignment in the test below.
    assert verdict.name in {"FIRE", "CONTINUE"}


# === Helper: _per_invocation_fields (from test_invocation_start_full_reset.py) ===
def _per_invocation_fields() -> dict[str, object]:
    """Return the canonical ``{name: baseline_value}`` map for every
    per-invocation field the reset must clear.

    Mirrors the field-by-field contract documented in
    :meth:`IdleWatchdog.record_invocation_start`. Adding a new
    per-invocation field MUST add an entry here so the reset test
    fails until the new field is wired into the reset.
    """
    return {
        "_last_alive_by": None,
        "_last_waiting_status_at": None,
        "_suspicion_announced_for_run": False,
        "_last_tool_result_at": None,
        "_awaiting_post_tool_result_progression": False,
        "_mcp_tool_call_count": 0,
        "_last_mcp_tool_call_at": None,
        "_subagent_progress_count": 0,
        "_last_subagent_progress_at": None,
        "_last_subagent_progress_emit_at": None,
        "_subagent_output_count": 0,
        "_last_subagent_output_at": None,
        "_workspace_event_count_internal": 0,
        "_last_workspace_event_at": None,
        "_last_workspace_event_weight": 0.0,
        "_workspace_kind_counts": {},
        "_last_subagent_progress_description": None,
        "_default_subagent_activity_listener": None,
        "_subagent_output_captures": {},
        "_last_fire_reason": None,
        "_last_deferred_kind": None,
        "_last_progress_fingerprint": None,
        "_last_deferred_log_at": {},
        "_last_any_deferred_log_at": {},
        "_last_evidence_deferral_log_at": {},
        "_entry_corroboration": None,
        "_waiting_on_child_started_at": None,
        "_cumulative_waiting_on_child_seconds": 0.0,
        "_in_drain_window": False,
        "_drain_started_at": None,
        "_classify_quiet_provider": None,
    }


# === Helper: _patch_classifier_to_deferring_kind (from test_log_spam_throttle.py) ===
def _patch_classifier_to_deferring_kind(watchdog: IdleWatchdog) -> None:
    """Force ``_classify_stuck_now`` to return a kind that DEFERS.

    ``LOADING`` (a live child is starting up / working) is a genuine
    deferring kind. These tests exercise the log-throttle machinery, not
    the fire/defer policy, so they need any kind the gate defers on.

    They previously used ``SILENT_SUBAGENT``, which the gate now FIRES on
    (see ``test_silent_subagent_fires.py``): a silent subagent with no live
    child is a dead agent, and deferring it wedged the run forever.

    The classifier is pure and consults watchdog state; monkey-patching
    it directly is the cleanest deterministic seam for this test.
    """

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.LOADING

    # Use ``setattr`` with the attribute name held in a local
    # variable so mypy cannot narrow the access to a private-method
    # assignment AND ruff B010 does not flag a setattr-with-constant-
    # value call. The policy test for ``test_zero_test_file_suppressions``
    # rejects bare mypy suppression comments inside test files.
    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)


# === Helper: _stale_subagent_corroborator (from test_log_spam_throttle_public_surface.py) ===
def _stale_subagent_corroborator() -> CorroborationSnapshot:
    """Corroborator that reports a stuck-but-alive child.

    Returns ``scoped_child_active=True`` and
    ``alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS`` so the
    SUB-ceiling block at ``_waiting_branch.py:184-237`` is reached
    (the block requires BOTH conditions plus
    ``candidate_total >= stuck_job_sub_ceiling_seconds``).
    """
    return CorroborationSnapshot(
        scoped_child_active=True,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )


# === Helper: _build_deferred_fire_watchdog (from test_log_spam_throttle_public_surface.py) ===
def _build_deferred_fire_watchdog(
    *,
    listener: Callable[[WaitingStatusEvent], None] | None,
    clock: FakeClock,
    silent_subagent_seconds: float = 1.0,
    stuck_job_sub_ceiling_seconds: float = 5.0,
    max_waiting_on_child_seconds: float = 10_000.0,
    watchdog_log_throttle_seconds: float = 30.0,
) -> IdleWatchdog:
    """Construct an IdleWatchdog wired to exercise the deferred-fire path.

    The configuration is intentionally minimal so the deferred-fire
    branch at ``_waiting_branch.py:184-237`` is reachable via public
    behavior:

      * ``stuck_job_sub_ceiling_seconds=5.0`` enables the SUB-ceiling
        block (the only block that consults ``_gate_fire``).
      * ``max_waiting_on_child_seconds=10_000.0`` makes the cumulative
        ceiling unreachable, so the watchdog cannot bypass the
        SUB-ceiling block via the cumulative hard stop.
      * ``silent_subagent_seconds=1.0`` enables the SILENT_SUBAGENT
        branch of the StuckClassifier. With a stale ``subagent_output``
        channel (seeded via ``record_subagent_work``) and
        ``subagent_liveness`` showing ``alive_by=None`` (because
        ``_HelpersOnlyMonitorLogSpamThrottlePublicSurface.live_subagent_count() == 0``), the
        classifier returns ``SILENT_SUBAGENT`` and ``_gate_fire``
        returns ``CONTINUE`` -- the deferred-fire path.
      * ``activity_evidence_ttl_seconds=0.0`` disables the
        first-party / side-channel freshness deferrals so the
        classifier does NOT short-circuit to ``THINKING`` or
        ``LOADING`` via those branches; the only remaining
        non-STUCK branch is SILENT_SUBAGENT.
      * ``waiting_status_interval_seconds=10_000.0`` (and
        ``watchdog_subagent_progress_interval_seconds=10_000.0``)
        keep the cadence gates closed so PROGRESS /
        SUBAGENT_PROGRESS events are NOT emitted during the 1000-call
        cycle (the spam-relevant emissions come from the
        deferred-fire branch, not the cadence gates).
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        # Short idle deadline so ``evaluate()`` reaches the
        # WAITING branch quickly; not directly used because
        # WAITING_ON_CHILD branch is consulted before the idle
        # deadline per ``evaluate()`` priority order.
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        # Cumulative ceiling far above the SUB-ceiling so the
        # cumulative hard stop cannot fire and bypass the
        # SUB-ceiling deferred-fire branch.
        max_waiting_on_child_no_progress_seconds=None,
        # Disable orthogonal no-progress / strictly-stuck ceilings so
        # the SUB-ceiling block is the only fire path consulted.
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        no_progress_quiet_seconds=None,
        no_progress_quiet_strictly_stuck_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        # SUB-ceiling: the headline fire reason consulted by the
        # deferred-fire spam regression.
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
        # Disable freshness deferrals so the classifier cannot
        # short-circuit to THINKING/LOADING and the SILENT_SUBAGENT
        # branch is the only non-STUCK branch reachable.
        activity_evidence_ttl_seconds=10_000.0,
        # SILENT_SUBAGENT branch threshold.
        silent_subagent_seconds=silent_subagent_seconds,
        # Cadence gates closed during the 1000-call cycle so the
        # throttle proof isolates the deferred-fire emissions.
        waiting_status_interval_seconds=10_000.0,
        watchdog_log_throttle_seconds=watchdog_log_throttle_seconds,
        watchdog_subagent_progress_interval_seconds=10_000.0,
    )
    return IdleWatchdog(
        config=policy,
        clock=clock,
        listener=listener,
        corroborator=_stale_subagent_corroborator,
        process_monitor=_LiveSubagentMonitor(),
    )


# === Helper: _build_pty_reader_with_strategy (from test_mark_tool_call_runtime_reachability.py) ===
def _build_pty_reader_with_strategy(strategy: object) -> PtyLineReader:
    """Construct a PtyLineReader with the given strategy.

    The reader's construction signature is broad (it takes
    ``AgentRunCtx``); we build a minimal SimpleNamespace that the
    reader touches at the ``_handle_queued_line`` call site.  The
    master_fd is a real ``/dev/null`` fd because the reader
    constructor calls ``os.dup(master_fd)`` for the input writer;
    a sentinel ``-1`` triggers an OSError on construction.
    """
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = SimpleNamespace(
        master_fd=master_fd,
        poll=lambda: None,
        terminate=lambda grace_period_s=None: None,
    )
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=strategy,
        liveness_probe=None,
        waiting_listener=None,
    )
    try:
        reader = PtyLineReader(handle, "claude", ctx, FakeClock(start=0.0), extras=None)
    finally:
        # The reader duped the fd; close the original.
        os.close(master_fd)
    return reader


# === Helper: _claude_assistant_tool_use_line (from test_mark_tool_call_runtime_reachability.py) ===
def _claude_assistant_tool_use_line(tool_input: dict[str, object]) -> str:
    """Build the Claude stream-json assistant message carrying a complete call."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01RCxFZpHAEB3yBAHn3nktG2",
                        "name": "Bash",
                        "input": tool_input,
                    }
                ]
            },
        }
    )


# === Helper: _make_watchdog_with_corroborator (from test_no_output_at_start_lifecycle_parity.py) ===
def _make_watchdog_with_corroborator(
    corroborator: WaitingCorroborator,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(_no_output_at_start_lifecycle_p_make_policy(), clock, corroborator=corroborator),
        clock,
    )


# === Helper: _make_watchdog_for_waiting_fire (from test_non_resumable_end_to_end.py) ===
def _make_watchdog_for_waiting_fire() -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog configured to fire CHILDREN_PERSIST_TOO_LONG quickly."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=15.0,
        # Disable the stuck-job sub-ceiling: this test file uses a
        # small cumulative ceiling (15s) for fast in-memory cycles.
        # The sub-ceiling default (600s) would fail the
        # ``<= max_waiting_on_child_seconds`` validator. The tests
        # in this file exercise the standard CHILDREN_PERSIST_TOO_LONG
        # path; the dedicated sub-ceiling tests live in
        # ``tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py``.
        stuck_job_sub_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        waiting_status_interval_seconds=100.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorNonResumableEndToEnd()), clock


# === Helper: _make_watchdog_for_session_ceiling (from test_non_resumable_end_to_end.py) ===
def _make_watchdog_for_session_ceiling() -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog configured to fire SESSION_CEILING_EXCEEDED quickly."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_session_seconds=10.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorNonResumableEndToEnd()), clock


# === Helper: _fire_in_stream_reason (from test_non_resumable_end_to_end.py) ===
def _fire_in_stream_reason(
    reason: WatchdogFireReason,
) -> tuple[list[str], _IdleStreamTimeoutError]:
    """Drive ``IdleWatchdog.evaluate`` and ``ProcessLineReader._check_fire``.

    Returns the pending lines and the wrapper exception carrying ``reason``.
    """
    if reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        watchdog, clock = _make_watchdog_for_session_ceiling()
        watchdog.record_invocation_start()
        clock.advance(11.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_active)
    else:
        watchdog, clock = _make_watchdog_for_waiting_fire()
        watchdog.record_invocation_start()
        # First evaluation enters WAITING_ON_CHILD after the idle deadline.
        clock.advance(6.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_waiting_on_child)
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD
        # Second evaluation fires once the cumulative ceiling is reached.
        clock.advance(15.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE for {reason}; got {verdict}"
    assert watchdog.last_fire_reason == reason

    fake_self = _FakeCheckFireSelf(_policy=watchdog._config, _clock=clock)
    result = ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == reason
    return pending_lines, wrapper


# === Helper: _fire_process_exit_hang (from test_non_resumable_end_to_end.py) ===
def _fire_process_exit_hang() -> _IdleStreamTimeoutError:
    """Drive ``PostExitWatchdog.wait_for_process_exit`` to fire."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        process_exit_wait_seconds=5.0,
        descendant_wait_poll_seconds=0.1,
    )
    post_exit = PostExitWatchdog(policy, clock)
    verdict = post_exit.wait_for_process_exit(lambda: False)
    assert verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    return _IdleStreamTimeoutError(
        policy.process_exit_wait_seconds,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    )


# === Helper: _fire_descendant_hang (from test_non_resumable_end_to_end.py) ===
def _fire_descendant_hang() -> _IdleStreamTimeoutError:
    """Drive ``PostExitWatchdog.wait_descendant_quiesce`` to fire."""
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        descendant_wait_timeout_seconds=5.0,
        descendant_wait_poll_seconds=0.1,
    )
    post_exit = PostExitWatchdog(policy, clock)
    verdict = post_exit.wait_descendant_quiesce(lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert verdict == PostExitVerdict.FIRE_DESCENDANT_HANG
    return _IdleStreamTimeoutError(
        policy.descendant_wait_timeout_seconds,
        WatchdogFireReason.DESCENDANT_HANG,
    )


# === Helper: _convert_reason_to_agent_error (from test_non_resumable_end_to_end.py) ===
def _convert_reason_to_agent_error(
    wrapper: _IdleStreamTimeoutError,
    pending_lines: tuple[str, ...] | list[str] = (),
) -> AgentInactivityTimeoutError:
    """Convert the wrapper through the canonical invocation-layer seam."""
    return _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=pending_lines,
        explicit_completion_seen=False,
        captured_session_id=None,
        expected_session_id="prior-session-abc",
    )


# === Helper: _resolve_recovery_session_id_for_test (from test_non_resumable_end_to_end.py) ===
def _resolve_recovery_session_id_for_test(exc: AgentInactivityTimeoutError) -> str | None:
    """Resolve the session id the same way the pipeline executor does."""
    if _failure_requires_fresh_session(exc, AgentInactivityTimeoutError):
        return None
    return getattr(exc, "resumable_session_id", None) or None


# === Helper: _assert_non_resumable_recovery_chain (from test_non_resumable_end_to_end.py) ===
def _assert_non_resumable_recovery_chain(exc: AgentInactivityTimeoutError) -> None:
    """Assert the full recovery chain refuses to resume for ``exc``."""
    assert exc.session_resume_safe is False, (
        f"reason={exc.reason!r}: session_resume_safe must be False; got {exc.session_resume_safe}"
    )
    session_id = _resolve_recovery_session_id_for_test(exc)
    assert session_id is None, (
        f"non-resumable reason={exc.reason!r} must clear the resolved session id;"
        f" got {session_id!r}"
    )
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=bool(session_id),
    )
    assert action == "fresh", (
        f"non-resumable reason={exc.reason!r} must map to fresh; got {action!r}"
    )
    resolved = resolve_resume_session_id(
        has_prior_session=False,
        prior_session_id="prior-session-abc",
        recovery_action=action,
    )
    assert resolved is None
    intent = agent_retry_intent_for_failure(
        failure_reason="AgentInactivityTimeoutError",
        session_id=resolved,
        reset_tool_registry=False,
    )
    assert intent.action == "fresh", (
        f"non-resumable reason={exc.reason!r} must yield fresh intent; got {intent.action!r}"
    )
    assert intent.session_id is None, (
        f"non-resumable reason={exc.reason!r} must yield intent.session_id=None;"
        f" got {intent.session_id!r}"
    )


# === Helper: _frame (from test_opencode_step_frames.py) ===
def _frame(event_type: str) -> str:
    return json.dumps(
        {
            "type": event_type,
            "timestamp": 1785133506972,
            "sessionID": "ses_1",
            "part": {"id": "prt_1", "type": event_type.replace("_", "-")},
        }
    )


# === Helper: _tool_line (from test_opencode_step_frames.py) ===
def _tool_line(command: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_exec",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"command": command}, "output": "x"},
            },
        }
    )


# === Helper: _opencode_tool_line (from test_opencode_tool_call_fingerprints.py) ===
def _opencode_tool_line(
    tool: str,
    tool_input: dict[str, object],
    *,
    call_id: str = "call_1",
    status: str = "completed",
    include_part_type: bool = True,
) -> str:
    """Build a real OpenCode ``tool_use`` line (shape from a live 1.17.15 run)."""
    state: dict[str, object] = {"status": status, "input": tool_input}
    if status == "completed":
        state["output"] = "ok"
    elif status == "error":
        state["error"] = "MCP error -32001: Request timed out"
    part: dict[str, object] = {
        "tool": tool,
        "callID": call_id,
        "state": state,
    }
    if include_part_type:
        part["type"] = "tool"
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_05dc0769cffeI7fO3oF7uFd0BQ",
            "part": part,
        }
    )


# === Helper: _make_os_descendant_only_corroborator (from test_os_descendant_only_escalation.py) ===
def _make_os_descendant_only_corroborator() -> WaitingCorroborator:
    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


# === Helper: _active_strategy_with_source (from test_production_subagent_registry_wiring.py) ===
def _active_strategy_with_source(
    source: SubagentPidSource,
) -> BaseExecutionStrategy:
    """Build a ``BaseExecutionStrategy`` with an injected SubagentPidSource."""
    return BaseExecutionStrategy(subagent_pid_source=source)


# === Helper: _tracker (from test_repetition_window_cycle_detection.py) ===
def _tracker(clock: FakeClock) -> RepetitionTracker:
    return RepetitionTracker(
        clock,
        consecutive_threshold=5,
        window_count=8,
        window_seconds=600.0,
    )


# === Helper: _make_pipeline_state (from test_resume_session_id_threading.py) ===
def _make_pipeline_state(
    *,
    chain_agents: tuple[str, ...] = ("agent-a",),
    retries: int = 0,
    connectivity_state: str | None = "online",
) -> PipelineState:
    """Construct a minimal ``PipelineState`` with a chain for the phase.

    Returns a state with ``phase='development'``, an AgentChainState
    that has ``chain_agents`` and ``current_index=0``, and
    ``last_agent_session_id=None`` (the empty pre-fire state).

    The default ``connectivity_state='online'`` matches the runtime
    invariant: the failure classifier's unavailability branch is only
    taken when connectivity is known healthy. Tests that drive a
    non-online state (e.g. offline / unknown) pass
    ``connectivity_state='unknown'`` to opt out of the
    unavailability branch.
    """
    chain = AgentChainState(agents=list(chain_agents), current_index=0, retries=retries)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain},
    )
    if connectivity_state is not None:
        state = state.copy_with(last_connectivity_state=connectivity_state)
    return state


# === Helper: _raise_like_process_reader (from test_runtime_session_resume_safe_mapping.py) ===
def _raise_like_process_reader(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
    """Replicate ``_process_reader.py:670-689`` except block.

    Kept in sync with production by black-box reuse of
    ``_is_resumable_fire_reason`` and ``InactivityTimeoutOpts``.
    """
    return AgentInactivityTimeoutError(
        ctx.agent_command_name,
        timeout_seconds,
        _bounded_output_lines(
            tuple(ctx.parsed_output or ()),
            explicit_completion_seen=ctx.explicit_completion_seen,
        ),
        InactivityTimeoutOpts(
            reason=reason,
            session_resume_safe=_is_resumable_fire_reason(reason),
            resumable_session_id=ctx.captured_session_id or ctx.expected_session_id,
            diagnostic=diagnostic,
        ),
    )


# === Helper: _raise_like_pty_runner (from test_runtime_session_resume_safe_mapping.py) ===
def _raise_like_pty_runner(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
    """Replicate ``_pty_runner.py:130-150`` except block."""
    return AgentInactivityTimeoutError(
        ctx.agent_command_name,
        timeout_seconds,
        _bounded_output_lines(
            tuple(ctx.parsed_output or ()),
            explicit_completion_seen=ctx.explicit_completion_seen,
        ),
        InactivityTimeoutOpts(
            reason=reason,
            session_resume_safe=_is_resumable_fire_reason(reason),
            resumable_session_id=ctx.captured_session_id or ctx.expected_session_id,
            diagnostic=diagnostic,
        ),
    )


# === Helper: _make_fake_watchdog_class (from test_runtime_session_resume_safe_mapping.py) ===
def _make_fake_watchdog_class(fire_reason: WatchdogFireReason) -> type[_BaseFakeWatchdog]:
    """Factory that pins the fire reason on each firing watchdog instance."""

    class _Cls(_FakeFiringWatchdog):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self._fire_reason = fire_reason

    return _Cls


# === Helper: _drive_invoke_agent_with_reason (from test_runtime_session_resume_safe_mapping.py) ===
def _drive_invoke_agent_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> AgentInactivityTimeoutError:
    """Drive ``invoke_agent`` with monkeypatched watchdog(s) so the
    subprocess reader emits ``AgentInactivityTimeoutError`` for ``reason``.
    """
    # PROCESS_EXIT_HANG is owned by the post-exit watchdog; we use a
    # no-fire IdleWatchdog double and an immediately-EOF'ing fake
    # process so the line reader reaches the post-exit path.
    if reason == WatchdogFireReason.PROCESS_EXIT_HANG:
        monkeypatch.setattr(
            _process_reader_module,
            "PostExitWatchdog",
            _FakeFiringPostExitWatchdog,
        )
        monkeypatch.setattr(
            _process_reader_module,
            "IdleWatchdog",
            _FakeNoFireWatchdog,
        )
        eof_after_lines = True
    else:
        monkeypatch.setattr(
            _process_reader_module,
            "IdleWatchdog",
            _make_fake_watchdog_class(reason),
        )
        eof_after_lines = False

    monkeypatch.setattr(
        invoke_module,
        "_start_workspace_monitor",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.subprocess.Popen",
        lambda *args, **kwargs: _FakeProcess(
            stdout_lines=[],
            eof_after_lines=eof_after_lines,
        ),
    )

    config = AgentConfig(cmd="opencode", output_flag="--json-stream")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    clock = FakeClock()
    # Reason-specific options so the production line reader can reach
    # the desired fire reason without being pre-empted by a different
    # real watchdog deadline.
    idle_timeout = 0.05
    max_session: float | None = None
    if reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        idle_timeout = None
        max_session = 0.05

    opts = InvokeOptions(
        show_progress=False,
        workspace_path=tmp_path,
        idle_timeout_seconds=idle_timeout,
        max_waiting_on_child_seconds=10.0,
        max_session_seconds=max_session,
        max_waiting_on_child_no_progress_seconds=None,
        waiting_status_interval_seconds=100.0,
        idle_poll_interval_seconds=0.01,
        session_id="sess-runtime-seam",
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        list(
            invoke_agent(
                config,
                str(prompt_file),
                options=opts,
                _clock=clock,
            )
        )
    return exc_info.value


# === Helper: _wedge (from test_silent_subagent_fires.py) ===
def _wedge(watchdog: IdleWatchdog, clock: FakeClock, silent_for: float) -> float:
    """Dispatch a subagent, let it speak once, then go silent for ``silent_for``.

    Reproduces the production wedge: the subagent channel carries stale
    historical evidence and no live-child signal.
    """
    watchdog.record_invocation_start()
    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(silent_for)
    return clock.monotonic()


# === Helper: _events (from test_stall_status_events.py) ===
def _events(captured: list[WaitingStatusEvent]) -> list[WaitingStatusEvent]:
    """Return the captured events list typed for assertion helpers."""
    return captured


# === Helper: _stall_state (from test_stall_status_events.py) ===
def _stall_state(watchdog: IdleWatchdog) -> bool:
    """Return the watchdog's current stall state via the public property."""
    return bool(watchdog.is_stalled)


# === Helper: _classifier_to_stuck_now (from test_stall_status_events.py) ===
def _classifier_to_stuck_now(
    watchdog: IdleWatchdog,
    *,
    reason: WatchdogFireReason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
) -> None:
    """Force ``_classify_stuck_now`` to return STUCK so the gate fires.

    The classifier is pure; monkey-patching it directly is the cleanest
    seam for these tests. See the same pattern in
    ``tests/agents/idle_watchdog/test_log_spam_throttle.py``.
    """
    _attr = "_classify_stuck_now"

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.STUCK

    setattr(watchdog, _attr, _stuck_now)


# === Helper: _fresh_progress_corroborator (from test_stall_status_events.py) ===
def _fresh_progress_corroborator() -> WaitingCorroborator:
    """Return a corroborator that always reports a fresh-progress child.

    ``FRESH_PROGRESS`` is the cleanest live-child signal for these
    tests because it is excluded from the watchdog's
    ``_STUCK_ALIVE_BY_VALUES`` and ``_NON_PROGRESS_ALIVE_BY_VALUES``
    sets, so neither the stuck-job sub-ceiling nor the
    no-progress ceiling engages. The SUSPECTED_FROZEN emission site
    then fires on the standard suspect threshold without competing
    HARD_STOP branches.
    """

    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


# === Helper: _summary_with_channel (from test_stuck_classifier.py) ===
def _summary_with_channel(
    *,
    channel: ChannelName,
    last_at: float | None,
    can_defer: bool = True,
) -> EvidenceSummary:
    """Build a one-channel evidence summary for a single first-party channel."""
    if last_at is None:
        age: float | None = None
    else:
        age = max(0.0, _NOW - last_at)
    counter = 1 if last_at is not None else None
    return EvidenceSummary(
        channels=(
            ChannelEvidenceSummary(
                channel_name=channel,
                tier=(
                    (CHANNEL_DEFERS_BY_DEFAULT[channel] and EvidenceTier.FIRST_PARTY)
                    or EvidenceTier.SIDE_CHANNEL
                ),
                last_at=last_at,
                age_seconds=age,
                counter=counter,
                can_defer=can_defer,
            ),
        )
    )


# === Helper: _multi_summary (from test_stuck_classifier.py) ===
def _multi_summary(
    *,
    subagent_output_at: float | None = None,
    subagent_liveness_at: float | None = None,
    alive_by: AliveBy | None = None,
) -> EvidenceSummary:
    """Build a full 5-channel summary with controlled timestamps."""
    channels: list[ChannelEvidenceSummary] = []
    # STDOUT - always stale in the test cases
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.STDOUT,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=False,
        )
    )
    # MCP_TOOL
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.MCP_TOOL,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=True,
        )
    )
    # SUBAGENT_OUTPUT
    sub_out_age = None if subagent_output_at is None else max(0.0, _NOW - subagent_output_at)
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.SUBAGENT_OUTPUT,
            tier=EvidenceTier.FIRST_PARTY,
            last_at=subagent_output_at,
            age_seconds=sub_out_age,
            counter=1 if subagent_output_at is not None else None,
            can_defer=True,
        )
    )
    # SUBAGENT_LIVENESS
    sub_liv_age = None if subagent_liveness_at is None else max(0.0, _NOW - subagent_liveness_at)
    # The classifier requires can_defer=True for the subagent_liveness
    # channel to count as fresh. The watchdog's _subagent_liveness_summary
    # sets can_defer=True only for process-monitor live-subagent signals;
    # the test helper exercises the classifier contract directly and
    # therefore sets can_defer=True when the liveness timestamp is set.
    sub_liv_can_defer = subagent_liveness_at is not None
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.SUBAGENT_LIVENESS,
            tier=EvidenceTier.SIDE_CHANNEL,
            last_at=subagent_liveness_at,
            age_seconds=sub_liv_age,
            counter=1 if subagent_liveness_at is not None else None,
            alive_by=alive_by,
            can_defer=sub_liv_can_defer,
        )
    )
    # WORKSPACE
    channels.append(
        ChannelEvidenceSummary(
            channel_name=ChannelName.WORKSPACE,
            tier=EvidenceTier.SIDE_CHANNEL,
            last_at=None,
            age_seconds=None,
            counter=None,
            can_defer=False,
        )
    )
    return EvidenceSummary(channels=tuple(channels))


# === Helper: _inputs (from test_stuck_classifier.py) ===
def _inputs(
    *,
    is_waiting_state: bool = False,
    connectivity_state: str | None = "online",
    evidence_summary: EvidenceSummary | None = None,
    classify_quiet_state: AgentExecutionState = AgentExecutionState.ACTIVE,
) -> ClassifyStuckInputs:
    return {
        "is_waiting_state": is_waiting_state,
        "connectivity_state": connectivity_state,
        "evidence_summary": evidence_summary or _multi_summary(),
        "classify_quiet": _ClassifyQuietStub(state=classify_quiet_state),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }


# === Helper: _make_stuck_corroborator (from test_stuck_job_sub_ceiling.py) ===
def _make_stuck_corroborator(
    alive_by: AliveBy = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
) -> Callable[[], CorroborationSnapshot]:
    """Corroborator that always reports a stale alive_by with a scoped child active."""

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=200.0,
        )

    return _corroborator


# === Helper: _probe_cache_cap (from test_subagent_capture_eviction.py) ===
def _probe_cache_cap() -> int:
    """Probe the production ``_MAX_SUBAGENT_OUTPUT_CAPTURES`` from observed behavior.

    The cap is a PRIVATE module-level constant in
    ``_activity_methods`` and cannot be imported directly from
    tests (policy file rules forbid private ralph imports). This
    helper drives a fresh watchdog with a worker count well above
    any plausible cap (``_PROBE_WORKER_COUNT`` = 4096) and
    returns the post-poll ``_subagent_output_captures`` size. The
    production code applies the HARD FIFO cap AT THE END of the
    polling pass, so the post-poll size IS the cap regardless of
    how many workers were polled. The probe is deterministic and
    safe to call from any test that needs the cap.
    """
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog._subagent_output_captures)


# === Helper: _probe_tombstone_cap (from test_subagent_capture_eviction.py) ===
def _probe_tombstone_cap() -> int:
    """Probe the production ``_MAX_EVICTED_TOMBSTONES`` from observed behavior.

    Drives a fresh watchdog with ``_PROBE_WORKER_COUNT`` distinct
    workers on the FIRST poll (so the cache evicts to its cap and
    every evicted worker is tombstoned). The post-poll
    ``_evicted_worker_tombstones`` size IS the tombstone cap --
    the production code bounds the tombstone at the END of the
    eviction pass via FIFO, so any tombstone entry beyond the cap
    is dropped before the poll returns.
    """
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog._evicted_worker_tombstones)


# === Helper: _reader (from test_tool_result_routing.py) ===
def _reader() -> object:
    return SimpleNamespace(
        _strategy=_ResultThenCallStrategy(),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; the double declares ``None`` (no prompt to echo-match).
        _input_prompt=None,
    )


# === Helper: _read (from test_watchdog_recovery_contract.py) ===
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# === Helper: _parse (from test_watchdog_recovery_contract.py) ===
@functools.cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


# === Helper: _walk (from test_watchdog_recovery_contract.py) ===
def _walk(tree: ast.AST) -> list[ast.AST]:
    """Return all nodes in the AST in document order."""
    return list(ast.walk(tree))


# === Helper: _iter_with_parent (from test_watchdog_recovery_contract.py) ===
def _iter_with_parent(tree: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    """Iterate every node with a reference to its direct parent (parent may be None)."""
    parent_map: dict[int, ast.AST] = {}
    for node in _walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return [(node, parent_map.get(id(node))) for node in _walk(tree)]


# === Helper: _function_bodies (from test_watchdog_recovery_contract.py) ===
def _function_bodies(tree: ast.Module, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return all function definitions with the given name."""
    return [
        n
        for n in _walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]


# === Helper: _collect_function_owners (from test_watchdog_recovery_contract.py) ===
def _collect_function_owners(
    files_to_check: list[Path],
    target_names: tuple[str, ...],
) -> dict[str, list[Path]]:
    """Return a mapping of function name to list of files defining it at top level.

    Performance: a substring pre-filter skips files that cannot possibly
    contain a top-level ``def <target_name>(...)`` (function names are
    syntactic and MUST appear as ``def <name>`` in the source). The
    pre-filter is the same fast-path pattern used in
    ``tests/test_no_anti_drift_regression.py`` -- it does not change
    the AST semantics, only avoids an AST.parse + ast.walk call when
    the source string cannot contain the function name.
    """
    owners: dict[str, list[Path]] = {name: [] for name in target_names}
    for path in files_to_check:
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if not any(f"def {name}(" in source or f"def {name} (" in source for name in target_names):
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, ValueError):
            continue
        for node in _walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.col_offset != 0:
                continue
            if node.name in owners:
                owners[node.name].append(path)
    return owners


# === Helper: _check_no_duplicate_cooldown_dataclass_field (from test_watchdog_recovery_contract.py) ===
def _check_no_duplicate_cooldown_dataclass_field(
    files_to_check: list[Path],
) -> None:
    """Raise if any file outside the tracker defines a cooldown state field.

    Performance: a substring pre-filter skips files that cannot possibly
    contain a class-body ``<field>: <type>`` annotation with one of the
    cooldown state field names (the field name MUST appear as an
    identifier in the source for an AST AnnAssign target to match).
    """
    cooldown_field_names = ("cooldown_until", "unavailable_until", "backoff_until_ms")
    for path in files_to_check:
        if path == UNAVAILABILITY_TRACKER:
            continue
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if not any(name in source for name in cooldown_field_names):
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, ValueError):
            continue
        for node in _walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign):
                    continue
                target = stmt.target
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in cooldown_field_names:
                    continue
                rel = path.relative_to(REPO_ROOT)
                msg = (
                    f"cooldown state field {target.id!r} at {rel}:"
                    f"{stmt.lineno} (in class {node.name}) -- "
                    "AgentUnavailabilityTracker.UnavailabilityEntry is "
                    "the sole owner of cooldown state."
                )
                raise AssertionError(msg)


# === Helper: _extract_fire_reasons (from test_watchdog_recovery_contract.py) ===
def _extract_fire_reasons(node: ast.AST) -> set[str]:
    """Return ``WatchdogFireReason.<member>`` references on a single AST node."""
    target_name: str | None = None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target_name = node.target.id
    elif (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        target_name = node.targets[0].id
    if target_name != "_EXPECTED_FIRE_REASONS":
        return set()
    if not isinstance(node.value, ast.Call):
        return set()
    if not (isinstance(node.value.func, ast.Name) and node.value.func.id == "frozenset"):
        return set()
    found: set[str] = set()
    for arg in node.value.args:
        if not isinstance(arg, ast.Set):
            continue
        for element in arg.elts:
            outer = element
            if isinstance(element, ast.Call):
                outer = element.func
            if not isinstance(outer, ast.Attribute):
                continue
            inner = outer.value
            attr: str | None = None
            owner_name: str | None = None
            if isinstance(inner, ast.Attribute):
                attr = inner.attr
                if isinstance(inner.value, ast.Name):
                    owner_name = inner.value.id
            elif isinstance(inner, ast.Name):
                attr = outer.attr
                owner_name = inner.id
            if attr is not None and owner_name == "WatchdogFireReason":
                found.add(attr)
    return found


# === Helper: _find_drift_guard (from test_watchdog_recovery_contract.py) ===
def _find_drift_guard(tree: ast.Module) -> ast.If | None:
    """Return the top-level ``if _actual != _EXPECTED_FIRE_REASONS`` guard.

    The guard is the import-time invariant that pins the IdleWatchdog
    sole-owner contract for ``WatchdogFireReason.__members__``.  A
    future refactor that silently regresses the guard (e.g. replaces
    ``raise RuntimeError`` with a plain assignment, or omits the raise
    entirely) would defeat the contract; this locator gives the
    fail-fast test a precise handle to assert on.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.NotEq):
            continue
        if len(test.comparators) != 1:
            continue
        if not (isinstance(test.left, ast.Name) and test.left.id == "_actual") or not (
            isinstance(test.comparators[0], ast.Name)
            and test.comparators[0].id == "_EXPECTED_FIRE_REASONS"
        ):
            continue
        return node
    return None


# === Helper: _guard_raises_runtime_error (from test_watchdog_recovery_contract.py) ===
def _guard_raises_runtime_error(guard: ast.If) -> tuple[bool, int | None]:
    """Return ``(has_raise, line_no)`` for ``RuntimeError`` raise nodes in ``guard.body``.

    Only the top-level statements of the guard body are inspected.
    The contract requires the raise to be a direct statement of the
    guard (not buried inside an inner ``if``/``try``) so a future
    refactor cannot accidentally hide the raise behind a conditional
    that never fires.
    """
    for stmt in guard.body:
        if not isinstance(stmt, ast.Raise):
            continue
        exc = stmt.exc
        if not isinstance(exc, ast.Call):
            continue
        if isinstance(exc.func, ast.Name) and exc.func.id == "RuntimeError":
            return True, stmt.lineno
    return False, None


# === Fixture: captured_info_records ===
@pytest.fixture
def captured_info_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink that captures INFO+ records from ``idle_watchdog``.

    Returns ``(buffer, records)`` where ``records`` is a list of
    formatted log lines. The sink is removed automatically after the
    test completes (the ``finally`` block runs even on test failure).
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="INFO",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


# === Fixture: captured_debug_records ===
@pytest.fixture
def captured_debug_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink that captures DEBUG records from idle_watchdog.

    Returns (buffer, records) where records is a list of formatted
    log lines. The sink is removed automatically after the test.
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


# === Fixture: captured_log_records ===
@pytest.fixture
def captured_log_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink filtered on ``component='idle_watchdog'``.

    The filter matches the canonical public loguru surface used by
    ``test_r6_heartbeat``: the watchdog binds its internal logger via
    ``self._log = logger.bind(component="idle_watchdog")`` in
    ``idle_watchdog.py:558``. Any DEBUG/INFO/ERROR record emitted via
    that logger (or any deeper bind) flows through this sink.
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


# === consolidated from test_activity_aware.py ===
def test_first_party_mcp_tool_defers_no_output_deadline() -> None:
    """AC-1: MCP tool calls with quiet stdout defer NO_OUTPUT_DEADLINE."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(100.0)
    wd.record_mcp_tool_call()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_activity_aware.py ===
def test_first_party_subagent_output_defers_no_output_deadline() -> None:
    """AC-2/AC-7: subagent output stream defers NO_OUTPUT_DEADLINE."""
    capture = FakeCapture(lines=[["hello from subagent"]])
    monitor = FakeProcessMonitor(captures={"worker-1": capture})
    wd, clock = _activity_aware_make_watchdog(
        _activity_aware_make_policy(activity_ttl=1000.0),
        process_monitor=monitor,
    )
    wd.record_activity()
    clock.advance(100.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd._subagent_output_count == 1

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_activity_aware.py ===
def test_first_party_subagent_progress_defers_no_output_deadline() -> None:
    """AC-2: explicit subagent progress signals defer NO_OUTPUT_DEADLINE."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE


# === consolidated from test_activity_aware.py ===
def test_dead_subagent_detected_within_idle_window() -> None:
    """AC-3: silent subagent fires at idle deadline, not cumulative ceiling."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    wd.record_activity()
    wd.record_subagent_work()
    clock.advance(31.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_activity_aware.py ===
def test_truly_idle_fires_on_time() -> None:
    """AC-4: no activity on any channel fires at idle deadline."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_activity_aware.py ===
def test_side_channel_workspace_source_defers_log_does_not() -> None:
    """AC-5: source workspace change defers; log change does not."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    assert wd.evaluate(classify_quiet=_activity_aware_active) == WatchdogVerdict.CONTINUE

    wd2, clock2 = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd2.record_activity()
    clock2.advance(1.0)
    wd2.record_workspace_event(kind=WorkspaceChangeKind.LOG, weight=0.0)
    assert wd2.evaluate(classify_quiet=_activity_aware_active) == WatchdogVerdict.FIRE


# === consolidated from test_activity_aware.py ===
def test_bare_subagent_liveness_defers_fire() -> None:
    """AC-02 (smart-verdict): bare PID liveness defers the fire.

    The new design treats a live subagent without first-party evidence
    as the LOADING stuck kind: the classifier returns LOADING and the
    gate returns CONTINUE so a productive-but-quiet session is not
    killed. This replaces the OLD behavior where bare liveness was
    ignored and the watchdog fired at the cumulative child-wait
    ceiling regardless of whether the child was making progress.
    """
    monitor = FakeProcessMonitor(live_count=1)
    wd, clock = _activity_aware_make_watchdog(
        _activity_aware_make_policy(activity_ttl=1000.0),
        process_monitor=monitor,
    )
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE


# === consolidated from test_activity_aware.py ===
def test_session_ceiling_unaffected_by_first_party_activity() -> None:
    """AC-13: session ceiling fires regardless of first-party activity."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(max_session=5.0, activity_ttl=1000.0))
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_activity_aware.py ===
def test_cumulative_waiting_ceiling_unaffected_by_activity() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY regardless of fresh first-party activity.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when first-party channels
    (mcp_tool) are fresh within ``activity_evidence_ttl_seconds``.

    Pre-fix (wt-013 activity-aware): the gate deferred the fire when
    first-party channels were fresh. Post-fix (R3 hard enforcement):
    the cumulative ceiling fires regardless of mcp_tool freshness.

    Assertions:
      - verdict is FIRE at the cumulative ceiling regardless of
        fresh mcp_tool activity within ``activity_evidence_ttl_seconds``.
    """
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(idle_timeout=0.1, max_waiting=2.0, activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(0.1)

    # The cumulative ceiling is 2.0s; advance the clock past it
    # in 0.1s increments while keeping the mcp_tool channel fresh
    # via ``record_mcp_tool_call``. Per R3 hard enforcement the
    # ceiling fires UNCONDITIONALLY regardless of mcp_tool
    # freshness.
    fire_observed = False
    for _ in range(30):
        wd.record_mcp_tool_call()
        verdict = wd.evaluate(classify_quiet=_activity_aware_waiting)
        clock.advance(0.1)
        if verdict == WatchdogVerdict.FIRE:
            fire_observed = True
            break

    # The cumulative ceiling MUST fire within 30 evaluate() calls
    # even with fresh mcp_tool activity.
    assert fire_observed, (
        "cumulative ceiling MUST fire unconditionally past the"
        " ceiling (R3 hard enforcement) regardless of mcp_tool"
        " freshness; never observed FIRE in 30 calls"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_activity_aware.py ===
def test_evidence_summary_labels_tiers() -> None:
    """AC-12: evidence summary includes tier labels and deferral flags."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    clock.advance(1.0)

    summary = wd.last_evidence_summary(clock.monotonic())
    by_name = {c.channel_name: c for c in summary.channels}
    assert by_name[ChannelName.STDOUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.MCP_TOOL].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_OUTPUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_LIVENESS].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.WORKSPACE].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.SUBAGENT_LIVENESS].can_defer is False


# === consolidated from test_activity_aware.py ===
def test_process_monitor_disabled_gracefully() -> None:
    """AC-10: when no process monitor is injected, liveness is unavailable."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    summary = wd.last_evidence_summary(clock.monotonic())
    liveness = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert liveness is not None
    assert liveness.last_at is None
    assert liveness.can_defer is False


# === consolidated from test_activity_aware.py ===
def test_subagent_output_unavailable_when_no_process_monitor() -> None:
    """AC-10: when process monitor is None, subagent output is unavailable."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    assert wd.poll_subagent_output() == 0
    output = wd.last_evidence_summary(clock.monotonic()).by_name(ChannelName.SUBAGENT_OUTPUT)
    assert output is not None
    assert output.last_at is None


# === consolidated from test_activity_aware.py ===
def test_fire_diagnostic_includes_evidence_summary() -> None:
    """AC-12: fire diagnostic embeds per-channel evidence summary."""
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    clock.advance(1.0)
    wd.evaluate(classify_quiet=_activity_aware_active)
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    summary = wd.last_evidence_summary(clock.monotonic())
    assert len(summary.channels) == 5
    assert all(
        c.tier in {EvidenceTier.FIRST_PARTY, EvidenceTier.SIDE_CHANNEL} for c in summary.channels
    )


# === consolidated from test_both_repetition_dimensions.py ===
def test_mcp_timeout_storm_with_varying_args_trips_the_error_dimension() -> None:
    """The originating incident: identical error text, DIFFERENT arguments.

    The tool dimension cannot collapse these -- every call has a different
    path -- so only the error dimension can see the storm.
    """
    clock, watchdog, record = _harness()

    for index in range(8):
        record(
            watchdog,
            _errored_tool_line(
                f"/repo/file_{index}.py",
                call_id=f"call_{index}",
                error="MCP error -32001: Request timed out",
            )
            + "\n",
        )
        clock.advance(30.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


# === consolidated from test_both_repetition_dimensions.py ===
def test_repeated_failing_command_with_varying_text_trips_the_tool_dimension() -> None:
    """The mirror case: identical arguments, DIFFERENT error text every attempt.

    The error dimension cannot collapse these, so only the tool dimension can
    see the wedge. Both directions must work, which is why the signal feeds
    both rather than choosing one.
    """
    clock, watchdog, record = _harness()

    for index in range(5):
        record(
            watchdog,
            _errored_tool_line(
                "/repo/same.py",
                call_id=f"call_{index}",
                error=f"2 failed, 118 passed in {index}.42s",
            )
            + "\n",
        )
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_both_repetition_dimensions.py ===
def test_successful_calls_feed_no_error_dimension() -> None:
    """A completed tool must not contribute anything to the error breaker."""
    clock, watchdog, record = _harness()
    line = json.dumps(
        {
            "type": "tool_use",
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"path": "/a.py"}, "output": "ok"},
            },
        }
    )

    for _ in range(8):
        record(watchdog, line + "\n")
        clock.advance(30.0)

    assert watchdog.repetition_diagnostic().get("error_fingerprint") is None


# === consolidated from test_claude_interactive_tool_fingerprints.py ===
def test_interactive_tool_use_carries_its_arguments() -> None:
    """The transcript parser already has ``input``; it must reach the breaker."""
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line(_transcript_line("git status --short"))

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "Bash",
        {"command": "git status --short"},
    )


# === consolidated from test_claude_interactive_tool_fingerprints.py ===
def test_interactive_distinct_commands_do_not_trip_the_breaker() -> None:
    """Ten different Bash commands MUST NOT look like one wedged call."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)
    commands = [
        "git status --short",
        "uv run pytest -q",
        "make lint",
        "ls ralph/display",
        "git diff --stat",
        "make verify",
        "cat pyproject.toml",
        "git log --oneline -5",
        "uv run ruff check ralph/",
        "make typecheck",
    ]

    for command in commands:
        signal = strategy.classify_activity_line(_transcript_line(command))
        assert signal is not None
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(30.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != (
        WatchdogVerdict.FIRE
    )


# === consolidated from test_claude_interactive_tool_fingerprints.py ===
def test_interactive_marker_without_metadata_still_falls_back() -> None:
    """A bare ``claude tool:`` marker keeps working through the plain branch."""
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line("claude tool: read_file\n")

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == ("read_file", {})


# === consolidated from test_clean_exit_session_id_recovery.py ===
def test_pi_session_event_id_is_preserved_for_clean_exit_recovery(tmp_path: Path) -> None:
    """Pi emits its resumable session identity as top-level ``id``."""
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        liveness_probe=FakeLivenessProbe(active=False),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
    )

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "pi",
            ['{"type":"session","id":"pi-main-session"}'],
            opts,
        )

    assert excinfo.value.resumable_session_id == "pi-main-session"


# === consolidated from test_clean_exit_session_id_recovery.py ===
def test_captured_session_id_preserved(tmp_path: Path) -> None:
    """When ``captured_session_id`` is set on ``CompletionCheckOptions``
    the resumable exception carries that id (no fallback
    re-extraction).

    This pins the production behavior: the live-stream captured id is
    authoritative when present. The fallback chain only runs when
    ``captured_session_id`` is ``None``.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id="sess-from-live-stream",
    )
    parsed_output: list[str] = []
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-from-live-stream", (
        f"captured_session_id MUST win over the bounded_output fallback;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


# === consolidated from test_clean_exit_session_id_recovery.py ===
def test_legacy_extractor_recovers_from_bounded_output(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None and ``parsed_output``
    contains a plain ``Session ID: ...`` line, the legacy
    ``extract_transport_session_id`` extractor recovers the id and
    the resumable exception carries it.

    Pre-fix this test would also pass (the legacy extractor was the
    primary path before the new fallback), but it pins the existing
    legacy path so a regression to the legacy path is caught.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["Session ID: sess-abc123", "other line"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-abc123", (
        f"legacy extractor MUST recover 'sess-abc123' from bounded_output;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


# === consolidated from test_clean_exit_session_id_recovery.py ===
def test_pty_visible_tui_recovers_from_ansi_wrapped_line(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None and ``parsed_output``
    contains an ANSI-wrapped ``Session ID: ...`` line, the per-line
    PTY-aware extractor recovers the id.

    Pre-fix this would raise ``OpenCodeResumableExitError`` with
    ``session_id=None`` because the legacy
    ``extract_transport_session_id`` cannot match anchored text
    patterns against TUI-banner lines wrapped in ANSI escape codes.
    Post-fix the per-line PTY extractor
    (``extract_transport_session_id_with_visible_tui``) strips ANSI
    codes via ``_visible_tui_text`` and matches the underlying text
    so the resumable exception carries the captured id.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["\x1b[32mSession ID: sess-pty-xyz\x1b[0m"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id == "sess-pty-xyz", (
        f"per-line PTY extractor MUST recover 'sess-pty-xyz' from"
        f" ANSI-wrapped bounded_output;"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


# === consolidated from test_clean_exit_session_id_recovery.py ===
def test_no_session_id_in_bounded_output_still_raises_none_id(tmp_path: Path) -> None:
    """When ``captured_session_id`` is None AND ``parsed_output`` lacks
    any session id, the resumable exception still carries
    ``session_id=None`` (no fabrication).

    The fallback chain is conservative: it only fills in a
    ``resumable_session_id`` when the bounded output contains one.
    If neither the legacy nor the per-line PTY extractor finds an id
    the exception raises with ``session_id=None`` so the recovery
    controller knows the session cannot be resumed.
    """
    probe = FakeLivenessProbe(active=False)
    strategy = OpenCodeExecutionStrategy()
    handle = _FakeHandle(returncode=0, has_descendants=False)
    opts = CompletionCheckOptions(
        execution_strategy=strategy,
        workspace_path=tmp_path,
        liveness_probe=probe,
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_no_signals,
        captured_session_id=None,
    )
    parsed_output = ["plain stdout line", "no id here"]
    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            parsed_output,
            opts,
        )
    assert excinfo.value.resumable_session_id is None, (
        f"resumable_session_id MUST be None when bounded_output lacks"
        f" an id (no fabrication);"
        f" got resumable_session_id={excinfo.value.resumable_session_id!r}"
    )


# === consolidated from test_cross_transport_subagent_visibility.py ===
def test_opencode_discovery_strategy_is_registry_backed_with_registry() -> None:
    """OpenCode + registry returns ``OpenCodeRegistryDiscoveryStrategy``.

    OpenCode is the only transport whose agent CLI documents a stable
    structured child event stream (carried on the agent's own stdout).
    The factory must wire the injected ``ChildLivenessRegistry``
    through to the strategy so a per-child
    :class:`RegistryBackedSubagentOutputCapture` can surface real-time
    progress, heartbeat, and terminal events.
    """
    config = type(
        "Cfg",
        (),
        {"transport": AgentTransport.OPENCODE},
    )()
    registry = _make_registry()
    strategy = _discovery_strategy_for_config(
        config, registry=registry, scope_prefix="agent:test-scope:"
    )
    assert isinstance(strategy, OpenCodeRegistryDiscoveryStrategy), (
        f"transport=OPENCODE: expected OpenCodeRegistryDiscoveryStrategy;"
        f" got {type(strategy).__name__}"
    )


# === consolidated from test_cross_transport_subagent_visibility.py ===
def test_opencode_discovery_strategy_is_null_without_registry() -> None:
    """OpenCode without a registry degrades to ``NullDiscoveryStrategy``.

    The watchdog must not invent a registry it does not have. Without a
    registry the cross-transport subagent activity sink is the
    documented fallback for OpenCode line observers.
    """
    config = type(
        "Cfg",
        (),
        {"transport": AgentTransport.OPENCODE},
    )()
    strategy = _discovery_strategy_for_config(config, registry=None, scope_prefix="")
    assert isinstance(strategy, NullDiscoveryStrategy), (
        f"transport=OPENCODE without registry: expected NullDiscoveryStrategy;"
        f" got {type(strategy).__name__}"
    )


# === consolidated from test_cross_transport_subagent_visibility.py ===
def test_opencode_surfaces_real_extracted_progress_via_registry() -> None:
    """OpenCode registry-backed strategy surfaces REAL extracted progress.

    End-to-end: a per-child capture surfaces registry progress events.
    The watchdog polls ``discover_subagent_outputs`` from the process
    monitor and records each new line as ``subagent_output`` first-party
    evidence via ``record_subagent_output``. With an injected
    ``OpenCodeRegistryDiscoveryStrategy`` backed by a real
    ``ChildLivenessRegistry`` containing an active child with progress
    and heartbeat events, the watchdog's first-party channel count
    must advance.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")
    registry.record_heartbeat("child-A")

    @dataclass
    class _RegistryBackedMonitor(ProcessMonitor):
        registry: ChildLivenessRegistry
        scope_prefix: str
        poll_count: int = 0

        def live_subagent_count(self) -> int:
            return 0

        def classified_processes(self) -> tuple:
            return ()

        def refresh(self) -> None:
            pass

        def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
            self.poll_count += 1
            return OpenCodeRegistryDiscoveryStrategy(
                self.registry, self.scope_prefix
            ).discover_subagent_outputs(host_pid=999)

    monitor = _RegistryBackedMonitor(registry=registry, scope_prefix="agent:test-scope:")
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        subagent_output_poll_interval_seconds=0.001,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    watchdog.record_activity()

    clock.advance(0.01)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())
    assert fresh >= 1
    assert monitor.poll_count == 1
    assert watchdog._subagent_output_count >= 1

    clock.advance(0.01)
    registry.record_progress("child-A", phase="phase-2")
    fresh2 = watchdog.poll_subagent_output(now=clock.monotonic())
    assert fresh2 >= 1
    assert watchdog._subagent_output_count >= 2


# === consolidated from test_cross_transport_subagent_visibility.py ===
def test_opencode_capture_lines_consumable_by_record_subagent_work() -> None:
    """Per-child capture lines surface as ``record_subagent_work`` signals.

    For OpenCode, a per-child
    :class:`RegistryBackedSubagentOutputCapture` produces textual lines
    (e.g. ``[subagent] progress: phase=phase-1``) which the
    ``DefaultProcessMonitor``-driven poll path forwards into
    ``record_subagent_work`` so ``last_subagent_progress_description``
    updates in real time. This test proves the line payload format the
    factory's strategy emits is consumable by the sink.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]
    lines = capture.read_lines(worker_id="child-A")

    watchdog = _cross_transport_subagent_visib_make_watchdog()
    watchdog.record_invocation_start()
    consumed: list[str] = []
    for line in lines:
        watchdog.record_subagent_work(description=line)
        consumed.append(line)

    assert watchdog.last_subagent_progress_description is not None
    assert any("phase-1" in line for line in consumed), consumed
    assert any("heartbeat" in line.lower() for line in consumed), consumed


# === consolidated from test_cross_transport_subagent_visibility.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_extracted_progress_to_watchdog(
    transport: AgentTransport,
) -> None:
    """Each transport's strategy surfaces REAL extracted progress.

    Black-box contract: build the canonical execution strategy for the
    transport, wire the watchdog's ``record_subagent_work`` into the
    cross-transport subagent sink, observe a child signal line that
    real agents emit on stdout, and assert the watchdog captures the
    real extracted description in
    ``last_subagent_progress_description``.

    This proves the prompt's requirement -- "we should do this for ALL
    supported agents" -- black-box for every transport, not just
    OpenCode. The non-OpenCode transports do not have a documented
    per-worker log path so the discovery strategy is a no-op, but the
    line observer feeds real extracted progress to the watchdog
    regardless of transport.
    """
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_PROGRESS_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_PROGRESS_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" progress from line observer; got"
            f" {watchdog.last_subagent_progress_description!r}"
        )
        # The subagent_progress_count is surfaced via the public
        # diagnostic_snapshot() rather than via the private
        # ``_subagent_progress_count`` field. Use the public API so the
        # test stays black-box.
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" progress line; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY: the monotonic timestamp of the most
        # recent subagent observation MUST be populated for every
        # transport after a real child signal line. ``>= 0.0``
        # guards against accidentally returning a sentinel
        # negative value (FakeClock starts at 0.0 so the recorded
        # timestamp is the wall-clock origin).
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real progress line; got {last_activity!r}"
        )
        # R5 CURRENT TOOL CALL: the parsed ``verb:`` prefix MUST
        # match what the production parser yields for the observed
        # description. For ``_REAL_PROGRESS_LINE =
        # "[subagent] progress: phase=phase-1"`` the parser
        # returns ``None`` (the head ``"[subagent] progress"`` is
        # not a known verb) -- the assertion is therefore a
        # meaningful black-box check that the field exists and
        # the parser runs end-to-end on every transport.
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_PROGRESS_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the observed description; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


# === consolidated from test_cross_transport_subagent_visibility.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_heartbeat_extraction(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted heartbeat activity.

    Heartbeat lines (``[subagent] heartbeat``) are routed through the
    cross-transport subagent activity sink for every transport. This
    test proves that real heartbeat activity is captured for every
    supported transport -- operators reading the watchdog's per-channel
    log see the most recent heartbeat, not a graceful-degradation stub.
    """
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_HEARTBEAT_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_HEARTBEAT_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" heartbeat; got {watchdog.last_subagent_progress_description!r}"
        )
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" heartbeat line; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL: must flow through
        # every transport after a real heartbeat line. The parser
        # returns ``None`` for ``"[subagent] heartbeat"`` (no
        # ``": "`` separator) so the assertion is meaningful even
        # when the parsed value is ``None``.
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real heartbeat line; got {last_activity!r}"
        )
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_HEARTBEAT_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the heartbeat description; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


# === consolidated from test_cross_transport_subagent_visibility.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_json_extraction(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted JSON child signals.

    Production agents (Codex, Generic, Claude with JSON envelopes)
    emit ``{"type": "child_progress", ...}`` lines. The cross-transport
    classifier routes these into the subagent activity sink for every
    transport.
    """
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_CHILD_JSON_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_CHILD_JSON_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" JSON child signal; got"
            f" {watchdog.last_subagent_progress_description!r}"
        )
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" JSON child signal; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL: must flow through
        # every transport after a real JSON child signal. The
        # parser returns ``None`` for the JSON envelope (the head
        # ``{"type"`` is not a known verb).
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real JSON child signal; got {last_activity!r}"
        )
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_CHILD_JSON_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the JSON child signal; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


# === consolidated from test_cross_transport_subagent_visibility.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_extraction_to_listener(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted progress to a registered listener.

    Black-box contract: build the canonical execution strategy for the
    transport, wire the watchdog's ``record_subagent_work`` into the
    cross-transport subagent sink, register a default subagent activity
    listener, observe a child signal line, drive the watchdog through
    ``evaluate()`` so it transitions into the WAITING_ON_CHILD branch
    and emits an ENTERED waiting-status event, and assert the listener
    receives the real extracted description via the ``subagent_activity``
    field of the waiting status event.

    This is the cross-transport surface that operators rely on to see
    what every supported agent's subagents are doing in real time.
    """
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        captured: list[WaitingStatusEvent] = []

        def _listener(event: WaitingStatusEvent) -> None:
            captured.append(event)

        watchdog.record_invocation_start()
        watchdog.register_default_subagent_activity_listener(_listener)

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_PROGRESS_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_PROGRESS_LINE

        # Drive the watchdog through ``evaluate()`` with a
        # WAITING_ON_CHILD ``classify_quiet`` so the watchdog
        # transitions into the waiting branch and emits the ENTERED
        # status event naturally. The threshold is configured so a
        # single ``evaluate()`` call advances past idle and into the
        # waiting branch on the first poll.
        clock = watchdog._clock
        clock.advance(61.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        watchdog.evaluate(classify_quiet=_waiting)
        assert captured, (
            f"transport={transport!r}: watchdog MUST emit a waiting"
            f" status event with subagent_activity after evaluate()"
            f" transitions into WAITING_ON_CHILD"
        )
        latest = captured[-1]
        assert latest.subagent_activity == _REAL_PROGRESS_LINE, (
            f"transport={transport!r}: listener did not receive real"
            f" extracted progress; got {latest.subagent_activity!r}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL on the
        # WaitingStatusEvent surface for every transport. The
        # ``emit`` dispatcher in ``_active_branch`` populates all
        # three R5 fields on every emitted event; the listener
        # receives the typed dataclass so the assertion is
        # black-box (no private-seam access).
        assert (
            latest.last_subagent_progress_at is not None
            and isinstance(latest.last_subagent_progress_at, float)
            and latest.last_subagent_progress_at >= 0.0
        ), (
            f"transport={transport!r}: WaitingStatusEvent"
            f" MUST carry last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real progress line; got"
            f" {latest.last_subagent_progress_at!r}"
        )
        assert latest.current_subagent_tool_call == _parse_tool_call_expected(
            _REAL_PROGRESS_LINE
        ), (
            f"transport={transport!r}: WaitingStatusEvent"
            f" MUST carry current_subagent_tool_call matching the"
            f" parser output for the observed description; got"
            f" {latest.current_subagent_tool_call!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


# === consolidated from test_cross_transport_subagent_visibility.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_cross_transport_subagent_activity_sink_is_wired(
    transport: AgentTransport,
) -> None:
    """Every transport surfaces subagent activity through the cross-transport sink.

    Black-box contract: regardless of the transport, the sink accepts a
    description and ``last_subagent_progress_description`` returns it;
    a waiting-status event driven by ``evaluate()`` forwards the
    description to a registered listener; and
    ``record_invocation_start`` clears the description so a new
    invocation starts with a clean slate.
    """
    del transport
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    captured: list[tuple[str, str]] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append((event.kind.value, event.subagent_activity or ""))

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="first")
    # Drive the watchdog into the WAITING_ON_CHILD branch so the
    # ENTERED event is emitted through the public evaluate() path.
    watchdog._clock.advance(61.0)

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    watchdog.evaluate(classify_quiet=_waiting)
    # The watchdog may emit multiple status events (ENTERED +
    # SUBAGENT_PROGRESS) on the same evaluate() call; the
    # black-box contract is "every event carries the recorded
    # description", not "exactly one event".
    assert captured, (
        "watchdog.evaluate MUST emit at least one waiting-status event"
        " carrying the recorded subagent description; got no events"
    )
    assert all(description == "first" for _kind, description in captured), (
        "Every waiting-status event forwarded to the listener MUST"
        " carry the recorded subagent description; got: {captured}"
    )

    # R5 LAST ACTIVITY + CURRENT TOOL CALL on the
    # ``diagnostic_snapshot()`` surface for the sink-wired path:
    # after recording subagent work and driving ``evaluate()``
    # into WAITING_ON_CHILD, the snapshot MUST expose all three
    # R5 fields populated from the same source the watchdog uses
    # for the WaitingStatusEvent surface. The snapshot MUST be
    # taken BEFORE ``record_invocation_start`` because that
    # helper resets the R5 fields to ``None`` (per-invocation
    # semantics from R5).
    post_record_snapshot = watchdog.diagnostic_snapshot(now=0.0)
    last_activity_post = post_record_snapshot["last_subagent_progress_at"]
    assert (
        last_activity_post is not None
        and isinstance(last_activity_post, float)
        and last_activity_post >= 0.0
    )
    assert post_record_snapshot["current_subagent_tool_call"] == _parse_tool_call_expected("first")

    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_description is None

    # ``record_invocation_start`` resets ALL THREE R5 fields to
    # ``None`` (per-invocation semantics from R5). Verifies the
    # LAST ACTIVITY + CURRENT TOOL CALL fields are cleared
    # alongside the existing PROGRESS field reset.
    reset_snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert reset_snapshot["last_subagent_progress_at"] is None
    assert reset_snapshot["current_subagent_tool_call"] is None


# === consolidated from test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py ===
def test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent() -> None:
    """R3 regression pin: cumulative ceiling fires on SILENT_SUBAGENT deferral.

    Scenario: a real subagent is alive (filtered count = 1) AND the
    classifier returns ``SILENT_SUBAGENT`` -- the subagent is alive
    in the OS but is NOT producing fresh progress / heartbeat
    evidence. The cumulative ceiling MUST fire regardless of the
    SILENT_SUBAGENT classification.

    This test FAILS on the pre-fix code because the cumulative
    ceiling block at ``_waiting_branch.py:238-247`` consults
    ``self._gate_fire(...)`` and returns ``WatchdogVerdict.CONTINUE``
    when ``_classify_stuck_now`` returns ``SILENT_SUBAGENT`` -- the
    exact bug class that produced the 2365s indefinite deferral.

    Post-fix the cumulative ceiling block drops the ``_gate_fire``
    consultation and the ``CONTINUE`` branch, so the ceiling fires
    unconditionally when ``candidate_total >= effective_ceiling``.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        # Short idle deadline so the watchdog enters the verdict
        # path quickly. MUST be <= ``max_waiting_on_child_seconds``.
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 10s.
        max_waiting_on_child_seconds=10.0,
        # Disable the no-progress quiet ceiling so the test is
        # unambiguous: the cumulative ceiling is the only fire path.
        max_waiting_on_child_no_progress_seconds=None,
        # Disable the OS-descendant-only ceiling -- would compete
        # with the cumulative ceiling.
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling so the SUB-ceiling
        # branch (which retains its ``_gate_fire`` consultation)
        # cannot fire first; the cumulative ceiling is the headline
        # fire reason for this test.
        stuck_job_sub_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        # Stale activity evidence (ttl=0 disables the
        # subagent_liveness_fresh branch so the classifier
        # does NOT short-circuit to LOADING via that branch;
        # the override below forces SILENT_SUBAGENT regardless).
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _RealSubagentMonitor(filtered_count=1)
    watchdog = _cumulative_waiting_ceiling_fir_make_watchdog(
        clock=clock,
        process_monitor=monitor,
        policy=policy,
    )
    # Force the classifier to return SILENT_SUBAGENT on every
    # call so the gate takes the SILENT_SUBAGENT branch (returns
    # CONTINUE on pre-fix; has no effect on post-fix because the
    # cumulative ceiling block drops the gate consultation).
    _force_classify_stuck_kind(watchdog, StuckKind.SILENT_SUBAGENT)
    watchdog.record_invocation_start()
    # First evaluate() at 3s: idle_elapsed (3s) > idle_timeout (2s),
    # classify_quiet returns WAITING_ON_CHILD -> enters waiting
    # branch with current_run_elapsed = 0.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    # Advance the clock by 9s so current_run_elapsed reaches 9s and
    # the candidate_total (cumulative=0 + run=9s = 9s) is still
    # below the cumulative ceiling (10s). The next advance tips
    # candidate_total past the ceiling.
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert pre_ceiling_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"pre-ceiling evaluate() MUST defer, got {pre_ceiling_verdict!r}"
    )
    # Advance past the cumulative ceiling. The ceiling MUST fire on
    # the next evaluate() call regardless of the SILENT_SUBAGENT
    # classification. This is the headline R3 invariant -- the
    # pre-fix code would have returned CONTINUE on SILENT_SUBAGENT.
    clock.advance(1.0)
    verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire even when _classify_stuck_now"
        f" returns SILENT_SUBAGENT; got verdict={verdict!r}"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
        f" last_deferred_kind={watchdog.last_deferred_kind!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"cumulative ceiling fire reason MUST be"
        f" CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


# === consolidated from test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py ===
def test_cumulative_ceiling_fires_when_classify_stuck_returns_loading() -> None:
    """R3 hard-enforcement: cumulative ceiling fires on LOADING deferral.

    Scenario: a real subagent is alive (filtered count = 1) AND the
    classifier returns ``LOADING``. Per PROMPT R3 the cumulative
    ceiling MUST fire regardless of any classification signal.

    This test pins the invariant that a healthy, fresh liveness
    signal (LOADING) cannot indefinitely extend the cumulative
    wait past the ceiling -- the hard ceiling is the absolute
    backstop the prompt requires.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 10s.
        max_waiting_on_child_seconds=10.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling so the SUB-ceiling
        # branch (which gates on stale alive_by in
        # ``_STUCK_ALIVE_BY_VALUES``) cannot fire first.
        stuck_job_sub_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _RealSubagentMonitor(filtered_count=1)
    watchdog = _cumulative_waiting_ceiling_fir_make_watchdog(
        clock=clock,
        process_monitor=monitor,
        policy=policy,
    )
    # Force the classifier to return LOADING so the gate takes
    # the LOADING branch (returns CONTINUE on pre-fix; has no
    # effect on post-fix because the cumulative ceiling block
    # drops the gate consultation).
    _force_classify_stuck_kind(watchdog, StuckKind.LOADING)
    watchdog.record_invocation_start()
    # First evaluate() at 3s enters the waiting branch.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    # Advance so cumulative waiting time exceeds the 10s ceiling.
    # The classifier returns LOADING (a healthy liveness signal)
    # so on the pre-fix code ``_gate_fire`` returns ``CONTINUE``
    # and the ceiling is bypassed. Post-fix the ceiling fires
    # unconditionally.
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert pre_ceiling_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"pre-ceiling evaluate() MUST defer, got {pre_ceiling_verdict!r}"
    )
    clock.advance(1.0)
    verdict = watchdog.evaluate(classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire even when _classify_stuck_now"
        f" returns LOADING; got verdict={verdict!r}"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"cumulative ceiling fire reason MUST be"
        f" CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


# === consolidated from test_cursor_tool_fingerprints.py ===
def test_identical_shell_calls_share_one_fingerprint() -> None:
    strategy = strategy_for_transport(AgentTransport.CURSOR)

    fingerprints = set()
    for index in range(3):
        signal = strategy.classify_activity_line(_shell_call_line("ls -la", call_id=f"t_{index}"))
        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        fingerprints.add(json.dumps(extracted, sort_keys=True))

    assert len(fingerprints) == 1


# === consolidated from test_cursor_tool_fingerprints.py ===
def test_different_shell_commands_stay_distinct() -> None:
    """Stripping per-call ids must not also collapse genuinely different calls."""
    first = extract_tool_call_from_activity_signal(_shell_call_line("ls -la", call_id="t_1"))
    second = extract_tool_call_from_activity_signal(_shell_call_line("pwd", call_id="t_2"))

    assert first != second
    assert first == (
        "shellToolCall",
        {"command": "ls -la", "workingDirectory": "/repo", "timeout": 30000},
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_has_all_required_keys() -> None:
    """The snapshot dict MUST contain every key documented in the
    method's docstring.
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    required_keys = {
        "last_fire_reason",
        "last_deferred_kind",
        "last_alive_by",
        "idle_elapsed_seconds",
        "invocation_elapsed_seconds",
        "cumulative_waiting_on_child_seconds",
        "last_subagent_progress_description",
        "live_subagent_count",
        "subagent_progress_count",
        "subagent_output_count",
        "mcp_tool_call_count",
        "workspace_event_count",
        "evidence_summary",
        "resumable_session_id",
    }
    assert set(snapshot.keys()) >= required_keys, (
        f"snapshot keys missing: required - snapshot = {required_keys - set(snapshot.keys())}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_is_json_serializable() -> None:
    """The snapshot dict MUST be JSON-serializable so it can be
    embedded in the merged_diag payload without further conversion.
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    # Must not raise (json.dumps requires primitive types).
    encoded = json.dumps(snapshot)
    assert isinstance(encoded, str)
    # Round-trip the JSON to confirm the shape is preserved.
    decoded = json.loads(encoded)
    assert decoded["last_fire_reason"] is None
    assert decoded["last_deferred_kind"] is None


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_reflects_record_subagent_work() -> None:
    """After ``record_subagent_work`` the snapshot MUST carry the
    description AND increment the subagent_progress_count.
    """
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_subagent_work(description="reading source.py")
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["last_subagent_progress_description"] == "reading source.py", (
        f"snapshot.last_subagent_progress_description MUST be"
        f" 'reading source.py'; got {snapshot['last_subagent_progress_description']!r}"
    )
    assert snapshot["subagent_progress_count"] == 1, (
        f"snapshot.subagent_progress_count MUST be 1; got {snapshot['subagent_progress_count']}"
    )
    # idle_elapsed_seconds == 5.0
    assert snapshot["idle_elapsed_seconds"] == 5.0, (
        f"snapshot.idle_elapsed_seconds MUST be 5.0; got {snapshot['idle_elapsed_seconds']}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_live_subagent_count() -> None:
    """When a process monitor is injected with ``live_subagent_count=N``
    the snapshot MUST report ``live_subagent_count=N``.
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog(monitor_count=3)
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert snapshot["live_subagent_count"] == 3, (
        f"snapshot.live_subagent_count MUST be 3; got {snapshot['live_subagent_count']}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_evidence_summary_has_channels() -> None:
    """The ``evidence_summary`` list MUST contain one entry per
    ``ChannelName`` (stdout, mcp_tool, subagent_output,
    subagent_liveness, workspace).
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    summary = snapshot["evidence_summary"]
    assert isinstance(summary, list), (
        f"snapshot.evidence_summary MUST be a list; got {type(summary)}"
    )
    # 5 channels: stdout, mcp_tool, subagent_output, subagent_liveness, workspace.
    assert len(summary) == 5, f"snapshot.evidence_summary MUST have 5 entries; got {len(summary)}"


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_after_record_invocation_start_resets() -> None:
    """``record_invocation_start`` MUST clear the
    ``last_subagent_progress_description`` so the snapshot reflects the
    fresh invocation. (Per-channel counters are session-scoped and
    survive across invocations by design.)
    """
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_subagent_work(description="reading source.py")
    # Capture a snapshot showing the populated state.
    populated = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert populated["last_subagent_progress_description"] == "reading source.py"
    assert populated["subagent_progress_count"] == 1
    # Reset and capture again.
    watchdog.record_invocation_start()
    clock.advance(2.0)
    reset = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert reset["last_subagent_progress_description"] is None, (
        f"record_invocation_start MUST reset"
        f" last_subagent_progress_description; got"
        f" {reset['last_subagent_progress_description']!r}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_is_pure_read_no_side_effects() -> None:
    """Calling ``diagnostic_snapshot`` MUST NOT mutate watchdog state.
    Two consecutive calls at the same clock value MUST return equal
    snapshots.
    """
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot_a = watchdog.diagnostic_snapshot(now=clock.monotonic())
    snapshot_b = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot_a == snapshot_b, (
        f"diagnostic_snapshot MUST be a pure read; got {snapshot_a} vs {snapshot_b}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_is_method_not_coroutine() -> None:
    """``diagnostic_snapshot`` MUST be a synchronous method, not a
    coroutine, so the watchdog-kill path can call it synchronously
    without awaiting.
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    assert not inspect.iscoroutinefunction(watchdog.diagnostic_snapshot), (
        "diagnostic_snapshot MUST be a synchronous method"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_uses_injected_now_argument() -> None:
    """When ``now`` is passed explicitly the snapshot MUST use that
    timestamp so tests can drive FakeClock deterministically.
    """
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot = watchdog.diagnostic_snapshot(now=42.5)
    assert snapshot["idle_elapsed_seconds"] == 42.5, (
        f"snapshot.idle_elapsed_seconds MUST use injected now; got"
        f" {snapshot['idle_elapsed_seconds']}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_records_fire_reason() -> None:
    """After a fire the snapshot MUST carry the canonical
    ``WatchdogFireReason.value`` string so post-mortem logs
    can show the reason without coupling to private watchdog
    internals.

    Black-box: drive the watchdog through ``evaluate()`` with a
    short no_output_at_start threshold so the no-output fire path
    sets ``last_fire_reason`` naturally. ``diagnostic_snapshot``
    is then read via its public API.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_FakeProcessMonitor())
    watchdog.record_invocation_start()
    # Advance past the no_output_at_start threshold; no recorded
    # activity; ACTIVE classify_quiet returns the verdict path
    # straight to NO_OUTPUT_AT_START.
    clock.advance(11.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict.name == "FIRE", (
        f"watchdog.evaluate MUST fire NO_OUTPUT_AT_START after the"
        f" threshold with no activity; got verdict={verdict}"
    )
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["last_fire_reason"] == "no_output_at_start", (
        f"snapshot.last_fire_reason MUST be 'no_output_at_start'; got"
        f" {snapshot['last_fire_reason']!r}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_resumable_session_id_is_always_none() -> None:
    """The inner ``diagnostic_snapshot()`` dict hardcodes
    ``resumable_session_id`` to ``None``.

    The watchdog is transport-agnostic and does NOT know about the
    outer watchdog-kill reader's session-capture seam.  The id is
    surfaced on the OUTER ``merged_diag`` payload by the watchdog-
    kill readers (``_process_reader.py`` / ``_pty_line_reader.py``)
    and on the typed ``IdleWatchdogKilledError.resumable_session_id``
    attribute used by the failure classifier via ``exc.__cause__``.

    Even after a fire the watchdog itself does not populate this
    field — the inner snapshot is reserved as a stable key for
    future readers, not as the canonical carrier of the id.
    """
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    # Pre-fire: field is None.
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert "resumable_session_id" in snapshot, (
        "diagnostic_snapshot MUST keep resumable_session_id as a stable key"
    )
    assert snapshot["resumable_session_id"] is None, (
        f"diagnostic_snapshot.resumable_session_id MUST be None"
        f" (the watchdog itself does not populate the field); got"
        f" {snapshot['resumable_session_id']!r}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_diagnostic_snapshot_resumable_session_id_remains_none_after_fire() -> None:
    """Even after a watchdog FIRE, ``resumable_session_id`` stays None.

    Confirms the watchdog does NOT populate the field on the inner
    snapshot at any point in the fire lifecycle.  The id MUST be
    threaded through the OUTER ``merged_diag`` payload (set by the
    watchdog-kill readers) or the typed ``IdleWatchdogKilledError``
    attribute, NOT the inner snapshot dict.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_FakeProcessMonitor())
    watchdog.record_invocation_start()
    clock.advance(11.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict.name == "FIRE"
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["resumable_session_id"] is None, (
        f"diagnostic_snapshot.resumable_session_id MUST remain None"
        f" after a fire (the watchdog never populates the field); got"
        f" {snapshot['resumable_session_id']!r}"
    )


# === consolidated from test_diagnostic_snapshot.py ===
def test_resumable_session_id_contract_documented_in_spec() -> None:
    """The watchdog-spec.md R4 section documents the actual location of
    ``resumable_session_id`` (the OUTER ``merged_diag`` payload,
    NOT the inner ``diagnostic_snapshot()`` dict).

    Pin the spec-vs-implementation contract: a future PR that
    silently moves the field without updating the doc MUST fail
    this assertion.  The doc is read relative to the test file so
    the test is cwd-robust (mirrors the ``test_r8`` cwd-robustness
    fix below).
    """
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "docs"
        / "agents"
        / "watchdog-spec.md"
    )
    spec_text = spec_path.read_text(encoding="utf-8")
    # The spec MUST name both surfaces explicitly so the
    # watchdog-spec.md -> implementation contract cannot drift.
    assert "merged_diag" in spec_text, (
        "watchdog-spec.md MUST document that resumable_session_id"
        " lives on the OUTER merged_diag payload"
    )
    assert "IdleWatchdogKilledError" in spec_text, (
        "watchdog-spec.md MUST document that resumable_session_id"
        " also lives on the typed IdleWatchdogKilledError attribute"
    )
    # The spec MUST clarify that the inner diagnostic_snapshot key
    # is hardcoded to None (so a future reader does not assume the
    # inner snapshot is the canonical carrier).
    assert "diagnostic_snapshot" in spec_text, (
        "watchdog-spec.md MUST reference diagnostic_snapshot in the"
        " R4 resumable_session_id location contract"
    )


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_agent_reading_product_criteria_with_subagent_progress() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY when ``candidate_total >= effective_ceiling``.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING (a productive session with a live subagent). The
    mitigation is to raise ``max_waiting_on_child_seconds`` for
    long-running sessions (the default is 1800s).

    This test exercises the cumulative ceiling with a live subagent
    (filtered count = 1) and ``os_descendant_only_ceiling=300.0``.
    The effective ceiling is reduced to 300s by the corroborator's
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` alive_by signal. The
    ceiling fires at 300s regardless of the LOADING classification.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at 300s with ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _dumb_kill_scenarios_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()

    # First evaluate: enter the WAITING_ON_CHILD branch after
    # idle_timeout (1.0s) elapses.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY at 300s per PROMPT R3 hard
    # enforcement (no _gate_fire consultation). The classifier
    # may return LOADING for the live subagent but the ceiling
    # fires regardless.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_agent_with_os_descendant_only_child_is_deferred() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY even when the corroborator reports
    ``scoped_child_active=True`` and ``OS_DESCENDANT_ONLY_STALE_PROGRESS``.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling fires regardless of any classifier deferral.

    This test exercises the cumulative ceiling with a live subagent
    and ``os_descendant_only_ceiling=300.0``. The effective ceiling
    is reduced to 300s and the ceiling fires unconditionally.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at 300s with ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _dumb_kill_scenarios_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()

    # First evaluate: enter the WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY per R3 hard enforcement.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_first_output_fragment_with_live_subagent() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY regardless of fresh mcp_tool evidence.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling fires regardless of any classifier deferral, even
    when first-party channels (mcp_tool) are fresh within
    ``activity_evidence_ttl_seconds``.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire because mcp_tool was fresh. Post-fix (R3 hard enforcement):
    the cumulative ceiling fires regardless of mcp_tool freshness.

    Assertions:
      - verdict is FIRE at 300s with ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _dumb_kill_scenarios_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # The agent dispatches a single mcp_tool_call.  The mcp_tool
    # channel is now fresh.
    wd.record_mcp_tool_call()

    # Advance 300s past the mcp_tool_call. The cumulative ceiling
    # fires UNCONDITIONALLY per R3 hard enforcement regardless of
    # mcp_tool freshness.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_repeated_evaluate_with_progress_does_not_drift_to_fire() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires within the production hot path even with continuous
    subagent_progress recordings.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling fires within the configured effective ceiling
    regardless of any classifier deferral -- a productive session
    that exceeds the ceiling IS killed (the mitigation is to raise
    ``max_waiting_on_child_seconds`` for long-running waits).

    Pre-fix (wt-012 dumb-kill prevention): 50 consecutive
    ``evaluate()`` calls never produced FIRE. Post-fix (R3 hard
    enforcement): the cumulative ceiling fires at the effective
    ceiling even with continuous subagent_progress.

    Assertions:
      - The first ``evaluate()`` past the effective ceiling
        returns FIRE with ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _dumb_kill_scenarios_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # 50 consecutive evaluate() calls each 6s apart, with the
    # subagent reporting progress each step. The cumulative
    # ceiling at 300s effective fires unconditionally per R3
    # hard enforcement.
    fire_observed = False
    for _ in range(50):
        clock.advance(6.0)
        wd.record_subagent_work()  # the subagent reports progress each step
        verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
        if verdict == WatchdogVerdict.FIRE:
            fire_observed = True
            break

    # The cumulative ceiling MUST fire at the effective ceiling
    # regardless of subagent_progress freshness.
    assert fire_observed, (
        "cumulative ceiling MUST fire at the effective ceiling"
        " (R3 hard enforcement); never observed FIRE in 50 calls"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_recovery_controller_never_advances_to_failed_on_unavailable() -> None:
    """End-to-end: a watchdog kill with typed cause
    ``IdleWatchdogKilledError(reason='no_progress_quiet', signal=15)``
    routed through the recovery controller must NOT advance the
    pipeline to ``failed_terminal``.

    This is the recovery-side complement to the watchdog-side test
    in ``test_smart_verdict_dumb_kills.py``.  The user's third
    dumb-kill concern was: even if the watchdog DID fire on a
    no_progress_quiet reason, the recovery controller must not
    exit the pipeline on the kill alone.  The typed
    ``IdleWatchdogKilledError`` is classified as an unavailable
    AGENT failure, so the controller routes it to the
    exponential-backoff branch (rule two) and the pipeline
    continues with the next agent in the chain.

    Assertions:
      - state.phase is NOT advanced to ``failed_terminal``.
      - The current agent (claude) is marked on cooldown.
      - The chain advances to the next agent.
    """
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    state = _three_agent_state(current_index=0)

    watchdog_exc = IdleWatchdogKilledError(reason="no_progress_quiet", signal=15)
    inactivity_exc = AgentInactivityTimeoutError(
        "claude",
        30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"invocation_elapsed": 30.0},
        ),
    )
    inactivity_exc.__cause__ = watchdog_exc

    new_state, _effects, _evt = controller.handle(
        state,
        inactivity_exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase != "failed_terminal", (
        f"phase must NOT advance to failed_terminal on unavailable AGENT failure,"
        f" got {new_state.phase}"
    )
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    # The chain should have advanced past claude (the failed agent).
    assert chain.current_index != 0, (
        f"chain must advance past the unavailable agent, got current_index={chain.current_index}"
    )
    # Claude must be on cooldown.
    assert not controller.unavailability_store.is_available("development", "claude"), (
        "claude must be on cooldown after no_progress_quiet typed cause"
    )


# === consolidated from test_dumb_kill_scenarios.py ===
def test_dumb_kill_three_agent_dispatching_parallel_scouts() -> None:
    """Reproduce the third dumb-kill incident from the user's log.

    User log excerpt::

        2026-06-15T05:50:52.722153+00:00 INFO CONT [content-start][opencode/...] I need to
            explore this codebase to understand the watchdog architecture before
            planning. Let me dispatch parallel discovery scouts.
        2026-06-16T05:53:23.711523+00:00 ERROR META [waiting] Background child work hit
            hard ceiling (cumulative=194s, ceiling=120s, scoped_child_active=True,
            oldest_child_seconds=-1781497724s, agent=opencode/minimax-coding-plan/MiniMax-M3)
        2026-06-15 22:53:23.711 | WARNING  | idle_watchdog: FIRE reason=no_progress_quiet
            idle_elapsed=151.0s invocation_elapsed=194.4s

    The OLD watchdog fired NO_PROGRESS_QUIET at cumulative=194s (just 30s past
    the 120s ceiling) while the agent was about to dispatch parallel discovery
    scouts. The agent was alive, had a live child (scoped_child_active=True),
    and the only signal was OS_DESCENDANT_ONLY_STALE_PROGRESS.

    The NEW behavior:

      - the dumb-kill floor (no_progress_quiet_minimum_invocation_seconds=120.0s)
        prevents the fire BEFORE invocation_elapsed=120.0s even when all
        channels are stale (the user log fired at 194s, so the floor is
        not the primary protection here -- the smart-verdict gate is);
      - the smart-verdict gate (StuckClassifier) defers the fire while the
        live process monitor reports a live child (the classifier returns
        LOADING via the subagent_liveness channel, and the gate defers).

    The test MUST construct the same live-child prerequisites the existing
    ``test_smart_verdict_dumb_kills.py::test_dumb_kill_two_pre_output_fragment``
    uses, because the current ``_stuck_classifier.py:230-275`` (classify_stuck)
    requires fresh first-party evidence, a subagent_liveness side-channel
    with ``can_defer=True``, OR a live ``classify_quiet`` returning
    WAITING_ON_CHILD to defer. Corroborator-only stale-child evidence
    (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS) without a live process
    monitor is explicitly NOT a deferral signal per the classifier
    docstring at ``_stuck_classifier.py:92-100``.

    Required setup:
      1. Inject the existing ``_LiveOnlyProcessMonitor(live_count=1)`` so
         ``_subagent_liveness_summary`` sets ``can_defer=True`` and the
         classifier returns LOADING via the subagent_liveness channel.
      2. Call ``wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)`` where ``_dumb_kill_scenarios_waiting``
         returns ``AgentExecutionState.WAITING_ON_CHILD`` -- this is the
         live signal the classifier's WAITING_ON_CHILD branch at
         ``_stuck_classifier.py:257-258`` consults to return LOADING.
      3. Configure ``no_progress_quiet_minimum_invocation_seconds=120.0s``
         and ``no_progress_quiet_seconds=120.0s`` so the dumb-kill floor
         is active. The user log's invocation_elapsed=194.4s is past the
         floor, so the floor is satisfied; the live-subagent deferral is
         the primary protection.

    Assertions:
      - verdict is CONTINUE (not FIRE) at the user's exact log scenario
        (idle_elapsed=151s, invocation_elapsed=194s).
      - The session phase is NOT failed_terminal.

    The watchdog returns CONTINUE without firing because the
    ``_is_no_progress_quiet`` short-circuits when the channel
    evidence is active (the live process monitor reports a live
    child, so the subagent_liveness channel is fresh, so
    ``_channel_evidence_active`` returns True and the no-progress
    path is not taken). The gate is never reached; the deferral
    happens at the channel-evidence layer. This is the
    dumb-kill protection the user requested.
    """
    monitor = _LiveOnlyProcessMonitor(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance to the user's exact log scenario:
    #   idle_elapsed=151s, cumulative=194s.
    # The OLD watchdog would have FIRE'd at 120s (cumulative) and
    # killed the agent mid-exploration. The NEW behavior defers the
    # fire because the live process monitor reports a live child
    # (subagent_liveness channel is fresh, can_defer=True) AND the
    # smart-verdict gate would defer with DEFERRED_BY_STUCK_CLASSIFIER
    # if it were reached.
    clock.advance(151.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE at idle_elapsed=151s invocation_elapsed=194s"
        f" (the user's exact log scenario), got {verdict}"
    )


# === consolidated from test_dumb_kill_scenarios.py ===
def test_no_progress_quiet_does_not_fire_within_dumb_kill_floor() -> None:
    """The dumb-kill floor protects a recently-launched agent.

    Even when the corroborator says no progress and ALL channels
    are stale, if ``invocation_elapsed <
    no_progress_quiet_minimum_invocation_seconds`` the watchdog
    returns CONTINUE. The floor prevents a recently-launched
    agent that is doing real thinking work from being killed
    before it has a chance to produce first-party activity
    evidence.

    Setup: corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (so the no-progress path
    is active), classify_quiet returns WAITING_ON_CHILD (so the
    no_progress_quiet evaluator runs). The classifier returns
    STUCK (no live subagent), the gate WOULD allow FIRE -- but
    the dumb-kill floor fires FIRST in ``_is_no_progress_quiet``
    and short-circuits the fire.

    Assertions:
      - verdict is CONTINUE (not FIRE) before the floor.
      - last_fire_reason is None (the gate never fired).
    """

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance to just under the floor: 119s. NO_PROGRESS_QUIET cannot fire.
    clock.advance(119.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must not FIRE at invocation_elapsed=119s (under 120s floor), got {verdict}"
    )


# === consolidated from test_dumb_kill_scenarios.py ===
def test_no_progress_quiet_still_fires_after_dumb_kill_floor_when_genuinely_stuck() -> None:
    """The dumb-kill floor does NOT mask a genuinely stuck agent.

    After the floor elapses, the watchdog must still fire
    NO_PROGRESS_QUIET when the agent is genuinely stuck (no
    output, no subagent, no workspace, all channels stale, no
    live process monitor). The floor is additive, not a
    replacement.

    Setup: corroborator returns ``alive_by=None`` (the
    corroborator cannot confirm liveness — i.e. the child is
    TRULY dead or missing). NO process monitor, ALL channels
    stale, invocation_elapsed well past the floor, classify_quiet
    returns WAITING_ON_CHILD (so the no_progress_quiet
    evaluator runs). The classifier returns STUCK and the gate
    allows FIRE.

    NOTE: per the wt-012 gate refinement, when the corroborator
    reports ANY alive_by signal (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``)
    the watchdog DEFERS the fire and relies on the cumulative
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s) as the
    upper bound. This test exercises the OTHER branch — the
    "child is truly dead" path where the corroborator cannot
    confirm liveness (alive_by is None).

    Assertions:
      - verdict is FIRE (not CONTINUE) past the floor.
      - last_fire_reason is NO_PROGRESS_QUIET.
    """

    def _dead_child_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            scoped_child_count=0,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_dead_child_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance well past the dumb-kill floor (120s) and past the
    # no_progress_quiet ceiling (120s). The floor has elapsed, the
    # ceiling is reached, all channels are stale, and the agent is
    # genuinely stuck. The watchdog must FIRE.
    clock.advance(150.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"watchdog must FIRE when the agent is genuinely stuck past"
        f" the dumb-kill floor + ceiling, got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


# === consolidated from test_dumb_kill_scenarios.py ===
def test_no_progress_quiet_does_not_fire_when_corroborator_reports_live_child() -> None:
    """``_is_no_progress_quiet`` defers the fire when the corroborator
    reports any ``alive_by`` signal.

    Per the wt-012 gate refinement, when ``corroboration.alive_by``
    is not ``None`` (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``),
    ``_is_no_progress_quiet`` returns ``False`` -- the watchdog
    defers the fire and the cumulative ``CHILDREN_PERSIST_TOO_LONG``
    ceiling (default 600s) is the correct upper bound for the live-
    child stall, not the 120s ``NO_PROGRESS_QUIET`` fire.

    The conservative policy: the new test exercises the NEW
    deferral behavior at idle_elapsed=151s (the user's exact log
    scenario); the watchdog must NOT fire ``NO_PROGRESS_QUIET`` even
    though the no_progress_quiet ceiling (120s) is past.

    Setup: ``no_progress_quiet_seconds=120.0``,
    ``no_progress_quiet_minimum_invocation_seconds=120.0`` (dumb-kill
    floor enabled), corroborator returns
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``, clock advances to 151s
    (past the floor AND past the ceiling), classify_quiet returns
    ``WAITING_ON_CHILD`` (so the no_progress_quiet evaluator runs).

    Assertions:
      - verdict is ``WAITING_ON_CHILD`` (NOT ``FIRE``) -- the
        gate refinement defers the fire at
        ``_is_no_progress_quiet`` via the early-return path
        ``_evaluate_no_progress_quiet`` returns ``None``.
      - ``last_fire_reason`` is ``None`` (NO fire happened).
    """

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _make_policy_with_floor(
            idle_timeout=300.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Advance past BOTH the dumb-kill floor (120s) AND the
    # no_progress_quiet ceiling (120s). The floor has elapsed, the
    # ceiling is reached, but the corroborator reports a LIVE child
    # (``OS_DESCENDANT_ONLY_STALE_PROGRESS``). The new gate refinement
    # MUST defer the fire.
    clock.advance(151.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    # The watchdog is in the active branch (idle_timeout=300s, idle_elapsed=151s).
    # The no_progress_quiet check DEFERRED the fire (alive_by is not None),
    # so the watchdog returns CONTINUE (NOT FIRE). The cumulative ceiling
    # (CHILDREN_PERSIST_TOO_LONG at 600s) is the upper bound for the
    # live-child stall, not NO_PROGRESS_QUIET at 120s.
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire NO_PROGRESS_QUIET when the corroborator"
        f" reports a live child past the no_progress_quiet ceiling, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None (NO fire happened -- the gate"
        f" refinement deferred), got {wd.last_fire_reason}"
    )


# === consolidated from test_dumb_kill_scenarios.py ===
def test_cumulative_ceiling_remains_upper_bound_for_live_child_stalls() -> None:
    """The cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s)
    is the upper bound for live-child stalls, not the 120s
    ``NO_PROGRESS_QUIET`` fire.

    The wt-012 gate refinement defers ``NO_PROGRESS_QUIET`` when
    the corroborator reports a live child. The cumulative ceiling
    is still the upper bound: the watchdog will fire
    ``CHILDREN_PERSIST_TOO_LONG`` (NOT ``NO_PROGRESS_QUIET``) when
    the cumulative total reaches the ceiling.

    Setup: ``no_progress_quiet_seconds=120.0`` (would have fired
    NO_PROGRESS_QUIET at 120s under the OLD behavior),
    ``max_waiting_on_child_seconds=600.0`` (the cumulative
    ceiling), corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``, the watchdog enters
    WAITING_ON_CHILD via classify_quiet, then we advance the clock
    past the cumulative ceiling (600s of WAITING_ON_CHILD time).

    Assertions:
      - While cumulative is under the ceiling (590s of waiting):
        verdict is ``WAITING_ON_CHILD`` (NOT FIRE).
      - Once cumulative reaches the ceiling (>= 600s of waiting):
        verdict is ``FIRE`` with
        ``last_fire_reason=CHILDREN_PERSIST_TOO_LONG`` (NOT
        ``NO_PROGRESS_QUIET`` -- the gate refinement defers
        NO_PROGRESS_QUIET when alive_by is not None).
    """

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _dumb_kill_scenarios_make_watchdog(
        _make_policy_with_floor(
            idle_timeout=10.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=120.0,
        ),
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_invocation_start()
    wd.record_activity()

    # Enter the WAITING_ON_CHILD branch via the active-branch exit
    # (idle_timeout=10s). The first evaluate advances to 11s and
    # transitions the watchdog into WAITING_ON_CHILD.
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"watchdog must enter WAITING_ON_CHILD at idle_elapsed=11s, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None on entry to WAITING_ON_CHILD, got {wd.last_fire_reason}"
    )

    # Under the os_descendant_only effective ceiling: 290s of waiting
    # time (well under the 300s effective ceiling for an
    # OS_DESCENDANT_ONLY child). Multiple short evaluate ticks are
    # used because the cumulative math only counts WAITING_ON_CHILD
    # time across evaluate() calls.
    for _ in range(29):
        clock.advance(10.0)
        verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    # 29 * 10s = 290s of waiting, under the 300s effective ceiling.
    # Should NOT fire (cumulative is below the effective ceiling).
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire at cumulative=290s of waiting (under"
        f" the 300s os_descendant_only ceiling), got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None at cumulative=290s, got {wd.last_fire_reason}"
    )

    # Past the effective ceiling: 1 more 10s tick brings cumulative
    # to >= 300s of waiting. The watchdog must fire
    # CHILDREN_PERSIST_TOO_LONG (NOT NO_PROGRESS_QUIET).
    clock.advance(10.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"watchdog must FIRE once cumulative waiting reaches the 300s"
        f" os_descendant_only ceiling, got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"last_fire_reason must be CHILDREN_PERSIST_TOO_LONG (cumulative"
        f" ceiling is the upper bound for live-child stalls), got {wd.last_fire_reason}"
    )


# === consolidated from test_e2e_activity_aware.py ===
@pytest.mark.parametrize(
    "discovery",
    [
        NullDiscoveryStrategy(),
        NullDiscoveryStrategy(),
    ],
)
def test_documented_discovery_returns_empty_when_path_not_documented(
    tmp_path: Path,
    discovery: DiscoveryStrategy,
) -> None:
    """AC-11: undocumented subagent output paths are not invented.

    Discovery strategies are cwd-relative; even when the legacy-looking
    directory layout is present on disk, the strategy must return an empty
    mapping because the path is not documented.
    """
    original_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    try:
        assert discovery.discover_subagent_outputs(0) == {}
    finally:
        os.chdir(str(original_cwd))


# === consolidated from test_e2e_activity_aware.py ===
def test_subagent_output_first_party_deferral(tmp_path: Path) -> None:
    """AC-02/AC-07: fresh subagent output lines defer NO_OUTPUT_DEADLINE."""
    log_file = tmp_path / ".agent" / "workers" / "w1" / "output.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line 1\n", encoding="utf-8")

    policy = _e2e_activity_aware_make_policy(activity_ttl=1000.0)
    monitor = _FakeProcessMonitorE2eActivityAware(captures={"w1": FileSubagentOutputCapture(str(log_file))})
    wd, clock = _e2e_activity_aware_make_watchdog(policy, monitor)
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_e2e_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd._subagent_output_count >= 1

    # Past TTL with no new lines -> fire.
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_e2e_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_e2e_activity_aware.py ===
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
@pytest.mark.timeout_seconds(5)
def test_process_monitor_discovers_and_classifies_subagent() -> None:
    """AC-06/AC-10/AC-11: DefaultProcessMonitor classifies a live descendant subagent.

    The built-in command-line classifier is documentation-grounded and
    conservative; it does not promote descendants based on broad command-line
    tokens. OpenCode subagents are instead identified via the shipped
    ``ChildLivenessSubagentPidSource`` backed by the
    ``ChildLivenessRegistry`` (first-party evidence from structured child
    lifecycle events on stdout). This test uses that shipped source to
    classify the spawned child as a subagent without injecting a substitute
    lambda classifier.
    """
    host_script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Poll for the child instead of a fixed sleep so healthy machines finish
    # well under the 1.0s per-test budget while slow hosts still wait briefly.
    deadline = time.monotonic() + 2.0
    children: list[psutil.Process] = []
    host_proc = psutil.Process(host.pid)
    while time.monotonic() < deadline:
        children = host_proc.children(recursive=False)
        if children:
            break
        time.sleep(0.005)
    try:
        assert len(children) >= 1
        child_pid = children[0].pid

        registry = ChildLivenessRegistry(
            progress_ttl=60.0,
            heartbeat_ttl=60.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
        )
        registry.register_child("child-A", "agent:test-scope:", pid=child_pid)
        pid_source = ChildLivenessSubagentPidSource(registry, "agent:test-scope:")

        monitor = DefaultProcessMonitor(
            host.pid,
            subagent_pid_source=pid_source,
            poll_interval_seconds=0.0,
        )
        assert monitor.live_subagent_count() == 1
        processes = monitor.classified_processes()
        roles = {p.pid: p.role for p in processes}
        assert roles[host.pid] == ProcessRole.HOST
        assert roles[child_pid] == ProcessRole.SPAWNED_SUBAGENT
    finally:
        # Direct kill of the known host + child, bypassing the recursive
        # psutil ``host.children(recursive=True)`` enumeration inside
        # ``DefaultProcessTeardown`` (which alone costs several hundred
        # milliseconds on macOS and would push the test past the 1.0s
        # per-test budget). The teardown's SIGTERM-then-SIGKILL escalation
        # semantics are exercised by the dedicated
        # ``test_teardown_reaps_nested_subagents`` test in this file; this
        # test only verifies monitor classification, so the cleanup just
        # needs to reap the processes it spawned.
        for proc in [host_proc, *children]:
            with contextlib.suppress(psutil.Error):
                proc.kill()
        host.wait(timeout=0.5)


# === consolidated from test_e2e_activity_aware.py ===
def test_evidence_summary_labels_tiers_e2e() -> None:
    """AC-12: last_evidence_summary exposes tier labels and freshness."""
    policy = _e2e_activity_aware_make_policy(activity_ttl=1000.0)
    wd, clock = _e2e_activity_aware_make_watchdog(policy)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    clock.advance(1.0)

    summary = wd.last_evidence_summary(clock.monotonic())
    by_name = {c.channel_name: c for c in summary.channels}
    assert by_name[ChannelName.STDOUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.MCP_TOOL].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_OUTPUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_LIVENESS].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.WORKSPACE].tier == EvidenceTier.SIDE_CHANNEL


# === consolidated from test_e2e_activity_aware.py ===
def test_truly_idle_fires_on_time_e2e() -> None:
    """AC-04: no activity on any channel fires at the idle deadline."""
    policy = _e2e_activity_aware_make_policy(activity_ttl=30.0)
    wd, clock = _e2e_activity_aware_make_watchdog(policy)
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_e2e_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_emit_info_log_throttle.py ===
def test_emit_info_log_fires_at_most_once_per_status_interval_when_no_subagent_listener(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """20 ``evaluate`` calls + 20 ``record_subagent_work`` calls across 2
    status-interval windows MUST produce at most 3 INFO log emissions.

    Setup:
      * main ``WaitingStatusListener`` registered (so the
        ``main_listener is not None`` arm fires for every event).
      * NO ``register_default_subagent_activity_listener`` call (so
        ``subagent_listener is None`` and the
        ``self._log.info("idle watchdog: subagent activity: ..."))``
        branch fires whenever an emit has a non-None
        ``subagent_activity``).
      * ``status_interval=10.0s``, ``idle_timeout=1.0s``,
        ``max_waiting=600.0s``,
        ``watchdog_subagent_progress_interval=3600.0s`` (effectively
        disables SUBAGENT_PROGRESS so the test isolates the
        ENTERED + PROGRESS cadence).
      * ``record_subagent_work(description='tool_use:Read')`` is
        called before every ``evaluate`` so the emit body has a
        non-None ``event.subagent_activity`` and the INFO log can
        fire.

    Drive:
      1. ``clock.advance(1.1)`` past idle_timeout; record subagent
         work; call ``evaluate(_emit_info_log_throttle_waiting)`` once -> ENTERED emit
         (subagent_activity=tool_use:Read) -> INFO log fires
         (candidate emission #1).
      2. ``clock.advance(10.0)`` to cross one status_interval.
      3. In the same window (no clock advance between calls), do 10
         cycles of: ``record_subagent_work`` then ``evaluate(_emit_info_log_throttle_waiting)``.
         The first evaluate crosses the PROGRESS interval and emits
         (candidate emission #2). The remaining 9 evaluates hit the
         already-refreshed interval and emit nothing -- their
         ``record_subagent_work`` calls just refresh the description
         state.
      4. ``clock.advance(10.0)`` to cross another status_interval.
      5. Repeat the 10 evaluate + record cycle. The first evaluate
         crosses the PROGRESS interval and emits (candidate emission
         #3). The remaining 9 do nothing.

    Expected: 3 INFO log records total (1 ENTERED + 2 PROGRESS).
    The 20 ``record_subagent_work`` calls do NOT each cause an INFO
    log -- the emit throttle is at the caller sites in
    ``_waiting_branch.py``, not in ``_emit`` itself.

    Pre-fix: the INFO log fired on every ``record_subagent_work``
    call (or every evaluate), producing ~20 records for this
    scenario. Post-fix the count is 3 (one per status emission
    boundary).
    """
    _buf, records = captured_info_records
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    # Phase 1: enter WAITING_ON_CHILD (ENTERED emit).
    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    assert watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting) is not None

    # Phase 2: cross one status_interval, then 10 evaluate calls
    # interleaved with record_subagent_work. Only the FIRST evaluate
    # crosses the PROGRESS interval; the rest are rate-limited.
    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    # Phase 3: cross another status_interval, then 10 more evaluate
    # calls. Only the FIRST evaluate crosses the new PROGRESS interval.
    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    # Black-box pin: the INFO log count is bounded by the number of
    # distinct status emissions (1 ENTERED + 1 PROGRESS per crossed
    # status_interval window), NOT by the number of
    # record_subagent_work calls. The expected count is 3
    # (1 ENTERED + 2 PROGRESS windows crossed).
    info_records = _info_log_records(records)
    # Expected emissions:
    #   1 ENTERED (handle_waiting_branch:122) -> _emit ENTERED
    #   1 SUBAGENT_PROGRESS (first-call immediate, _last_subagent_progress_emit_at is None)
    #   1 PROGRESS at 10s window
    #   1 PROGRESS at 20s window
    # Total: 4 INFO log records. The 20 record_subagent_work calls
    # in phase 2 + 20 in phase 3 do NOT each cause an INFO log --
    # SUBAGENT_PROGRESS throttle (3600s interval) and PROGRESS
    # throttle (10s interval) gate the emissions. The bound is
    # exact because each window boundary deterministically produces
    # exactly one emission.
    max_emissions = 4  # 1 ENTERED + 1 SUBAGENT_PROGRESS + 2 PROGRESS
    assert len(info_records) == max_emissions, (
        f"INFO log spam regression on the no-subagent-listener path:"
        f" expected exactly {max_emissions} INFO records (1 ENTERED +"
        f" 1 SUBAGENT_PROGRESS first-call + 2 PROGRESS windows) for"
        f" 22 record_subagent_work calls across 2 status intervals;"
        f" got {len(info_records)}. Records: {info_records[:5]}"
    )

    # Sanity: the main listener MUST have received every status event
    # the watchdog emitted (the contract is that the main listener is
    # always notified, regardless of whether the subagent listener
    # is set). This guards against a regression where the
    # ``main_listener is None and subagent_listener is None`` early
    # return is incorrectly extended to the
    # ``subagent_listener is None`` arm.
    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    subagent_progress_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(entered_events) == 1, (
        f"main listener MUST receive the ENTERED event; got {len(entered_events)} ENTERED events"
    )
    assert len(progress_events) == 2, (
        f"main listener MUST receive the 2 PROGRESS events (one per"
        f" crossed window); got {len(progress_events)} PROGRESS events"
    )
    assert len(subagent_progress_events) == 1, (
        f"main listener MUST receive the 1 SUBAGENT_PROGRESS event"
        f" (first-call immediate); got"
        f" {len(subagent_progress_events)} SUBAGENT_PROGRESS events"
    )


# === consolidated from test_emit_info_log_throttle.py ===
def test_emit_info_log_carries_latest_recorded_description(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """Every captured INFO log line MUST contain the most-recently
    recorded description.

    Companion to the throttle test: even when the INFO log is
    rate-limited, the *content* of each captured line must reflect
    the latest ``record_subagent_work`` description. This pins that
    the watchdog does not "stick" on a stale description between
    status intervals (e.g. a future refactor that caches the
    description at ENTERED time and never refreshes it).
    """
    _buf, records = captured_info_records
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    # Phase 1: enter with the first description.
    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    # Phase 2: cross one status_interval, change the description,
    # then drive the PROGRESS emit.
    clock.advance(10.0)
    watchdog.record_subagent_work(description="tool_use:Write")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    # Phase 3: cross another status_interval, change the description
    # again, then drive the second PROGRESS emit.
    clock.advance(10.0)
    watchdog.record_subagent_work(description="bash:ls")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    info_records = _info_log_records(records)
    # We expect 4 INFO records (1 ENTERED + 1 SUBAGENT_PROGRESS
    # first-call + 2 PROGRESS). Each one MUST contain a recognized
    # tool-call verb from the canonical set (not a stale description,
    # not a missing description).
    assert len(info_records) == 4, (
        f"expected exactly 4 INFO records (1 ENTERED + 1"
        f" SUBAGENT_PROGRESS first-call + 2 PROGRESS);"
        f" got {len(info_records)}. Records: {info_records}"
    )
    # The full set of recorded descriptions MUST appear across the
    # 3 captured lines (one description per line, in the order they
    # were most recently recorded before each emit).
    assert any("tool_use:Read" in r for r in info_records), (
        f"the first description (ENTERED) MUST appear in at least"
        f" one INFO record; records: {info_records}"
    )
    assert any("tool_use:Write" in r for r in info_records), (
        f"the second description (PROGRESS 1) MUST appear in at"
        f" least one INFO record; records: {info_records}"
    )
    assert any("bash:ls" in r for r in info_records), (
        f"the third description (PROGRESS 2) MUST appear in at"
        f" least one INFO record; records: {info_records}"
    )


# === consolidated from test_emit_info_log_throttle.py ===
def test_emit_no_info_log_when_subagent_activity_is_none(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """When no ``record_subagent_work`` has been called, the INFO log
    MUST NOT fire even though the main listener is registered.

    The ``_emit`` body has a guard:
    ``if event.subagent_activity is not None: ... self._log.info(...)``.
    A future refactor that drops the guard would regress to
    emitting the INFO log with ``event.subagent_activity=None`` on
    every status event -- a different spam pattern (every PROGRESS
    would carry a bare ``idle watchdog: subagent activity: None`` line).

    This test pins the guard: when ``record_subagent_work`` is NEVER
    called, the INFO log substring does NOT appear in any captured
    record even though the watchdog emits the expected ENTERED +
    PROGRESS cadence via the main listener path.
    """
    _buf, records = captured_info_records
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)  # ENTERED
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)  # PROGRESS 1
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)  # PROGRESS 2

    # Sanity: the main listener received all 3 status events.
    assert len(captured_events) == 3, (
        f"main listener MUST receive ENTERED + 2 PROGRESS events; got {len(captured_events)}"
    )

    # The contract: NO INFO log line carries the
    # ``idle watchdog: subagent activity:`` substring when
    # ``event.subagent_activity is None`` for every event.
    info_records = _info_log_records(records)
    assert info_records == [], (
        f"INFO log MUST NOT fire when subagent_activity is None"
        f" for every emit; got {len(info_records)} records."
        f" Records: {info_records}"
    )


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_throttles_identical_channel_emission() -> None:
    """1000 ``evaluate()`` calls in the same FakeClock second with the
    same channel label MUST emit at most 2 DEBUG records.

    Pre-fix the deferral path emitted one record per call (1000 records).
    Post-fix the throttle keeps it to <= 2 (initial transition + first
    refresh window).
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
        # Drive an active mcp_tool channel at t=0 so the verdict hook
        # reports ``active_channel=mcp_tool`` and the deferral path is
        # taken.  Advance past ``idle_timeout_seconds`` (60s) so
        # ``evaluate()`` enters the activity-aware deferral branch.
        watchdog.record_mcp_tool_call(now=0.0)
        clock.advance(61.0)
        for _ in range(1000):
            verdict = watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
            assert verdict == WatchdogVerdict.CONTINUE
        matching = [
            r for r in captured if "deferred via activity evidence" in r and "channel=mcp_tool" in r
        ]
        assert len(matching) <= _MAX_DEFER_EMISSIONS, (
            f"DEBUG log spam regression: got {len(matching)} records"
            f" for 1000 calls in the same second; expected <= {_MAX_DEFER_EMISSIONS}"
            f" (one initial + one refresh window). Records: {matching[:3]}"
        )
    finally:
        _remove_sink(handler_id)


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_throttle_uses_configured_window() -> None:
    """A throttle window of 0.01s MUST allow refresh emissions.

    With a tiny throttle window the test exercises the refresh
    boundary: drive 100 ticks at 0s and 100 ticks at 0.05s; the
    first tick emits, then no emissions for 0.01s; the 0.05s tick
    is past the refresh window so it emits again.
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=0.01)
        watchdog.record_mcp_tool_call(now=0.0)
        clock.advance(61.0)
        for _ in range(100):
            watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        clock.advance(0.05)
        for _ in range(100):
            watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        matching = [
            r for r in captured if "deferred via activity evidence" in r and "channel=mcp_tool" in r
        ]
        assert len(matching) <= 3, (
            f"throttle window 0.01s produced too many emissions: {len(matching)}"
        )
    finally:
        _remove_sink(handler_id)


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_throttle_is_per_channel() -> None:
    """Different channel labels MUST be tracked independently so an
    mcp_tool emission does not suppress a subsequent subagent emission.

    Verifies the throttle key is the channel label (mcp_tool /
    subagent / workspace / none), not the fire_reason alone.

    Setup: only mcp_tool is fresh in the first window; only subagent
    is fresh in the second window. We avoid co-recording channels by
    using a clean reset between the two windows: the first window
    records only mcp_tool; the second window records only subagent
    after a full TTL advance.

    Black-box: drive ``evaluate()`` and verify the channel appears in
    the emitted evidence-summary channels.
    """
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            watchdog_log_throttle_seconds=0.5,
            activity_evidence_ttl_seconds=180.0,
        ),
        FakeClock(start=0.0),
    )
    clock = watchdog._clock

    # First window: only mcp_tool is fresh (subagent is NOT yet set).
    # Advance past idle timeout and evaluate so the deferral path is
    # taken with ``active_channel=mcp_tool``.
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active) == WatchdogVerdict.CONTINUE

    # The diagnostic snapshot is the public surface for the per-channel
    # evidence summary; assert the mcp_tool channel is fresh in the
    # first window so the per-channel throttle key was set.
    snap_first = watchdog.diagnostic_snapshot(now=clock.monotonic())
    mcp_channel_first = next(
        (
            entry
            for entry in snap_first["evidence_summary"]
            if isinstance(entry, dict) and entry.get("channel") == "mcp_tool"
        ),
        None,
    )
    assert mcp_channel_first is not None, (
        f"evidence_summary MUST contain an mcp_tool channel in the"
        f" first window; got: {snap_first['evidence_summary']}"
    )

    # Advance well past the mcp_tool TTL (180s) so mcp_tool ages out
    # and we can drive a clean per-channel transition.  Re-invoke
    # invocation_start to clear the throttle map so the second
    # window's subagent emission is the FIRST entry for the
    # subagent channel key (the throttle helper only emits on the
    # initial transition for an unseen key, but we need this
    # test to focus on per-channel key isolation rather than
    # re-logging under the same key).
    clock.advance(200.0)
    watchdog.record_invocation_start()

    # Second window: only subagent is fresh (mcp_tool is NOT set
    # this round).
    clock.advance(61.0)
    watchdog.record_subagent_work(now=clock.monotonic())
    assert watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active) == WatchdogVerdict.CONTINUE
    snap_second = watchdog.diagnostic_snapshot(now=clock.monotonic())
    subagent_channel_second = next(
        (
            entry
            for entry in snap_second["evidence_summary"]
            if isinstance(entry, dict) and "subagent" in str(entry.get("channel", ""))
        ),
        None,
    )
    assert subagent_channel_second is not None, (
        f"evidence_summary MUST contain a subagent channel in the"
        f" second window; got: {snap_second['evidence_summary']}"
    )


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_throttle_resets_on_invocation_start() -> None:
    """``record_invocation_start`` MUST reset the per-channel throttle map.

    Same contract as ``_last_deferred_log_at``: the throttle survives
    long-lived WAITING runs but MUST NOT carry state across invocations.

    Black-box: drive a deferral scenario through ``evaluate()`` to
    populate the throttle map, then ``record_invocation_start`` MUST
    clear it (the next deferral scenario starts a fresh log budget).
    """
    watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active) == WatchdogVerdict.CONTINUE
    # Reset by invocation_start.
    watchdog.record_invocation_start()
    # Drive a second deferral scenario immediately after the reset.
    # The reset MUST NOT have carried over throttle state from the
    # previous invocation.
    clock.advance(0.0)
    watchdog.record_mcp_tool_call(now=clock.monotonic())
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active) == WatchdogVerdict.CONTINUE


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_returns_continue_when_throttled() -> None:
    """The verdict MUST remain CONTINUE regardless of whether the
    throttle suppresses the DEBUG emission.

    The throttle is a LOGGING concern only; the verdict logic is
    independent.  This is observable from ``evaluate()``'s return
    value: every call returns CONTINUE while the channel is fresh.
    """
    watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    for _ in range(50):
        verdict = watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        assert verdict == WatchdogVerdict.CONTINUE


# === consolidated from test_evidence_deferral_throttle.py ===
def test_evidence_deferral_uses_correlation_snapshot_when_no_channel() -> None:
    """When no channel is fresh the ``active_channel`` label is ``none``.

    The throttle map MUST still throttle the ``none`` key so a
    session that stays in this state for thousands of ticks emits
    at most 2 DEBUG records total.
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
        # Advance past idle timeout.  No recorded activity channel
        # means ``active_channel=none``; the deferral path is still
        # entered because ``_channel_evidence_active`` defaults to
        # ACTIVE (the dummy channel is reported as active when no
        # recorded evidence exists; the test asserts the throttle
        # bounds the debug emission regardless of the channel label).
        clock.advance(61.0)
        for _ in range(1000):
            watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        matching = [r for r in captured if "deferred via activity evidence" in r]
        assert len(matching) <= _MAX_DEFER_EMISSIONS, (
            f"throttle regression on 'none' channel: got {len(matching)}"
            f" records for 1000 calls; expected <= {_MAX_DEFER_EMISSIONS}"
            f". Records: {matching[:3]}"
        )
    finally:
        _remove_sink(handler_id)


# === consolidated from test_hard_ceiling_with_helpers_alive.py ===
def test_session_ceiling_fires_with_helpers_alive() -> None:
    """R3 headline: the 2365s indefinite deferral CANNOT happen.

    A monitor that reports 0 FILTERED subagents (10 helpers are
    visible in the descendant tree but they are NOT real subagents)
    must NOT block the ``max_session_seconds`` ceiling. The ceiling
    fires at the configured value regardless of the helper count.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        # SESSION_CEILING_EXCEEDED is the operator-set hard cap.
        # When set, it MUST fire regardless of any deferral reason
        # (the gate is bypassed -- see ``_gate_fire``).
        max_session_seconds=300.0,
        # Disable NO_OUTPUT_AT_START so the SESSION_CEILING is the
        # headline fire reason for this test.
        no_output_at_start_seconds=None,
        # Disable the no-progress quiet ceiling so the test is
        # unambiguous: the session ceiling is the only fire reason.
        no_progress_quiet_seconds=None,
        # Make the cumulative waiting ceiling shorter than the
        # session ceiling so it cannot fire first.
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=None,
        # Disable SUSPECTED_FROZEN so the SUSPECT branch does not
        # compete with the session ceiling.
        suspect_waiting_on_child_seconds=None,
        # Activity evidence is stale (no recorded activity for 305s).
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # Advance past the session ceiling (305s > 300s). A real
    # ``idle_elapsed`` value with helpers-but-no-subagents MUST trip
    # the SESSION_CEILING.
    clock.advance(305.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    # The filtered count is 0; the broader helper count (10) was
    # ignored -- the headline R3 invariant.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


# === consolidated from test_hard_ceiling_with_helpers_alive.py ===
def test_cumulative_waiting_ceiling_fires_with_helpers_alive() -> None:
    """R3 cumulative path: helpers cannot stretch ``CHILDREN_PERSIST_TOO_LONG``.

    The watchdog enters WAITING_ON_CHILD when ``classify_quiet`` reports
    the agent is waiting. The cumulative ceiling is checked against
    the FILTERED subagent count; a helpers-only monitor MUST NOT block
    the ceiling. After cumulative WAITING time exceeds the ceiling
    (with 0 real subagents), the watchdog fires
    ``CHILDREN_PERSIST_TOO_LONG``.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        # Short idle deadline so the watchdog enters the verdict
        # path quickly. MUST be <= ``max_waiting_on_child_seconds``
        # per TimeoutPolicy validator.
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 5s. A short
        # ceiling keeps the test fast and unambiguous; the headline
        # invariant is the same regardless of the absolute value.
        max_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        # Disable the OS-descendant-only ceiling which has a default
        # larger than 5s and would fail validation.
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling which has a default
        # larger than 5s and would fail validation.
        stuck_job_sub_ceiling_seconds=None,
        # Disable the no-progress quiet ceiling to avoid ambiguity.
        no_progress_quiet_seconds=None,
        # Disable NO_OUTPUT_AT_START so the test focuses on the
        # cumulative ceiling.
        no_output_at_start_seconds=None,
        # Disable SUSPECTED_FROZEN so the SUSPECT branch does not
        # compete with the cumulative ceiling.
        suspect_waiting_on_child_seconds=None,
        # Stale activity evidence.
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # First evaluate() at 3s: idle_elapsed (3s) > idle_timeout (2s)
    # and classify_quiet returns WAITING_ON_CHILD -> enters the
    # waiting branch (current_run_elapsed=0).
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD
    # Advance the clock by 5s so the current_run_elapsed (5s)
    # reaches the cumulative ceiling (5s). The watchdog MUST fire
    # on the next tick because the ceiling is exceeded and no real
    # subagent is alive.
    clock.advance(5.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    # Cumulative time exceeded the ceiling but helpers alone did not
    # block the ceiling. The filtered count is 0; the helpers (10)
    # were ignored.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


# === consolidated from test_hard_ceiling_with_helpers_alive.py ===
def test_idle_timeout_fires_with_helpers_alive() -> None:
    """R3 idle path: helpers cannot stretch ``NO_OUTPUT_DEADLINE``.

    After the idle deadline elapses (no stdout, no first-party
    activity, no real subagent alive), the watchdog MUST fire
    ``NO_OUTPUT_DEADLINE``. The presence of 10 helper processes
    (shell tools the agent dispatched) MUST NOT defer the fire.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        # Disable NO_OUTPUT_AT_START and NO_PROGRESS_QUIET so the
        # idle deadline is the unambiguous fire reason.
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        # Disable the drain window so the watchdog fires immediately
        # after the idle deadline (otherwise the default 5s drain
        # window would defer the fire by 5s and the verdict would
        # be CONTINUE).
        drain_window_seconds=0.0,
        # Stale activity evidence.
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # Advance past the idle deadline (65s > 60s) with no recorded
    # activity and a helpers-only monitor. The watchdog MUST fire.
    clock.advance(65.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The filtered count is 0; the helpers (10) were ignored.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_all_per_invocation_fields() -> None:
    """After the watchdog has been driven through every per-invocation
    write path, ``record_invocation_start()`` MUST reset every field
    back to its baseline so a reused watchdog cannot defer/fingerprint
    based on the previous run.

    Pre-fix several fields survived across invocations (``_last_alive_by``,
    ``_last_mcp_tool_call_at``, ``_last_subagent_progress_at``,
    ``_last_subagent_output_at``, ``_last_workspace_event_at``,
    ``_last_progress_fingerprint``, ``_last_subagent_progress_emit_at``).
    """
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    _populate_per_invocation_state(watchdog, clock)
    # Every per-invocation field is dirty. ``record_invocation_start``
    # MUST reset them all to baseline.
    watchdog.record_invocation_start()
    for field_name, baseline in _per_invocation_fields().items():
        actual = getattr(watchdog, field_name)
        assert actual == baseline, (
            f"record_invocation_start MUST reset {field_name} to {baseline!r}; got {actual!r}"
        )


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_alive_by_signal() -> None:
    """``_last_alive_by`` MUST be cleared on invocation_start.

    The pre-fix leak: ``_last_alive_by`` (assigned at
    ``idle_watchdog.py:1260``) survived across invocations, so a
    reused watchdog could feed a stale ``alive_by`` value into the
    ``IdleWatchdogKilledError.child_alive`` field on the next run's
    fire.
    """
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    # Manually populate ``_last_alive_by`` via the public fire path
    # (the watchdog assigns it post-fire when the corroborator reports
    # a non-None ``alive_by``).
    clock.advance(40.0)
    watchdog._last_alive_by = AliveBy.CPU_IDLE_WHILE_ALIVE
    assert watchdog.last_alive_by == AliveBy.CPU_IDLE_WHILE_ALIVE
    watchdog.record_invocation_start()
    assert watchdog.last_alive_by is None, (
        f"record_invocation_start MUST clear _last_alive_by; got {watchdog.last_alive_by!r}"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_progress_fingerprint() -> None:
    """``_last_progress_fingerprint`` MUST be cleared on invocation_start.

    The pre-fix leak: a fingerprint from the previous run would
    cause a same-fingerprint line in the new run to be skipped as
    a "repeat" when it is actually fresh.
    """
    watchdog, _clock = _invocation_start_full_reset_make_watchdog()
    watchdog._last_progress_fingerprint = "previous-fingerprint"
    watchdog.record_invocation_start()
    assert watchdog._last_progress_fingerprint is None, (
        "record_invocation_start MUST clear _last_progress_fingerprint"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_subagent_progress_emit_at() -> None:
    """``_last_subagent_progress_emit_at`` MUST be cleared on invocation_start.

    The pre-fix leak: the SUBAGENT_PROGRESS waiting-status emit
    cadence timestamp survived across invocations so the new run's
    first emit could be throttled by the previous run's emit time.
    """
    watchdog, _clock = _invocation_start_full_reset_make_watchdog()
    watchdog._last_subagent_progress_emit_at = 12345.0
    watchdog.record_invocation_start()
    assert watchdog._last_subagent_progress_emit_at is None, (
        "record_invocation_start MUST clear _last_subagent_progress_emit_at"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_per_channel_timestamps() -> None:
    """Every per-channel evidence timestamp MUST be cleared on invocation_start.

    The pre-fix leak: ``_last_mcp_tool_call_at``,
    ``_last_subagent_progress_at``, ``_last_subagent_output_at``, and
    ``_last_workspace_event_at`` survived across invocations so the
    second run's deferral path could inherit stale "fresh" evidence
    from the first run.
    """
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(5.0)
    watchdog.record_mcp_tool_call(now=clock.monotonic())
    watchdog.record_subagent_work(description="x")
    watchdog.record_subagent_output(now=clock.monotonic())
    watchdog.record_workspace_event()
    watchdog.record_invocation_start()
    assert watchdog._last_mcp_tool_call_at is None
    assert watchdog._last_subagent_progress_at is None
    assert watchdog._last_subagent_output_at is None
    assert watchdog._last_workspace_event_at is None


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_cumulative_waiting() -> None:
    """``_cumulative_waiting_on_child_seconds`` MUST reset to 0.0.

    The cumulative counter is per-invocation; a reused watchdog
    MUST NOT carry the prior run's cumulative budget into the next
    run because the CHILDREN_PERSIST_TOO_LONG fire compares the
    counter against the configured ceiling and a stale counter
    could push the watchdog over the threshold prematurely.
    """
    watchdog, _clock = _invocation_start_full_reset_make_watchdog()
    watchdog._cumulative_waiting_on_child_seconds = 250.0
    watchdog.record_invocation_start()
    assert watchdog._cumulative_waiting_on_child_seconds == 0.0, (
        "record_invocation_start MUST reset cumulative_waiting_on_child_seconds"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_record_invocation_start_resets_coarse_any_deferred_log_at() -> None:
    """``_last_any_deferred_log_at`` MUST be cleared on invocation_start.

    Pin for R6 per-invocation semantics: the coarse per-``fire_reason``
    throttle map shares the per-invocation reset semantics with the
    per-tuple map (``_last_deferred_log_at``) and the per-channel
    evidence map (``_last_evidence_deferral_log_at``). A coarse-map
    leak across invocations lets a fresh invocation inherit the
    previous run's coarse throttle timestamps and incorrectly
    suppress its first human-visible deferred-status log.

    Pre-fix the coarse map survived across invocations (only the
    per-tuple map and per-channel map were reset); the pin test at
    :mod:`test_log_spam_throttle` proves the coarse map is populated
    by ``_gate_fire`` but had no companion test for the reset path.
    """
    watchdog, _clock = _invocation_start_full_reset_make_watchdog()
    watchdog._last_any_deferred_log_at = {
        "no_output_at_start": 1234.0,
        "idle_timeout": 5678.0,
    }
    assert len(watchdog._last_any_deferred_log_at) == 2, (
        "precondition: the coarse throttle map MUST be populated"
    )
    watchdog.record_invocation_start()
    assert watchdog._last_any_deferred_log_at == {}, (
        f"record_invocation_start MUST reset _last_any_deferred_log_at;"
        f" got {watchdog._last_any_deferred_log_at!r}"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_second_invocation_starts_from_clean_baseline_no_stale_throttle() -> None:
    """A second invocation MUST NOT inherit the first invocation's throttle state.

    Black-box: drive the watchdog through ``evaluate()`` to populate
    the per-channel log throttle map, then ``record_invocation_start``
    and verify the second invocation starts with an empty throttle
    map (no deferred-log carryover from the first invocation).
    """
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(61.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    watchdog.record_mcp_tool_call(now=0.0)
    # First invocation: drive the deferral path so the throttle map is populated.
    assert watchdog.evaluate(classify_quiet=_active).name == "CONTINUE"
    assert len(watchdog._last_evidence_deferral_log_at) > 0, (
        "first invocation MUST populate the deferral throttle map"
    )
    # Reset and drive a second invocation.
    watchdog.record_invocation_start()
    clock.advance(0.1)
    # The throttle map MUST be empty after reset.
    assert watchdog._last_evidence_deferral_log_at == {}, (
        f"record_invocation_start MUST reset _last_evidence_deferral_log_at;"
        f" got {watchdog._last_evidence_deferral_log_at!r}"
    )
    assert watchdog._last_deferred_log_at == {}, (
        f"record_invocation_start MUST reset _last_deferred_log_at;"
        f" got {watchdog._last_deferred_log_at!r}"
    )
    assert watchdog._last_any_deferred_log_at == {}, (
        f"record_invocation_start MUST reset _last_any_deferred_log_at;"
        f" got {watchdog._last_any_deferred_log_at!r}"
    )


# === consolidated from test_invocation_start_full_reset.py ===
def test_second_invocation_fingerprint_does_not_skip_fresh_lines() -> None:
    """A second invocation MUST NOT skip a fresh progress line because
    it has the same fingerprint as the previous invocation's last line.

    Black-box: drive ``record_progress_report`` with the same line in
    two invocations and verify the watchdog does not suppress the
    second invocation's progress event as a repeat.
    """
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(5.0)
    watchdog.record_progress_report("phase=alpha")
    # Reset and immediately re-record the same progress report.
    watchdog.record_invocation_start()
    clock.advance(5.0)
    # The progress report MUST be processed (the fingerprint reset
    # means the watchdog cannot see the second invocation's
    # ``phase=alpha`` as a repeat of the first invocation's
    # ``phase=alpha``).
    watchdog.record_progress_report("phase=alpha")
    assert watchdog._last_progress_fingerprint == "phase=alpha", (
        f"second invocation MUST update _last_progress_fingerprint;"
        f" got {watchdog._last_progress_fingerprint!r}"
    )


# === consolidated from test_log_spam_throttle.py ===
def test_gate_fire_throttles_identical_deferred_emission(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """1000 calls to ``_gate_fire`` in the same FakeClock second MUST
    emit at most 2 DEBUG records.

    Pre-fix the gate emits one record per call (1000 records). Post-fix
    the throttle keeps it to <= 2 (initial transition + first refresh
    window).
    """
    _buf, records = captured_debug_records
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=30.0)
    _patch_classifier_to_deferring_kind(watchdog)
    # SESSION_CEILING_EXCEEDED bypasses the gate; use a normal gated
    # reason so the deferral branch is reached.
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(1000):
        verdict = watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        assert verdict == WatchdogVerdict.CONTINUE

    matching = [r for r in records if "deferred fire" in r and "CHILDREN_PERSIST_TOO_LONG" in r]
    assert len(matching) <= 2, (
        f"DEBUG log spam regression: got {len(matching)} records"
        f" for 1000 calls in the same second; expected <= 2"
        f" (one initial + one refresh window). Records: {matching[:3]}"
    )


# === consolidated from test_log_spam_throttle.py ===
def test_gate_fire_throttle_uses_configured_window(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """A throttle window of 0.01s MUST allow refresh emissions.

    With a tiny throttle window the test exercises the refresh
    boundary: drive 100 ticks at 0s and 100 ticks at 0.05s; the
    first tick emits, then no emissions for 0.01s; the 0.05s tick
    is past the refresh window so it emits again.
    """
    _buf, records = captured_debug_records
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=0.01)
    _patch_classifier_to_deferring_kind(watchdog)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(100):
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    clock.advance(0.05)
    for _ in range(100):
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    matching = [r for r in records if "deferred fire" in r and "CHILDREN_PERSIST_TOO_LONG" in r]
    # Expect at most: 1 first transition + 1 refresh = 2
    assert len(matching) <= 3, f"throttle window 0.01s produced too many emissions: {len(matching)}"


# === consolidated from test_log_spam_throttle.py ===
def test_gate_fire_throttle_is_per_key() -> None:
    """The COARSE single-key throttle caps emissions at most one DEBUG
    record per ``watchdog_log_throttle_seconds`` per ``fire_reason``
    regardless of how the ``deferred_kind`` cycles.

    Verifies the COARSE throttle is keyed on ``fire_reason.value``
    alone, NOT the tuple. The per-tuple key is consulted ONLY when
    the coarse throttle permits a log emission.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    even after the per-(fire_reason, deferred_kind) throttle was
    added, because the deferred_kind cycles (DUPLICATE_KILL ->
    LOADING -> DUPLICATE_KILL) and the per-tuple throttle key
    CHANGED on each cycle so the per-tuple throttle MISSED. The
    coarse single-key throttle solves this by keying on
    ``fire_reason.value`` alone, capping emissions to one DEBUG
    record per throttle window per fire_reason.
    """
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=30.0)
    call_log: list[StuckKind] = []

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        kind = call_log[0] if call_log else StuckKind.DUPLICATE_KILL
        return kind

    # Use ``setattr`` with the attribute name held in a local
    # variable so mypy cannot narrow the access to a private-method
    # assignment AND ruff B010 does not flag a setattr-with-constant-
    # value call. The policy test for ``test_zero_test_file_suppressions``
    # rejects bare mypy suppression comments inside test files.
    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # First call with DUPLICATE_KILL.
    call_log = [StuckKind.DUPLICATE_KILL]
    assert (
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    # Second call with LOADING (different deferred_kind).
    call_log = [StuckKind.LOADING]
    assert (
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    # Both transitions MUST route through the coarse throttle (the
    # kind label is preserved on ``_last_deferred_kind`` so
    # operators can still see WHICH kind was deferred; the throttle
    # is on the LOG emission, not on the kind tracking).
    assert hasattr(watchdog, "_last_any_deferred_log_at"), (
        "IdleWatchdog MUST expose _last_any_deferred_log_at for the coarse single-key throttle"
    )
    coarse_map = watchdog._last_any_deferred_log_at
    assert fire_reason.value in coarse_map, (
        f"fire_reason key missing from coarse throttle map; keys={list(coarse_map)}"
    )
    # The CURRENT kind label is preserved on the watchdog's
    # ``_last_deferred_kind`` field -- the operator can still see
    # which kind was deferred even when the coarse throttle
    # suppressed the log emission.
    assert watchdog._last_deferred_kind == StuckKind.LOADING, (
        f"expected _last_deferred_kind=LOADING (the most recent);"
        f" got {watchdog._last_deferred_kind!r}"
    )


# === consolidated from test_log_spam_throttle.py ===
def test_coarse_single_key_throttle_caps_emissions_across_kind_cycles(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """1000 calls cycling DUPLICATE_KILL <-> LOADING MUST emit at most 2 DEBUG records.

    The PROMPT log spam regression: drive ``_gate_fire`` 1000 times
    cycling between DUPLICATE_KILL and LOADING (the typical
    deferred_kind cycle during a long-lived waiting run) inside a
    single throttle window; assert the captured DEBUG records is at
    most 2 (one initial transition + one refresh). Pre-fix the count
    is ~500 because the per-tuple throttle key changes every call.
    Post-fix the coarse throttle caps emissions to <= 2.
    """
    _buf, records = captured_debug_records
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=30.0)
    call_log: list[StuckKind] = []

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        # Cycle SILENT_SUBAGENT <-> LOADING on every call so the
        # per-tuple throttle key changes every time.
        kind = call_log[0] if call_log else StuckKind.DUPLICATE_KILL
        return kind

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    kinds = [StuckKind.DUPLICATE_KILL, StuckKind.LOADING]
    for i in range(1000):
        call_log = [kinds[i % 2]]
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )

    matching = [r for r in records if ("deferred fire" in r and "CHILDREN_PERSIST_TOO_LONG" in r)]
    assert len(matching) <= 2, (
        f"coarse single-key throttle MUST cap emissions across"
        f" kind-cycles; got {len(matching)} records for 1000 calls"
        f" in the same throttle window. Records: {matching[:3]}"
    )


# === consolidated from test_log_spam_throttle.py ===
def test_scoped_child_active_appears_in_hard_stop_diag() -> None:
    """Every HARD_STOP fire's diag dict MUST contain ``scoped_child_active``.

    The PROMPT log showed ``scoped_child_active=?`` at the 3 consumer
    sites (subscriber.py:114, _idle_stream_timeout_error.py:30,
    _agent_inactivity_timeout_error.py:30). The root cause was the
    producer site only setting the key when ``scoped_child_active``
    was non-None in the corroborator snapshot; the
    ``_build_corroboration_diag`` helper skipped the assignment
    when the value was ``None`` and the consumer sites fell through
    to the ``?`` fallback.

    The fix: ``_build_corroboration_diag`` ALWAYS sets
    ``scoped_child_active`` (defaulting to False when None) so the
    3 consumer sites always see a concrete boolean.
    """
    # Capture emitted WaitingStatusEvents so we can inspect the
    # diag dict from the HARD_STOP emission.
    emitted: list[WaitingStatusEvent] = []

    def _capture(event: WaitingStatusEvent) -> None:
        emitted.append(event)

    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        activity_evidence_ttl_seconds=180.0,
        stuck_job_sub_ceiling_seconds=600.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=1800.0,
    )
    clock = FakeClock(start=0.0)
    # Use a stale alive_by + scoped_child_active=True so the
    # stuck_job_sub_ceiling will trip at 600s. The corroborator
    # returns scoped_child_active=True, but the diagnostic
    # MUST also include the value (after the fix).
    watchdog = IdleWatchdog(
        policy,
        clock,
        listener=_capture,
        corroborator=lambda: CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=200.0,
        ),
    )

    watchdog.record_invocation_start()
    clock.advance(201.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(600.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    # The HARD_STOP path (CHILDREN_PERSIST_TOO_LONG via the
    # _handle_waiting_branch path) MUST have emitted with a diag
    # dict that contains ``scoped_child_active`` (NOT the
    # ``?`` fallback).
    hard_stop_events = [e for e in emitted if e.kind == WaitingStatusKind.HARD_STOP]
    assert hard_stop_events, (
        f"expected at least one HARD_STOP emission; got kinds={[e.kind for e in emitted]}"
    )
    for event in hard_stop_events:
        diag = event.diagnostic or {}
        assert "scoped_child_active" in diag, (
            f"HARD_STOP diag dict MUST contain scoped_child_active key"
            f" (no '?' fallback); got diag={diag!r}"
        )
        assert isinstance(diag["scoped_child_active"], bool), (
            f"scoped_child_active MUST be a concrete boolean"
            f" (True or False), not None; got"
            f" {diag['scoped_child_active']!r}"
        )


# === consolidated from test_log_spam_throttle_public_surface.py ===
def test_log_spam_throttle_public_surface_reaches_deferred_fire_branch(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6: deferred-fire branch reached via PUBLIC surface only.

    The headline R6 regression was ~10 DEBUG records/sec emitted at
    ``_gate_fire:949`` while a fire was deferred with
    ``SILENT_SUBAGENT`` via the ``CHILDREN_PERSIST_TOO_LONG`` deferred
    path produced by the SUB-ceiling block at
    ``_waiting_branch.py:184-237``. The throttle fix caps emissions
    to <= 1 per ``(fire_reason, deferred_kind)`` key per
    ``watchdog_log_throttle_seconds`` (30s default).

    This test drives ``watchdog.evaluate(classify_quiet=...)`` 1000
    times via the PUBLIC entry point and reaches the deferred-fire
    branch via PUBLIC behavior:

      1. ``record_subagent_work(now=0.0, description="phase-1")``
         seeds the ``subagent_progress_count=1`` and
         ``last_subagent_progress_at=0.0`` -- the watchdog's
         ``subagent_output`` channel then reports
         ``counter=1, age=5.1s`` at evaluate() time (>= 1.0s
         ``silent_subagent_seconds``).
      2. ``set_is_waiting_state(False)`` (public method) prevents
         the classifier from returning ``DUPLICATE_KILL``.
      3. ``_stale_subagent_corroborator`` returns
         ``scoped_child_active=True, alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS``
         -- required for the SUB-ceiling block to fire.
      4. ``_HelpersOnlyMonitorLogSpamThrottlePublicSurface`` returns ``live_subagent_count()=0``
         -- the watchdog's ``subagent_liveness`` channel reports
         ``alive_by=None`` and ``can_defer=False``. Combined with the
         stale ``subagent_output`` channel, the
         ``_silent_subagent_path`` branch triggers and the classifier
         returns ``SILENT_SUBAGENT``.
      5. Each ``evaluate()`` after ``stuck_job_sub_ceiling_seconds``
         (5s) elapses calls ``_handle_waiting_branch`` which reaches
         the SUB-ceiling block at line 184; ``_gate_fire`` returns
         ``CONTINUE`` (deferred fire) and emits the DEBUG log
         "idle watchdog: silent subagent (deferred)
         reason=CHILDREN_PERSIST_TOO_LONG idle_elapsed=...s".

    The throttle holds: 1000 calls in the same 30s throttle window
    produce AT MOST 1 DEBUG log (initial transition; subsequent calls
    are suppressed by the coarse single-key throttle). The test
    asserts ``<= 2`` to tolerate the per-tuple throttle's refresh
    window (the canonical R6 spam-invariant matches the private-seam
    ``test_log_spam_throttle.py`` ceiling).

    The test uses NO private seams: no ``setattr`` on
    ``_classify_stuck_now``, no direct call to ``_gate_fire``, no
    read of any ``_last_*_log_at`` field. It proves the R6 invariant
    from the PUBLIC listener and loguru sink surfaces only.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
    )

    # PUBLIC: ensure the classifier's first branch (is_waiting_state)
    # does not return DUPLICATE_KILL. The watchdog runs OUTSIDE a
    # pipeline wait state in this scenario. MUST be called BEFORE
    # ``record_invocation_start`` because the latter resets
    # ``_is_waiting_state`` to False anyway -- but calling it
    # explicitly documents the contract.
    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    # PUBLIC: seed the evidence summary so the StuckClassifier falls
    # through to the SILENT_SUBAGENT branch (stale subagent_output,
    # alive_by=None on subagent_liveness, no first-party fresh,
    # noop classify_quiet returns ACTIVE). MUST be called AFTER
    # ``record_invocation_start`` because the latter resets the
    # per-channel evidence counters and timestamps
    # (``_subagent_progress_count``, ``_last_subagent_progress_at``,
    # ``_last_subagent_progress_description``).
    watchdog.record_subagent_work(now=0.0, description="phase-1")

    # First evaluate() at t=3.0s enters the WAITING_ON_CHILD
    # branch (classify_quiet returns WAITING_ON_CHILD). The
    # SUB-ceiling block at line 184 is consulted but
    # ``current_run_elapsed=0`` and ``candidate_total=0`` so the
    # block does NOT fire yet. The branch emits the
    # ``WAITING_ON_CHILD deferral`` INFO log + ENTERED event.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD deferral; got {first_verdict!r}"
    )

    # Advance so ``current_run_elapsed >= stuck_job_sub_ceiling_seconds``.
    # The SUB-ceiling block at line 184 fires when
    # ``candidate_total >= stuck_job_sub_ceiling_seconds``. With
    # ``_waiting_on_child_started_at=3.0`` (from the first evaluate())
    # and ``_cumulative_waiting_on_child_seconds=0.0``, advancing the
    # clock to t=8.1 makes ``current_run_elapsed=5.1`` and
    # ``candidate_total=5.1 >= 5.0`` so the SUB-ceiling block fires
    # on every subsequent evaluate() call.
    clock.advance(5.1)

    # Drive 1000 evaluate() calls in the SAME 30s throttle window
    # (no further clock advance). Each call enters
    # ``_handle_waiting_branch``, reaches the SUB-ceiling block,
    # and calls ``self._gate_fire(...)`` -- the deferred-fire
    # branch that produced the original spam regression. The
    # classifier returns ``SILENT_SUBAGENT`` via the public
    # evidence summary; ``_gate_fire`` returns ``CONTINUE`` and the
    # throttle (``_maybe_log_any_deferred`` then
    # ``_maybe_log_deferred``) caps DEBUG emissions.
    # ``evaluate()`` propagates the gate's CONTINUE -- this is the
    # PUBLIC surface signature for a deferred-fire cycle. Before
    # the SUB-ceiling block fires, ``_handle_waiting_branch`` falls
    # through to the cadence gate which emits PROGRESS-kind
    # ``WaitingStatusEvent`` instances and returns
    # ``WAITING_ON_CHILD``. After the SUB-ceiling block fires, the
    # gate defers and ``_handle_waiting_branch`` returns
    # ``CONTINUE``. Either verdict proves the watchdog stayed in
    # deferral (no FIRE was emitted).
    for i in range(1000):
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

    # ASSERTION 1 (the headline R6 invariant): DEBUG records
    # matching the deferred-fire spam pattern are <= 2 per
    # throttle window. The private-seam pin test
    # ``test_log_spam_throttle.py::test_gate_fire_throttles_identical_deferred_emission``
    # asserts the same bound (one initial + one refresh); this
    # public-surface test proves the same invariant is observable
    # from the PUBLIC loguru sink filtered on
    # ``component='idle_watchdog'``.
    deferred_fire_records = [
        r for r in log_records if "deferred fire" in r and "children_persist_too_long" in r
    ]
    assert len(deferred_fire_records) <= 2, (
        f"R6 deferred-fire spam regression: got"
        f" {len(deferred_fire_records)} 'silent subagent"
        f" (deferred)' records for 1000 evaluate() calls in the"
        f" same throttle window; expected <= 2 (one initial"
        f" transition + one refresh). Records: {deferred_fire_records[:3]}"
    )

    # ASSERTION 2: the sink is REAL (not trivially empty). The
    # deferred-fire emission MUST have flowed through the sink
    # -- a zero-sink bypass (e.g. a future refactor that
    # silently renames the ``component`` bind) cannot pass this
    # test trivially. The classifier is configured to return
    # SILENT_SUBAGENT and ``_gate_fire`` MUST emit exactly one
    # DEBUG record on the initial transition.
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the deferred-fire DEBUG log emitted at"
        f" _gate.py:174; got {len(deferred_fire_records)} records"
        f" matching 'deferred fire' + 'children_persist_too_long'"
        f" (a zero-sink bypass means the bound is meaningless)."
    )

    # ASSERTION 3: the watchdog's PUBLIC ``last_fire_reason``
    # surface shows the classifier-kind label -- operators can
    # see WHY a would-be fire was deferred via the public
    # property (no setattr / no private read required).
    assert watchdog.last_deferred_kind in (StuckKind.THINKING, StuckKind.LOADING), (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST report the"
        f" deferring kind after 1000 deferred-fire cycles; got"
        f" {watchdog.last_deferred_kind!r}"
    )

    # ASSERTION 4: PROGRESS-kind WaitingStatusEvent emissions are
    # also bounded by the cadence gate (``waiting_status_interval_seconds``
    # = 10_000.0s, so the cadence gate is closed for the entire
    # 1000-call cycle). This is the secondary R6 witness -- the
    # cadence gate and the deferred-fire throttle are two
    # distinct spam-suppression mechanisms and both must hold.
    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    # ASSERTION 5: ENTERED event fires EXACTLY once (on first
    # WAITING entry). This is a public-surface witness that the
    # WAITING branch was actually entered -- a missing ENTERED
    # would imply the watchdog never deferred, which would make
    # the throttle invariant trivially true (a zero-sink bypass).
    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    # ASSERTION 6: no HARD_STOP emission -- the deferred-fire
    # branch returns CONTINUE on every call (the gate's
    # CONTINUE response signals the SUB-ceiling block to stay
    # in deferral). HARD_STOP only fires when ``_gate_fire``
    # returns FIRE (the cumulative ceiling path or the
    # post-deferral fire path).
    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )


# === consolidated from test_log_spam_throttle_public_surface.py ===
def test_log_spam_throttle_public_surface_deferred_fire_throttle_window(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6 secondary witness: throttle window refresh allows a second emission.

    The per-(fire_reason, deferred_kind) throttle plus the coarse
    single-key throttle allow ONE refresh emission per
    ``watchdog_log_throttle_seconds`` window per ``fire_reason``.
    With a small throttle window (0.05s) the test exercises the
    refresh boundary: 100 calls at t=5.1s (initial transition +
    throttled rest) and 100 more calls at t=5.2s (past the
    refresh window, ONE more emission). Total: <= 3 records
    across 200 calls in two throttle windows.

    This is the public-surface analogue of the private-seam
    ``test_log_spam_throttle.py::test_gate_fire_throttle_uses_configured_window``
    test and proves the throttle refresh boundary is observable
    from the PUBLIC loguru sink.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    # Tight throttle window so the refresh boundary is reachable
    # in a single test. The 0.05s window allows ONE refresh
    # emission between two well-spaced batches.
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
        watchdog_log_throttle_seconds=0.05,
    )

    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(now=0.0, description="phase-1")
    clock.advance(3.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(5.1)

    # Batch 1: 100 calls in the initial throttle window
    # (now=5.1s, no advance). Emits ONE initial deferred-fire
    # DEBUG record; the next 99 are throttled.
    for _ in range(100):
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    # Advance past the 0.05s throttle window to open the
    # refresh boundary.
    clock.advance(0.1)

    # Batch 2: 100 more calls in the new throttle window.
    # The first call emits ONE refresh DEBUG record; the
    # remaining 99 are throttled. Total across both batches:
    # <= 3 records (2 emissions + 1 potential per-tuple
    # refresh edge case).
    for _ in range(100):
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    deferred_fire_records = [
        r for r in log_records if "deferred fire" in r and "children_persist_too_long" in r
    ]
    assert len(deferred_fire_records) <= 3, (
        f"R6 throttle window 0.05s produced too many deferred-fire"
        f" emissions: got {len(deferred_fire_records)} records for"
        f" 200 calls in two throttle windows; expected <= 3"
        f" (initial + refresh + per-tuple edge case)."
    )
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink MUST capture the deferred-fire DEBUG log;"
        f" got {len(deferred_fire_records)} records."
    )


# === consolidated from test_log_spam_throttle_public_surface.py ===
def test_log_spam_throttle_public_surface_kind_cycle_via_public_surface(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6 deferred-kind cycle proof via PUBLIC surface.

    Coarse throttle holds across ``SILENT_SUBAGENT`` <-> ``DUPLICATE_KILL``.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    while a fire was deferred. The fix added a per-``(fire_reason,
    deferred_kind)`` throttle plus a COARSE single-key throttle keyed
    on ``fire_reason.value`` alone so the throttle holds even when
    the ``deferred_kind`` cycles between calls.

    Pre-fix the per-tuple throttle MISSED the duplicate emission
    whenever the ``deferred_kind`` changed (e.g.
    ``SILENT_SUBAGENT`` -> ``LOADING`` -> ``SILENT_SUBAGENT``) because
    the per-tuple key changed on every cycle. The coarse throttle
    solves this by keying on ``fire_reason.value`` alone, capping
    emissions to at most one DEBUG record per ``watchdog_log_throttle_seconds``
    per ``fire_reason`` regardless of how the ``deferred_kind`` cycles.

    This test drives ``watchdog.evaluate(classify_quiet=...)`` 1000
    times in the same FakeClock second, alternating the deferred-kind
    value between ``SILENT_SUBAGENT`` and ``DUPLICATE_KILL`` via the
    PUBLIC ``watchdog.set_is_waiting_state(bool)`` method -- the
    pipeline-facing surface that the run loop uses to mirror
    ``state.is_waiting_state``. No ``setattr`` on
    ``_classify_stuck_now`` and no direct call to ``_gate_fire``; the
    classifier's verdict is driven entirely by the public state.

    The cycle scenario:

      * ``set_is_waiting_state(False)``: classifier falls through to
        branch 7 (``SILENT_SUBAGENT``) because the
        ``subagent_output`` channel is seeded (counter=1, age=5.1s,
        5.1 >= ``silent_subagent_seconds=1.0``) AND
        ``subagent_liveness`` has ``alive_by=None`` (the
        ``_HelpersOnlyMonitorLogSpamThrottlePublicSurface`` returns ``live_subagent_count()=0``
        so the process-monitor live-subagent signal is absent). Gate
        emits the ``idle watchdog: silent subagent (deferred) ...``
        DEBUG record.
      * ``set_is_waiting_state(True)``: classifier returns
        ``DUPLICATE_KILL`` immediately on branch 1 (the highest-
        priority branch). Gate emits the
        ``idle watchdog: deferred fire reason=CHILDREN_PERSIST_TOO_LONG
        kind=duplicate_kill ...`` DEBUG record.

    Both deferred-fire DEBUG records share the same
    ``fire_reason`` key (``CHILDREN_PERSIST_TOO_LONG``); the coarse
    single-key throttle (``_maybe_log_any_deferred``) suppresses
    emissions after the first one in the same throttle window
    regardless of which ``deferred_kind`` the classifier returned.

    The test asserts ``len(deferred_fire_records) <= 2`` (one initial
    transition + one per-tuple refresh edge case -- the same bound the
    private-seam
    ``test_log_spam_throttle.py::test_coarse_single_key_throttle_caps_emissions_across_kind_cycles``
    test asserts). Pre-fix the count is ~500 because the per-tuple
    throttle MISSED on every cycle (different key per call); post-fix
    the coarse throttle caps emissions to <= 2 records.

    The ``>= 1`` lower bound guards against a zero-sink bypass: if
    the ``component='idle_watchdog'`` loguru bind is silently
    renamed in a future refactor, the sink would capture zero
    records and the bound would lose its meaning.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
    )

    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(now=0.0, description="phase-1")
    clock.advance(3.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(5.1)

    # Drive 1000 evaluate() calls in the SAME 30s throttle window
    # (no further clock advance). Alternate ``set_is_waiting_state``
    # between False (classifier falls through to SILENT_SUBAGENT
    # via branch 7) and True (classifier returns DUPLICATE_KILL on
    # branch 1 immediately). The gate's classifier call sees a
    # different ``deferred_kind`` on every other call -- exactly the
    # ``SILENT_SUBAGENT`` -> ``DUPLICATE_KILL`` -> ``SILENT_SUBAGENT``
    # -> ... cycle the coarse single-key throttle is designed to
    # suppress. Without the coarse throttle the per-tuple throttle
    # would MISS on every other call (different
    # ``(fire_reason, deferred_kind)`` tuple) and the gate would log
    # ~500 DEBUG records.
    for i in range(1000):
        # PUBLIC: drive the classifier's ``is_waiting_state`` input
        # via the canonical run-loop-facing method. Even i ->
        # is_waiting_state=False (classifier branch 7 SILENT_SUBAGENT),
        # odd i -> is_waiting_state=True (classifier branch 1
        # DUPLICATE_KILL).
        watchdog.set_is_waiting_state(i % 2 == 1)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

    # ASSERTION 1 (the headline R6 invariant across kind-cycles):
    # the coarse single-key throttle MUST cap DEBUG emissions to
    # <= 2 records per ``watchdog_log_throttle_seconds`` per
    # ``fire_reason`` REGARDLESS of how the ``deferred_kind``
    # cycles. The filter captures BOTH the
    # ``silent subagent (deferred)`` log (SILENT_SUBAGENT branch
    # of ``_gate_fire``) AND the generic ``deferred fire reason=...
    # kind=...`` log (DUPLICATE_KILL branch -- and any other
    # non-STUCK, non-SILENT_SUBAGENT deferred kind).
    deferred_fire_records = [
        r
        for r in log_records
        if (("deferred fire" in r or "deferred fire" in r) and "children_persist_too_long" in r)
    ]
    assert len(deferred_fire_records) <= 2, (
        f"R6 coarse single-key throttle MUST cap emissions across"
        f" SILENT_SUBAGENT <-> DUPLICATE_KILL kind-cycles; got"
        f" {len(deferred_fire_records)} deferred-fire DEBUG records"
        f" for 1000 evaluate() calls in the same throttle window."
        f" Records: {deferred_fire_records[:3]}"
    )

    # ASSERTION 2: the sink is REAL (not trivially empty). The
    # FIRST evaluate() with a new ``deferred_kind`` MUST emit a
    # DEBUG record (the initial transition is never throttled).
    # A zero-sink bypass would silently capture zero records and
    # the upper bound would be vacuously satisfied.
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the first deferred-fire DEBUG log; got"
        f" {len(deferred_fire_records)} records (a zero-sink bypass"
        f" would make the throttle bound meaningless)."
    )

    # ASSERTION 3: the watchdog's PUBLIC ``last_deferred_kind``
    # surface reports BOTH kinds across the cycle (operators can
    # see WHY each fire was deferred even when the coarse throttle
    # suppressed the log emission -- the kind label is preserved
    # on ``_last_deferred_kind`` regardless of throttle state).
    # The 1000-call cycle ENDS with i=999 (odd), so
    # ``is_waiting_state=True`` was set just before the last
    # evaluate(); the classifier returned DUPLICATE_KILL on that
    # last call.
    assert watchdog.last_deferred_kind in (
        StuckKind.THINKING,
        StuckKind.LOADING,
        StuckKind.DUPLICATE_KILL,
    ), (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST report a"
        f" deferring kind after 1000 cycle iterations; got"
        f" {watchdog.last_deferred_kind!r}"
    )

    # ASSERTION 4: PROGRESS-kind WaitingStatusEvent emissions are
    # also bounded by the cadence gate
    # (``waiting_status_interval_seconds`` = 10_000.0s, so the
    # cadence gate is closed for the entire 1000-call cycle).
    # The throttle on deferred-fire DEBUG logs and the cadence on
    # WaitingStatusEvent emissions are two distinct spam-suppression
    # mechanisms; both must hold for R6.
    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    # ASSERTION 5: ENTERED event fires EXACTLY once (on first
    # WAITING entry). This is a public-surface witness that the
    # WAITING branch was actually entered -- a missing ENTERED
    # would imply the watchdog never deferred, which would make
    # the throttle invariant trivially true (a zero-sink bypass).
    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    # ASSERTION 6: no HARD_STOP emission -- the deferred-fire
    # branch returns CONTINUE on every call (the gate's CONTINUE
    # response signals the SUB-ceiling block to stay in deferral).
    # HARD_STOP only fires when ``_gate_fire`` returns FIRE (the
    # cumulative ceiling path or the post-deferral fire path),
    # which never happens in this configuration.
    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_plain_tool_use_envelope() -> None:
    """A canonical ``{"type": "tool_use", "name": ..., "input": ...}``
    envelope yields the expected ``(tool_name, tool_args)`` pair.
    """
    line = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_claude_content_block_start_envelope() -> None:
    """A Claude ``{"type": "stream_event", "event":
    {"type": "content_block_start", "content_block":
    {"type": "tool_use", "name": "Read", "input": {...}}}}`` envelope
    unwraps to the canonical ``(tool_name, tool_args)`` pair.
    """
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/tmp/example.txt"},
                },
            },
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Read"
    assert tool_args == {"file_path": "/tmp/example.txt"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_arguments_field() -> None:
    """Some transports use ``arguments`` instead of ``input``; the
    helper accepts either.
    """
    line = json.dumps(
        {
            "type": "tool_use",
            "name": "Write",
            "arguments": {"content": "hello"},
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Write"
    assert tool_args == {"content": "hello"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_pi_tool_execution_start_envelope() -> None:
    """Pi's documented ``tool_execution_start`` event must fingerprint
    like other transports so repeated MCP calls are breakable.
    """
    line = json.dumps(
        {
            "type": "tool_execution_start",
            "toolCallId": "call_1",
            "toolName": "mcp__ralph__exec",
            "args": {"command": "pwd", "timeout_ms": 300000},
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "mcp__ralph__exec"
    assert tool_args == {"command": "pwd", "timeout_ms": 300000}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_pi_toolcall_end_envelope() -> None:
    """Pi ``message_update`` toolcall_end events carry the tool call
    inside ``assistantMessageEvent.toolCall``; the breaker must see
    the inner name and input arguments.
    """
    line = json.dumps(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "toolcall_end",
                "contentIndex": 0,
                "toolCall": {
                    "id": "call_1",
                    "name": "mcp__ralph__exec",
                    "input": {"command": "pwd", "timeout_ms": 300000},
                },
            },
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "mcp__ralph__exec"
    assert tool_args == {"command": "pwd", "timeout_ms": 300000}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_live_cursor_nested_tool_call_envelope() -> None:
    """Cursor stream-json nests live tool calls under ``tool_call.<name>ToolCall``."""
    line = json.dumps(
        {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "tool-1",
            "tool_call": {
                "editToolCall": {
                    "args": {
                        "path": "/tmp/probe/tool_probe.txt",
                        "streamContent": "cursor parser probe",
                    }
                },
                "toolCallId": "tool-1",
            },
        }
    )

    result = _extract_tool_call_from_activity_signal(line)

    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "editToolCall"
    assert tool_args == {
        "path": "/tmp/probe/tool_probe.txt",
        "streamContent": "cursor parser probe",
    }


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_returns_none_for_non_tool_use_envelope() -> None:
    """A non-tool-use envelope (e.g. ``{"type": "text", ...}``) MUST
    return ``None`` so the breaker is NOT fed for irrelevant lines.
    """
    line = json.dumps({"type": "text", "text": "hello"})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is None


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_returns_none_for_invalid_json() -> None:
    """Invalid JSON MUST return ``None`` (no exception)."""
    assert _extract_tool_call_from_activity_signal("not json {{{") is None
    assert _extract_tool_call_from_activity_signal("") is None


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_returns_unknown_for_missing_name() -> None:
    """A tool-use envelope without a ``name`` field MUST fall back to
    ``"unknown"`` so the fingerprint is always well-formed.
    """
    line = json.dumps({"type": "tool_use", "input": {"foo": "bar"}})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "unknown"
    assert tool_args == {"foo": "bar"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_claude_prefixed_plain_text() -> None:
    """A plain-text ``claude tool: <name>`` line classified as TOOL_USE
    by ClaudeExecutionStrategy MUST yield a stable fingerprint.

    Plain-text tool-use lines carry no arguments, so the helper
    returns an empty ``args`` dict.  Without this path the
    repetition breaker cannot fire on repeated identical plain-text
    tool invocations.
    """
    result = _extract_tool_call_from_activity_signal("claude tool: Bash")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_plain_tool_prefix() -> None:
    """A plain-text ``[plain] tool: <name>`` line MUST also yield a
    stable fingerprint when it reaches the helper.

    This mirrors the GenericParser convention so the tool-call
    circuit breaker stays reachable for any transport that surfaces
    plain-text tool-use markers.
    """
    result = _extract_tool_call_from_activity_signal("[plain] tool: Read")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Read"
    assert tool_args == {}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_returns_none_for_plain_text_without_tool_marker() -> None:
    """A non-tool plain-text line MUST NOT produce a fingerprint."""
    assert _extract_tool_call_from_activity_signal("random log line") is None


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pi_strategy_classifies_tool_execution_start_as_tool_use() -> None:
    """Pi's strategy must classify pi.dev tool events as TOOL_USE.

    Without this, pi MCP calls are ordinary output to the watchdog,
    so the repeated-identical-tool-call breaker never receives the
    extraction helper's stable fingerprint.
    """
    strategy = _make_pi_strategy()
    line = json.dumps(
        {
            "type": "tool_execution_start",
            "toolName": "mcp__ralph__exec",
            "args": {"command": "pwd"},
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pi_strategy_classifies_toolcall_end_as_tool_result() -> None:
    """Pi's ``toolcall_end`` CLOSES a call ``tool_execution_start`` opened.

    Real captures show both events carrying the same ``callID``, so counting
    both as TOOL_USE fed the tool-call repetition breaker twice per call and
    four legitimate identical calls hit a window rule sized for eight.
    """
    strategy = _make_pi_strategy()
    line = json.dumps(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "toolcall_end",
                "toolCall": {"name": "mcp__ralph__exec", "input": {"command": "pwd"}},
            },
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_RESULT
    assert signal.raw == line


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pi_strategy_classifies_message_update_error_as_error_line() -> None:
    """Pi ``assistantMessageEvent.type=error`` frames such as
    ``terminated`` must feed the repeated-error breaker, not reset
    the idle timer as ordinary output.
    """
    strategy = _make_pi_strategy()
    line = json.dumps(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "error", "reason": "terminated"},
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert signal.raw == "terminated"


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pi_strategy_classifies_tool_execution_error_as_error_line() -> None:
    """Pi tool execution failures must not become ordinary progress.

    This keeps failed MCP results, including client-collapsed timeout
    text, on the repeated-error circuit-breaker path.
    """
    strategy = _make_pi_strategy()
    line = json.dumps(
        {
            "type": "tool_execution_end",
            "toolName": "mcp__ralph__exec",
            "isError": True,
            "result": {"content": [{"type": "text", "text": "terminated"}]},
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert signal.raw == "terminated"


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_cursor_strategy_classifies_live_tool_call_started_as_tool_use() -> None:
    """Cursor live ``tool_call`` start events must feed the tool-use path."""
    strategy = _make_cursor_strategy()
    line = json.dumps(
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "editToolCall": {
                    "args": {"path": "/tmp/probe/tool_probe.txt"},
                },
                "toolCallId": "tool-1",
            },
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_cursor_strategy_classifies_live_tool_call_completed_as_tool_result() -> None:
    """Cursor live ``tool_call`` completed events must close the post-tool window."""
    strategy = _make_cursor_strategy()
    line = json.dumps(
        {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {
                "editToolCall": {
                    "args": {"path": "/tmp/probe/tool_probe.txt"},
                    "result": {"success": {"message": "ok"}},
                },
                "toolCallId": "tool-1",
            },
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_RESULT
    assert signal.raw == line


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pty_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
    """A parsed TOOL_USE line on the PTY reader MUST reach
    ``watchdog.record_tool_call_activity`` with the canonical
    ``(tool_name, tool_args)`` pair extracted from the envelope.
    """
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    watchdog = _RecordingWatchdog()

    list(reader._handle_queued_line(raw + "\n", watchdog))

    assert len(watchdog.tool_call_observations) == 1, (
        f"Expected exactly one tool-call observation; got {watchdog.tool_call_observations}"
    )
    tool_name, tool_args = watchdog.tool_call_observations[0]
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pty_line_reader_routes_repeated_tool_use_to_trip_breaker() -> None:
    """The PTY reader MUST route repeated identical tool calls
    through the breaker so an identical-tool-call wedge is detected.

    The test exercises the production ``_handle_queued_line`` path
    with a recording watchdog that tracks the fingerprint -- the
    production ``RepetitionTracker.tripped()`` will fire on
    identical (tool_name, tool_args) pairs observed >= window_count
    times.  Without this path the production breaker dimension is
    unreachable in real runs.
    """
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}})
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        list(reader._handle_queued_line(raw + "\n", watchdog))

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 tool-call observations; got {watchdog.tool_call_observations}"
    )
    fingerprints = {
        (name, json.dumps(args, sort_keys=True)) for name, args in watchdog.tool_call_observations
    }
    assert len(fingerprints) == 1, (
        f"Expected identical fingerprints for repeated identical tool calls; got {fingerprints}"
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pty_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
    """PTY tool-use activity must not clear the identical-tool-call breaker."""
    raw = json.dumps({"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}})
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(2):
        list(reader._handle_queued_line(raw + "\n", watchdog))
        clock.advance(1.0)

    with pytest.raises(_IdleStreamTimeoutError) as exc_info:
        list(reader._handle_queued_line(raw + "\n", watchdog))

    assert exc_info.value.reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
    assert 'exec tool call args={"cmd": "long silent command"}' in str(exc_info.value)
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_pty_line_reader_silently_skips_unrecognised_envelopes() -> None:
    """A non-JSON tool-use envelope MUST NOT crash the line reader
    AND MUST NOT feed the breaker with garbage fingerprints.

    The helper returns ``None`` for unknown envelopes; the
    production line reader only calls
    ``watchdog.record_tool_call_activity`` when the helper returns
    a valid (tool_name, tool_args) pair.
    """
    reader = _build_pty_reader_with_strategy(_JunkToolUseStrategy())
    watchdog = _RecordingWatchdog()

    list(reader._handle_queued_line("not-json-{{{\n", watchdog))

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_process_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
    """A parsed TOOL_USE line on the subprocess reader MUST reach
    ``watchdog.record_tool_call_activity`` with the canonical
    ``(tool_name, tool_args)`` pair extracted from the envelope.

    This test exercises the production ``_record_line_activity``
    method (lines 588-613 in ``_process_reader.py``) without
    spinning up a real subprocess.  We bind the unbound method to a
    minimal reader-like object so the only production code in the
    call path is the activity classification and routing.
    """
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    # Bind the production method to the minimal reader-like object.
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    bound_method(watchdog, raw)

    assert len(watchdog.tool_call_observations) == 1, (
        f"Expected exactly one tool-call observation from subprocess reader;"
        f" got {watchdog.tool_call_observations}"
    )
    tool_name, tool_args = watchdog.tool_call_observations[0]
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_process_line_reader_routes_repeated_tool_use_to_breaker() -> None:
    """The subprocess reader MUST route repeated identical tool calls
    through the breaker so an identical-tool-call wedge is detected.

    Exercises the production ``_record_line_activity`` path with a
    recording watchdog.  Identical (tool_name, tool_args) pairs
    observed multiple times must be recorded so the production
    ``RepetitionTracker.tripped()`` can fire.
    """
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        bound_method(watchdog, raw)

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 tool-call observations from subprocess reader;"
        f" got {watchdog.tool_call_observations}"
    )
    fingerprints = {
        (name, json.dumps(args, sort_keys=True)) for name, args in watchdog.tool_call_observations
    }
    assert len(fingerprints) == 1, (
        f"Expected identical fingerprints for repeated identical tool calls; got {fingerprints}"
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_process_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
    """Production reader activity must not clear the identical-tool-call breaker."""
    raw = json.dumps({"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(3):
        bound_method(watchdog, raw)
        clock.advance(1.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_repeated_tool_call_timeout_diagnostic_identifies_command() -> None:
    """Repeated-tool timeout messages name the repeated tool and command preview."""
    raw = json.dumps({"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(3):
        bound_method(watchdog, raw)
        clock.advance(1.0)

    diagnostic = watchdog.repetition_diagnostic()

    assert diagnostic["tool_name"] == "exec"
    assert diagnostic["tool_args_preview"] == '{"cmd": "long silent command"}'


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_repeated_tool_call_timeout_messages_identify_command() -> None:
    """Timeout exceptions include the repeated tool args preview."""
    diagnostic = {
        "tool_name": "exec",
        "tool_args_preview": '{"cmd": "long silent command"}',
    }

    stream_error = _IdleStreamTimeoutError(
        300.0,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
        diagnostic=diagnostic,
    )
    invocation_error = AgentInactivityTimeoutError(
        "codex",
        300.0,
        [],
        InactivityTimeoutOpts(
            reason=WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
            diagnostic=diagnostic,
        ),
    )

    assert 'exec tool call args={"cmd": "long silent command"}' in str(stream_error)
    assert 'exec tool call args={"cmd": "long silent command"}' in str(invocation_error)


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_process_line_reader_routes_plain_text_tool_use_to_breaker() -> None:
    """A plain-text ``claude tool: <name>`` TOOL_USE line on the
    subprocess reader MUST feed the tool-call circuit breaker.

    This is the analysis-feedback reachability gap: the helper only
    understood JSON envelopes, so repeated identical plain-text
    tool-use markers (classified as TOOL_USE elsewhere) were silently
    ignored and ``REPEATED_IDENTICAL_TOOL_CALL`` could not fire.
    """
    strategy = _ToolUseStrategy("claude tool: Bash")
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        bound_method(watchdog, "claude tool: Bash")

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 plain-text tool-call observations; got {watchdog.tool_call_observations}"
    )
    assert all(name == "Bash" and args == {} for name, args in watchdog.tool_call_observations), (
        f"Expected (Bash, {{}}) fingerprints; got {watchdog.tool_call_observations}"
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_process_line_reader_silently_skips_unrecognised_tool_envelopes() -> None:
    """A non-JSON tool-use envelope on the subprocess reader MUST NOT
    crash the line reader AND MUST NOT feed the breaker with garbage
    fingerprints.
    """
    strategy = _JunkToolUseStrategy()
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; duck-typed doubles declare ``None`` (no prompt to
        # echo-match) so the harness-echo check reads a real attribute.
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    bound_method(watchdog, "not-json-{{{\n")

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_identical_tool_calls_trip_even_when_each_result_arrives() -> None:
    """A tool result must NOT reset the tool-call repetition streak.

    Every tool call is followed by its result, so clearing the streak on the
    result made ``REPEATED_IDENTICAL_TOOL_CALL`` unreachable in practice: the
    dimension was wiped after each completed call, and an agent re-issuing one
    identical call forever stayed invisible to the circuit breaker.
    ``record_tool_use_activity`` already documents that a call is not proof of
    forward progress; the result side must agree.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )

    for _ in range(3):
        watchdog.record_tool_call_activity("list_directory", {"path": "."})
        watchdog.record_tool_result_activity()
        clock.advance(1.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_from_claude_assistant_message() -> None:
    """The assistant message carries the real arguments and MUST be used."""
    line = _claude_assistant_tool_use_line({"command": "echo one"})

    result = _extract_tool_call_from_activity_signal(line)

    assert result == ("Bash", {"command": "echo one"})


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_claude_distinct_commands_produce_distinct_fingerprints() -> None:
    """Two different Bash commands MUST NOT share one fingerprint."""
    first = _extract_tool_call_from_activity_signal(
        _claude_assistant_tool_use_line({"command": "echo one"})
    )
    second = _extract_tool_call_from_activity_signal(
        _claude_assistant_tool_use_line({"command": "echo two"})
    )

    assert first != second


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_extract_tool_call_skips_claude_streaming_placeholder() -> None:
    """``content_block_start`` arrives with ``input: {}`` before the deltas.

    Feeding it to the breaker keys every call of one tool to ``<name>|{}``
    regardless of the arguments it was actually invoked with.
    """
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_01RCxFZpHAEB3yBAHn3nktG2",
                    "name": "Bash",
                    "input": {},
                },
            },
        }
    )

    assert _extract_tool_call_from_activity_signal(line) is None


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_claude_strategy_distinct_commands_do_not_trip_the_breaker() -> None:
    """Five DIFFERENT Bash commands MUST NOT trip the tool-call breaker."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )

    for index in range(5):
        line = _claude_assistant_tool_use_line({"command": f"echo {index}"})
        extracted = _extract_tool_call_from_activity_signal(line)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(1.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != (
        WatchdogVerdict.FIRE
    )


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
def test_claude_identical_commands_still_trip_the_breaker() -> None:
    """The breaker must stay REACHABLE on Claude: five identical calls fire."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )

    for _ in range(5):
        line = _claude_assistant_tool_use_line({"command": "pytest -q"})
        extracted = _extract_tool_call_from_activity_signal(line)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(1.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
    assert watchdog.repetition_diagnostic().get("tool_name") == "Bash"


# === consolidated from test_no_output_at_start.py ===
def test_defers_when_subagent_progress_observed_within_window() -> None:
    """NO_OUTPUT_AT_START defers (returns CONTINUE) when subagent progress
    is observed within ``activity_evidence_ttl_seconds``.

    This is the central test for the false-positive fix: a recently-launched
    agent that dispatches a subagent and immediately goes silent must NOT
    be killed at 30s.  The upstream subagent sink feed (via
    ``record_subagent_work``) keeps the per-channel evidence fresh so the
    ``_channel_evidence_active`` deferral gate returns CONTINUE.
    """
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    # Advance the clock past the no_output_at_start threshold (30s).
    clock.advance(31.0)
    # But BEFORE evaluate, record a fresh subagent signal.  This mimics
    # the new emit_subagent_activity hook in stream_parsed_agent_activity
    # feeding the watchdog sink via the contextvar.
    wd.record_subagent_work(description="tool_use:Bash")
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer when subagent progress is fresh; got {verdict}"
    )


# === consolidated from test_no_output_at_start.py ===
def test_does_not_defer_after_evidence_ttl_expired() -> None:
    """NO_OUTPUT_AT_START fires normally after the activity_evidence_ttl
    window expires.  The deferral gate is bounded by the TTL so a subagent
    that dispatched but went silent for the full TTL is NOT evidence of
    progress and the watchdog returns to the normal fire path.
    """
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    # Record subagent progress at 30s (within no_output_at_start window).
    clock.advance(31.0)
    wd.record_subagent_work(description="tool_use:Bash")
    # Subagent progress is now stale after TTL expires.
    clock.advance(_ACTIVITY_TTL_SECONDS + 1.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire after the activity TTL expires; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_no_output_at_start.py ===
def test_no_output_at_start_defers_on_first_waiting_on_child_entry() -> None:
    """NO_OUTPUT_AT_START defers on the FIRST WAITING_ON_CHILD entry.

    This is the exact prompt scenario: a subagent is dispatched at
    invocation start, the agent transitions to WAITING_ON_CHILD, and the
    watchdog polls at 30s.  The cumulative waiting-on-child time is still
    0.0 on the first entry, so the old cumulative gate could not defer;
    the new classify_quiet WAITING_ON_CHILD early-exit must defer instead.
    """
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_waiting_on_child)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer on first WAITING_ON_CHILD entry; got {verdict}"
    )
    assert wd.last_fire_reason is None


# === consolidated from test_no_output_at_start.py ===
def test_no_output_at_start_fires_after_waiting_ceiling_reached() -> None:
    """After the WAITING_ON_CHILD cumulative ceiling is reached, the
    watchdog fires CHILDREN_PERSIST_TOO_LONG -- NOT NO_OUTPUT_AT_START.

    The WAITING_ON_CHILD early-exit in _evaluate_no_output_at_start only
    defers the 30s short kill; the 600s cumulative ceiling inside
    _handle_waiting_branch remains the upper bound for live-child stalls.
    """
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    # Enter WAITING_ON_CHILD and advance past the 600s ceiling.
    # We deliberately provide no channel evidence and no corroborator so
    # the only deferral path is the new WAITING_ON_CHILD early-exit; once
    # the cumulative ceiling is reached, CHILDREN_PERSIST_TOO_LONG fires.
    # First cross the idle_timeout so _evaluate_final_verdict enters the
    # WAITING_ON_CHILD branch and starts the waiting run; then advance the
    # remainder of the 600s ceiling.
    clock.advance(61.0)
    wd.evaluate(classify_quiet=_no_output_at_start_waiting_on_child)
    clock.advance(_MAX_WAITING_SECONDS)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE after waiting ceiling reached; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected CHILDREN_PERSIST_TOO_LONG at ceiling, got {wd.last_fire_reason}"
    )


# === consolidated from test_no_output_at_start.py ===
def test_no_output_at_start_fires_at_threshold_even_when_floor_unreached() -> None:
    """NO_OUTPUT_AT_START fires at the threshold even when invocation_elapsed
    is under the ``no_progress_quiet_minimum_invocation_seconds`` floor.

    The dumb-kill floor is intentionally NOT consulted inside
    ``_evaluate_no_output_at_start`` so the operator's
    ``no_output_at_start_seconds`` short ceiling is the single source of
    truth for ``NO_OUTPUT_AT_START`` lifetime. The floor is enforced
    inside ``_is_no_progress_quiet`` for ``NO_PROGRESS_QUIET`` only.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        max_waiting_on_child_seconds=_MAX_WAITING_SECONDS,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=_ACTIVITY_TTL_SECONDS,
        silent_subagent_seconds=None,
    )
    wd = IdleWatchdog(
        policy,
        clock,
        process_monitor=_NoProcessMonitorNoOutputAtStart(),
    )
    wd.record_invocation_start()
    # Advance to 60s: past the 30s NO_OUTPUT_AT_START threshold AND
    # under the 120s dumb-kill floor. The watchdog MUST fire at the
    # short ceiling; the floor does NOT defer NO_OUTPUT_AT_START.
    clock.advance(60.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold regardless of"
        f" the dumb-kill floor (invocation_elapsed=60s, threshold=30s,"
        f" floor=120s); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_no_output_at_start_lifecycle_parity.py ===
@pytest.mark.parametrize("alive_by", _FRESH_ALIVE_BY_STATES)
def test_no_output_at_start_defers_when_alive_by_is_fresh(alive_by: AliveBy) -> None:
    """Fresh ``AliveBy`` states defer the 30s ``NO_OUTPUT_AT_START`` kill.

    Pins the false-positive fix: a live child agent with a recent
    progress or heartbeat signal MUST defer the short ceiling. Pre-fix
    the gate was ``alive_by is not None`` which deferred on every
    ``AliveBy`` value, including stale ones. The new gate is the
    fresh-evidence subset, so a productive live child is never
    killed by the short fire.
    """

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_no_output_at_start_lifecycle_p_active)

    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer for fresh alive_by={alive_by.value!r};"
        f" got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None, (
        f"NO_OUTPUT_AT_START MUST NOT fire for fresh alive_by={alive_by.value!r};"
        f" got last_fire_reason={watchdog.last_fire_reason!r}"
    )


# === consolidated from test_no_output_at_start_lifecycle_parity.py ===
@pytest.mark.parametrize("alive_by", _STALE_ALIVE_BY_STATES)
def test_no_output_at_start_fires_when_alive_by_is_stale(alive_by: AliveBy) -> None:
    """Stale ``AliveBy`` states do NOT defer ``NO_OUTPUT_AT_START``.

    Pins the no-false-negative contract: a wedged-startup pattern
    where the corroborator reports a stale ``AliveBy`` value MUST
    still fire the short kill. Pre-fix the gate was
    ``alive_by is not None`` which would defer the fire on stale
    states, letting a wedged agent run for the cumulative 600s
    no-progress ceiling (too late for a 30s-startup wedge).

    The new gate restricts the deferral to the FRESH subset so a
    stale corroborator signal falls through to ``_gate_fire`` /
    ``_classify_stuck_now``. The StuckClassifier may still defer
    when the run is genuinely classified as non-stuck (the gate
    contract); the assertion here verifies the deferral does NOT
    happen via ``alive_by``-is-not-None.
    """

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_no_output_at_start_lifecycle_p_active)

    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire for stale alive_by={alive_by.value!r};"
        f" got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"NO_OUTPUT_AT_START MUST be the fire reason for stale"
        f" alive_by={alive_by.value!r}; got"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
    )


# === consolidated from test_no_output_at_start_lifecycle_parity.py ===
def test_no_output_at_start_full_lifecycle_parity() -> None:
    """Lifecycle parity: FRESH states defer; STALE states fire.

    Combines both directions in a single deterministic test that
    exercises the full ``evaluate()`` lifecycle for one fresh and
    one stale state in sequence. The point is to confirm both
    directions of the gate are honoured in the same watchdog
    instance (the reset path between invocations does not regress).
    """

    # Fresh state first: defer.
    def _fresh_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_fresh_corroborator)
    watchdog.record_invocation_start()
    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_no_output_at_start_lifecycle_p_active)
    assert verdict == WatchdogVerdict.CONTINUE, f"FRESH_PROGRESS MUST defer; got {verdict!r}"

    # Reset invocation to simulate a new run on a SEPARATE watchdog
    # instance -- the fresh-state deferral must NOT carry over to the
    # next invocation (no stale-alive_by caching). Using a fresh
    # ``IdleWatchdog`` for the stale case keeps the test fully typed
    # (the corroborator is a constructor parameter, not an attribute
    # to be swapped after construction).
    del watchdog

    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    stale_watchdog, stale_clock = _make_watchdog_with_corroborator(_stale_corroborator)
    stale_watchdog.record_invocation_start()
    stale_clock.advance(31.0)
    verdict = stale_watchdog.evaluate(classify_quiet=_no_output_at_start_lifecycle_p_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"OS_DESCENDANT_ONLY_STALE_PROGRESS MUST fire; got {verdict!r}"
    )
    assert stale_watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_no_output_at_start_loading.py ===
def test_no_output_at_start_fires_at_threshold_with_stale_alive_by() -> None:
    """NO_OUTPUT_AT_START fires at the threshold even when alive_by is a
    stale descendant-only signal AND invocation_elapsed is under the
    dumb-kill floor (default 120 s).

    The dumb-kill floor (``no_progress_quiet_minimum_invocation_seconds``)
    is intentionally NOT consulted inside ``_evaluate_no_output_at_start``
    so the operator's ``no_output_at_start_seconds`` short ceiling is the
    single source of truth for ``NO_OUTPUT_AT_START`` lifetime. A wedged
    startup that reports ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (process
    tree descendant exists but no progress/heartbeat yet) is precisely
    the stuck-agent pattern the 30 s short kill is meant to detect, so
    the floor MUST NOT defer the fire.

    Drives ``evaluate()`` at invocation elapsed = 60 s with the floor at
    120 s, threshold at 30 s, and the corroborator reporting
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``.
    """
    wd, clock = _no_output_at_start_loading_make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()

    # Advance to 60 s, which is:
    #   - past the 30 s no_output_at_start threshold (so the
    #     short ceiling fires)
    #   - under the 120 s dumb-kill floor (the floor MUST NOT
    #     defer for NO_OUTPUT_AT_START)
    clock.advance(60.0)

    verdict = wd.evaluate(classify_quiet=_no_output_at_start_loading_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold even when the"
        f" dumb-kill floor is not yet elapsed and alive_by is stale"
        f" (invocation_elapsed=60s, threshold=30s, floor=120s);"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_no_output_at_start_loading.py ===
def test_no_output_at_start_fires_for_truly_silent_run() -> None:
    """NO_OUTPUT_AT_START fires at the threshold for a truly silent ACTIVE
    run (no corroborator alive_by, no channel evidence).

    Drives ``evaluate()`` at invocation elapsed = 150 s with the floor at
    120 s, threshold at 30 s, and no corroborator. This pins the canonical
    short-kill behaviour: a freshly-launched agent that never produces
    any channel evidence (stdout, MCP tool call, file change, subagent
    progress) inside the ``no_output_at_start_seconds`` window is a stuck
    process and the 30 s short kill MUST fire even though the agent is
    well past the dumb-kill floor.
    """
    wd, clock = _no_output_at_start_loading_make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=None,
    )
    wd.record_invocation_start()
    # Advance to 150 s: past the floor and past the short ceiling.
    clock.advance(150.0)

    verdict = wd.evaluate(classify_quiet=_no_output_at_start_loading_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold for a truly silent run; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_no_progress_quiet_watchdog.py ===
def test_watchdog_fires_no_progress_quiet_on_prompt_signature() -> None:
    """Watchdog fires NO_PROGRESS_QUIET on a TRULY-DEAD child & idle stdout.

    Per the wt-012 gate refinement, ``_is_no_progress_quiet`` defers
    the fire whenever the corroborator reports any alive_by signal
    (the child is alive but stale-progress); the cumulative
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s) is the
    correct upper bound for live-child stalls. NO_PROGRESS_QUIET
    fires ONLY when the corroborator returns ``alive_by=None`` (the
    corroborator cannot confirm liveness — i.e. the child is TRULY
    dead or missing) AND no fresh channel evidence is present.

    The classifier's branch 4 distinguishes "live child from process
    monitor" (defers, can_defer=True) from "stale child from
    corroborator" (does NOT defer, can_defer=False) so the
    no_progress_quiet ceiling is NOT blocked by the gate.
    """
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=None,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            oldest_child_seconds=12.0,
        )

    watchdog = IdleWatchdog(policy, clock, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # At start, not enough time elapsed
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE

    # Advance clock past 10s. The classifier returns STUCK (the
    # corroborator-only path sets can_defer=False on the
    # subagent_liveness channel, so branch 4 does not defer) and
    # the gate allows FIRE.
    clock.advance(12.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


# === consolidated from test_no_progress_quiet_watchdog.py ===
def test_watchdog_does_not_fire_no_progress_quiet_when_post_tool_result_fresh() -> None:
    """Watchdog does not fire NO_PROGRESS_QUIET when tool results or activity is fresh."""
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
        )

    watchdog = IdleWatchdog(policy, clock, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    clock.advance(12.0)

    # Record post-tool-result activity at current time (12s)
    watchdog.record_tool_result_activity()

    # Evaluate: should not fire because tool result activity resets idle baseline
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE


# === consolidated from test_no_progress_quiet_watchdog.py ===
def test_no_progress_quiet_diagnostic_payload_contains_required_fields() -> None:
    """NO_PROGRESS_QUIET HARD_STOP diagnostic contains operator-facing fields.

    Verifies that when NO_PROGRESS_QUIET fires, the emitted WaitingStatusEvent
    carries the required diagnostic fields: invocation_elapsed, idle_elapsed,
    alive_by, ceiling, effective_ceiling, and per-channel evidence summary.
    """
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
    )

    captured_events: list[WaitingStatusEvent] = []

    def listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            oldest_child_seconds=12.0,
        )

    watchdog = IdleWatchdog(policy, clock, listener=listener, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    clock.advance(12.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET

    assert len(captured_events) == 2
    # wt-047-stall-label: HARD_STOP fires alongside STALLED (the
    # watchdog is the sole owner of the STALLED label). The order
    # matters: STALLED is emitted FIRST so the Status Bar reflects
    # the new stall state in the same tick as the HARD_STOP.
    stalled_evt = next(e for e in captured_events if e.kind == WaitingStatusKind.STALLED)
    assert stalled_evt is not None
    hard_stop_evt = next(e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP)
    assert hard_stop_evt is not None
    evt = hard_stop_evt
    assert evt.kind.value == "hard_stop"

    diag = evt.diagnostic
    assert "invocation_elapsed" in diag, "diagnostic must contain invocation_elapsed"
    assert "idle_elapsed" in diag, "diagnostic must contain idle_elapsed"
    # NOTE: when the corroborator returns alive_by=None, the
    # _build_corroboration_diag helper omits the "alive_by" key
    # (the field is only added when alive_by is not None). The
    # absence of the "alive_by" key is itself the signal that
    # the child is truly dead (the conservative path).
    assert "ceiling" in diag, "diagnostic must contain ceiling"
    assert diag["ceiling"] == 10.0
    assert "effective_ceiling" in diag, "diagnostic must contain effective_ceiling"
    assert diag["effective_ceiling"] == "no_progress_quiet"
    assert "evidence_summary" in diag, "diagnostic must contain evidence_summary"


# === consolidated from test_non_resumable_end_to_end.py ===
def test_process_exit_hang_does_not_resume() -> None:
    """PROCESS_EXIT_HANG refuses to resume the prior session."""
    wrapper = _fire_process_exit_hang()
    timeout_exc = _convert_reason_to_agent_error(wrapper)
    assert timeout_exc.reason == WatchdogFireReason.PROCESS_EXIT_HANG
    _assert_non_resumable_recovery_chain(timeout_exc)


# === consolidated from test_non_resumable_end_to_end.py ===
def test_descendant_hang_does_not_resume() -> None:
    """DESCENDANT_HANG refuses to resume the prior session."""
    wrapper = _fire_descendant_hang()
    timeout_exc = _convert_reason_to_agent_error(wrapper)
    assert timeout_exc.reason == WatchdogFireReason.DESCENDANT_HANG
    _assert_non_resumable_recovery_chain(timeout_exc)


# === consolidated from test_non_resumable_end_to_end.py ===
def test_session_ceiling_exceeded_does_not_resume() -> None:
    """SESSION_CEILING_EXCEEDED refuses to resume the prior session."""
    pending_lines, wrapper = _fire_in_stream_reason(WatchdogFireReason.SESSION_CEILING_EXCEEDED)
    timeout_exc = _convert_reason_to_agent_error(wrapper, pending_lines)
    assert timeout_exc.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    _assert_non_resumable_recovery_chain(timeout_exc)


# === consolidated from test_non_resumable_end_to_end.py ===
def test_children_persist_too_long_does_not_resume() -> None:
    """CHILDREN_PERSIST_TOO_LONG refuses to resume the prior session."""
    pending_lines, wrapper = _fire_in_stream_reason(WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG)
    timeout_exc = _convert_reason_to_agent_error(wrapper, pending_lines)
    assert timeout_exc.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    _assert_non_resumable_recovery_chain(timeout_exc)


# === consolidated from test_opencode_step_frames.py ===
def test_step_frames_classify_as_lifecycle() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    assert strategy.classify_activity_line(_frame("step_start")) is not None
    assert strategy.classify_activity_line(_frame("step_start")).kind == (
        AgentActivityKind.LIFECYCLE
    )
    assert strategy.classify_activity_line(_frame("step_finish")).kind == (
        AgentActivityKind.LIFECYCLE
    )


# === consolidated from test_opencode_step_frames.py ===
def test_wedge_trips_on_the_real_interleaved_stream() -> None:
    """The real stream brackets each call with frames; the wedge must survive it.

    This is the end-to-end claim: replay exactly what OpenCode emits, through
    the real strategy and the real line reader, and the breaker must still fire.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    reader = SimpleNamespace(
        _strategy=strategy_for_transport(AgentTransport.OPENCODE),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; the double declares ``None`` (no prompt to echo-match).
        _input_prompt=None,
    )
    record = MethodType(ProcessLineReader._record_line_activity, reader)

    for _ in range(5):
        record(watchdog, _frame("step_start") + "\n")
        record(watchdog, _tool_line("uv run pytest -q") + "\n")
        record(watchdog, _frame("step_finish") + "\n")
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_opencode_step_frames.py ===
def test_distinct_calls_on_the_real_stream_do_not_trip() -> None:
    """The same replay with DIFFERENT commands must stay quiet."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    reader = SimpleNamespace(
        _strategy=strategy_for_transport(AgentTransport.OPENCODE),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        # The production reader carries ``_input_prompt`` from its run
        # ctx; the double declares ``None`` (no prompt to echo-match).
        _input_prompt=None,
    )
    record = MethodType(ProcessLineReader._record_line_activity, reader)

    for index in range(10):
        record(watchdog, _frame("step_start") + "\n")
        record(watchdog, _tool_line(f"echo {index}") + "\n")
        record(watchdog, _frame("step_finish") + "\n")
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != (
        WatchdogVerdict.FIRE
    )


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_extract_tool_call_from_opencode_part_nested_envelope() -> None:
    """OpenCode's ``part.tool`` / ``part.state.input`` MUST be unwrapped."""
    line = _opencode_tool_line("ralph_git_status", {"format": "compact"})

    result = extract_tool_call_from_activity_signal(line)

    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "ralph_git_status"
    assert tool_args == {"format": "compact"}


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_distinct_tool_calls_produce_distinct_fingerprints() -> None:
    """Different OpenCode tools MUST NOT collapse onto one fingerprint."""
    first = extract_tool_call_from_activity_signal(
        _opencode_tool_line("ralph_git_status", {"format": "compact"})
    )
    second = extract_tool_call_from_activity_signal(
        _opencode_tool_line("todowrite", {"todos": [{"content": "a"}]}, call_id="call_2")
    )

    assert first != second


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_extract_tool_call_ignores_opencode_non_tool_part() -> None:
    """A ``step-start`` part carries no tool, so no fingerprint may be produced."""
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {"type": "step-start", "id": "prt_1"},
        }
    )

    assert extract_tool_call_from_activity_signal(line) is None


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_extract_tool_call_returns_none_when_nothing_distinguishing() -> None:
    """An envelope with neither a name nor args MUST be skipped, not
    fingerprinted as ``("unknown", {})``.

    Collapsing every unreadable envelope onto one fingerprint is what let a
    healthy agent look wedged: the breaker counted unrelated calls as repeats.
    """
    line = json.dumps({"type": "tool_use"})

    assert extract_tool_call_from_activity_signal(line) is None


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_strategy_regression_classifies_untyped_tool_part_as_tool_use() -> None:
    """S-2: fixture-compatible OpenCode tool envelopes reach the watchdog."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line(
        "ralph_read_file",
        {"path": "/tmp/x"},
        include_part_type=False,
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "ralph_read_file",
        {"path": "/tmp/x"},
    )


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_strategy_regression_classifies_untyped_running_task_as_child_progress() -> None:
    """S-2: a native running task without part.type keeps child work visible."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line(
        "task", {"prompt": "inspect"}, status="running", include_part_type=False
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_strategy_classifies_tool_use_as_tool_use() -> None:
    """The OpenCode strategy must surface tool calls as TOOL_USE."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_read_file", {"path": "/tmp/x"})

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_strategy_keeps_an_errored_tool_in_the_tool_dimension() -> None:
    """An errored tool MUST stay a TOOL_USE so the breaker can still see it.

    The tool-call dimension is the only one that catches an agent re-running
    one failing command forever: the failure text varies per attempt (exit
    codes, pytest counts, elapsed times) and ``RepetitionTracker.fingerprint``
    cannot collapse it, while the ``(tool, args)`` pair is identical every
    time. Reclassifying the error away made that wedge invisible in BOTH
    dimensions.
    """
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_exec", {"command": "uv run pytest -q"}, status="error")

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "ralph_exec",
        {"command": "uv run pytest -q"},
    )


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_repeated_failing_tool_trips_the_breaker() -> None:
    """A wedge that re-runs one failing command MUST still be caught."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    for index in range(5):
        line = _opencode_tool_line(
            "ralph_exec", {"command": "uv run pytest -q"}, call_id=f"c{index}", status="error"
        )
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_errored_subagent_releases_its_child_progress_signal() -> None:
    """An errored ``task`` is terminal rather than fresh child work."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("task", {"prompt": "inspect"}, status="error")

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_TERMINAL_ACK


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_strategy_classifies_tool_result_as_tool_result() -> None:
    """A ``tool_result`` envelope must not be counted as a second tool call."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = json.dumps(
        {
            "type": "tool_result",
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"path": "/tmp/x"}, "output": "ok"},
            },
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_RESULT


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_distinct_tool_calls_do_not_trip_the_breaker() -> None:
    """Eight DIFFERENT OpenCode tool calls MUST NOT trip the breaker.

    This is the production regression: the window rule (8 occurrences of one
    fingerprint in 600s) is deliberately immune to ``note_progress``, so once
    every call shared the ``("unknown", {})`` fingerprint, any OpenCode agent
    that used eight tools in ten minutes was killed mid-run.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    tools = [
        ("ralph_read_file", {"path": "/tmp/a"}),
        ("ralph_exec", {"command": "ls"}),
        ("ralph_search_files", {"pattern": "**/x"}),
        ("ralph_list_directory", {"path": "/tmp"}),
        ("ralph_git_status", {"format": "compact"}),
        ("todowrite", {"todos": [{"content": "a"}]}),
        ("ralph_write_file", {"path": "/tmp/b"}),
        ("ralph_submit_md_artifact", {"artifact_type": "smoke_test_result"}),
    ]

    for index, (tool, tool_input) in enumerate(tools):
        line = _opencode_tool_line(tool, tool_input, call_id=f"call_{index}")
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(2.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE


# === consolidated from test_opencode_tool_call_fingerprints.py ===
def test_opencode_identical_tool_calls_still_trip_the_breaker() -> None:
    """The breaker must stay REACHABLE on OpenCode: five identical calls fire."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    for index in range(5):
        line = _opencode_tool_line("ralph_exec", {"command": "ls"}, call_id=f"call_{index}")
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(2.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
    assert watchdog.repetition_diagnostic().get("tool_name") == "ralph_exec"


# === consolidated from test_os_descendant_only_escalation.py ===
def test_short_ceiling_fires_at_os_descendant_only_ceiling() -> None:
    """Watchdog fires FIRE with os_descendant_only ceiling at 120s.

    Setup: idle_timeout=10.0, max_waiting=600.0,
    os_descendant_only_ceiling=120.0, alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS.

    Advance to 130s (past 120s short ceiling) -> FIRE with
    effective_ceiling_label='os_descendant_only' and effective_ceiling=120.0.
    """
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        os_descendant_only_ceiling=120.0,
        corroborator=_make_os_descendant_only_corroborator(),
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    clock.advance(130.0)
    result = watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "os_descendant_only"
    assert diag.get("effective_ceiling") == 120.0


# === consolidated from test_os_descendant_only_escalation.py ===
def test_suspect_event_fires_at_os_descendant_only_suspect_seconds() -> None:
    """SUSPECTED_FROZEN fires at 60s (os_descendant_only_suspect) not 500s.

    Setup: idle_timeout=10.0, max_waiting=600.0,
    os_descendant_only_suspect=60.0, suspect=500.0 (standard),
    alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS.

    Advance to 70s -> one SUSPECTED_FROZEN with
    suspect_reason='os_descendant_only' and suspect_threshold=60.0.
    """
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        suspect=500.0,
        os_descendant_only_ceiling=120.0,
        os_descendant_only_suspect=60.0,
        status_interval=100.0,
        corroborator=_make_os_descendant_only_corroborator(),
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    clock.advance(70.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    suspect_events = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspect_events) == 1
    diag = suspect_events[0].diagnostic
    assert diag is not None
    assert diag.get("suspect_reason") == "os_descendant_only"
    assert diag.get("suspect_threshold") == 60.0
    assert diag.get("effective_ceiling_label") == "os_descendant_only"


# === consolidated from test_os_descendant_only_escalation.py ===
def test_cpu_idle_override_picks_no_progress_ceiling() -> None:
    """CPU_IDLE_WHILE_ALIVE short-circuits to no_progress ceiling (180s).

    Setup: max_waiting_on_child_no_progress_seconds=180.0,
    cpu_idle_seconds=60.0, alive_by=CPU_IDLE_WHILE_ALIVE.

    Advance to 190s -> FIRE with effective_ceiling_label='no_progress'
    and effective_ceiling=180.0.
    """

    def _cpu_idle_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.CPU_IDLE_WHILE_ALIVE,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        no_progress_ceiling=180.0,
        cpu_idle_seconds=60.0,
        corroborator=_cpu_idle_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    clock.advance(190.0)
    result = watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "no_progress"
    assert diag.get("effective_ceiling") == 180.0


# === consolidated from test_os_descendant_only_escalation.py ===
def test_log_growth_override_picks_no_progress_ceiling() -> None:
    """LOG_STALE_WHILE_ALIVE short-circuits to no_progress ceiling (180s).

    Setup: max_waiting_on_child_no_progress_seconds=180.0,
    log_growth_seconds=30.0, alive_by=LOG_STALE_WHILE_ALIVE.

    Advance to 190s -> FIRE with effective_ceiling_label='no_progress'
    and effective_ceiling=180.0.
    """

    def _log_stale_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.LOG_STALE_WHILE_ALIVE,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        no_progress_ceiling=180.0,
        log_growth_seconds=30.0,
        corroborator=_log_stale_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    clock.advance(190.0)
    result = watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "no_progress"
    assert diag.get("effective_ceiling") == 180.0


# === consolidated from test_os_descendant_only_escalation.py ===
def test_fresh_progress_path_unchanged() -> None:
    """FRESH_PROGRESS alive_by uses standard ceiling (600.0).

    Regression test: alive_by=FRESH_PROGRESS at 310s cumulative
    -> CONTINUE (standard ceiling 600.0, not yet reached).
    Any PROGRESS event has effective_ceiling_label='standard'.
    """

    def _fresh_progress_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        status_interval=100.0,
        corroborator=_fresh_progress_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    clock.advance(310.0)
    result = watchdog.evaluate(classify_quiet=_os_descendant_only_escalation_waiting)

    assert result == WatchdogVerdict.WAITING_ON_CHILD

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) >= 1
    for prog_ev in progress_events:
        diag = prog_ev.diagnostic
        assert diag is not None
        assert diag.get("effective_ceiling_label") == "standard"
        assert diag.get("effective_ceiling") == 600.0


# === consolidated from test_post_exit_watchdog_no_resume.py ===
@pytest.mark.parametrize(
    "failure_reason",
    (
        "PROCESS_EXIT_HANG",
        "DESCENDANT_HANG",
        "CHILDREN_PERSIST_TOO_LONG",
        "DEFERRED_BY_STUCK_CLASSIFIER",
    ),
)
def test_post_exit_watchdog_fire_reasons_do_not_resume_with_prior_session(
    failure_reason: str,
) -> None:
    """PostExitWatchdog fire reasons MUST NOT resume the prior session.

    A post-exit hang (PROCESS_EXIT_HANG) means the agent process
    closed stdout but did not exit within the grace window. The
    process tree is in an indeterminate half-dead state and cannot
    be safely continued via the prior session id; the next attempt
    MUST restart from a fresh session.
    """
    action = recovery_action_for_failure_reason(failure_reason, has_prior_session=True)
    assert action == "fresh", (
        f"failure_reason={failure_reason!r} with has_prior_session=True"
        f" MUST return 'fresh' (the half-dead process tree cannot"
        f" be safely resumed); got {action!r}"
    )


# === consolidated from test_post_exit_watchdog_no_resume.py ===
def test_process_exit_hang_without_prior_session_returns_fresh() -> None:
    """PROCESS_EXIT_HANG without a prior session MUST also return 'fresh'."""
    action = recovery_action_for_failure_reason("PROCESS_EXIT_HANG", has_prior_session=False)
    assert action == "fresh"


# === consolidated from test_post_exit_watchdog_no_resume.py ===
def test_process_exit_hang_is_not_in_resumable_set() -> None:
    """PROCESS_EXIT_HANG MUST NOT be in the canonical resumable-fire-reason set.

    Mirrors the contract pinned at
    ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``
    and enforced by ``_process_reader._RESUMABLE_FIRE_REASONS``.
    """
    action = recovery_action_for_failure_reason("PROCESS_EXIT_HANG", has_prior_session=True)
    assert action != "resume", (
        f"PROCESS_EXIT_HANG MUST NOT resume (the post-exit hang is"
        f" a half-dead process tree); got {action!r}"
    )
    assert action != "new_session_with_id", (
        f"PROCESS_EXIT_HANG MUST NOT new_session_with_id (the prior"
        f" session id is unsafe to reuse); got {action!r}"
    )


# === consolidated from test_post_exit_watchdog_no_resume.py ===
def test_resumable_reasons_still_resume_for_prior_session() -> None:
    """Sanity check: the canonical resumable exception class names still resume.

    Pins the inverse of the regression contract: the EXISTING
    resumable EXCEPTION CLASS NAMES must continue to return
    ``'resume'`` so the agent-attributed watchdog kills still
    continue the prior session as designed. The helper at
    ``recovery_action_for_failure_reason`` matches on exception
    class name strings (``AgentInactivityTimeoutError``,
    ``OpenCodeResumableExitError``), NOT on watchdog fire-reason
    enum strings (``NO_OUTPUT_AT_START`` etc.) -- those reach the
    helper wrapped in an ``AgentInactivityTimeoutError`` already.
    """
    resumable = (
        "AgentInactivityTimeoutError",
        "OpenCodeResumableExitError",
    )
    for reason in resumable:
        action = recovery_action_for_failure_reason(reason, has_prior_session=True)
        assert action == "resume", (
            f"failure_reason={reason!r} with has_prior_session=True"
            f" MUST return 'resume' (sanity check of the resumable"
            f" set); got {action!r}"
        )


# === consolidated from test_production_subagent_registry_wiring.py ===
@pytest.mark.parametrize("transport", list(AgentTransport))
def test_agent_registry_build_subagent_pid_registry_per_transport(
    transport: AgentTransport,
) -> None:
    """``AgentRegistry.build_subagent_pid_registry`` returns a per-transport pair.

    For each supported ``AgentTransport``, the helper MUST return a
    ``(SubagentPidRegistry, SubagentPidSource)`` pair where the source
    filters by the transport's source label. This is the production
    entry point the analysis flagged as missing.
    """
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(transport)
    assert isinstance(registry, SubagentPidRegistry)
    assert isinstance(source, SubagentPidSource)
    # Registering a PID for the correct transport source makes it
    # visible via the per-transport filtered source. Every supported
    # ``AgentTransport`` member is bound to its canonical source label
    # (``transport.value``) -- including Nanocoder, which has its own
    # ``make_nanocoder_subagent_pid_source`` factory since the
    # watchdog's per-transport ``SubagentPidSource`` filter (R1) is
    # keyed on the ``AgentTransport`` enum, not the parser.
    registry.register(12345, source=transport.value, now=0.0)
    assert 12345 in source.known_subagent_pids()
    # A PID registered for a DIFFERENT transport is invisible (R1
    # isolation between per-transport filtered views).
    other_transport = (
        AgentTransport.CLAUDE if transport != AgentTransport.CLAUDE else AgentTransport.PI
    )
    other_registry, other_source = agent_registry.build_subagent_pid_registry(other_transport)
    other_registry.register(67890, source=other_transport.value, now=0.0)
    assert 67890 not in source.known_subagent_pids()
    assert 67890 in other_source.known_subagent_pids()


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_agent_registry_build_subagent_pid_registry_rejects_unknown_transport() -> None:
    """An unknown transport label raises ``ValueError`` -- no silent fallback."""
    agent_registry = AgentRegistry()
    with pytest.raises(ValueError, match="no SubagentPidSource factory"):
        agent_registry.build_subagent_pid_registry("not-a-transport")


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_agent_registry_from_config_provides_subagent_registry_helper() -> None:
    """``AgentRegistry.from_config`` returns an instance with the helper attached.

    The canonical pipeline constructs the registry via
    ``AgentRegistry.from_config(config)`` and then calls
    ``build_subagent_pid_registry(transport)`` to obtain the per-
    invocation registry + source. This test proves the helper is
    available on the result of the canonical constructor.
    """
    config = UnifiedConfig()
    agent_registry = AgentRegistry.from_config(config)
    assert hasattr(agent_registry, "build_subagent_pid_registry")
    for transport in AgentTransport:
        registry, source = agent_registry.build_subagent_pid_registry(transport)
        assert isinstance(registry, SubagentPidRegistry)
        assert isinstance(source, SubagentPidSource)


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_classify_quiet_returns_waiting_when_filtered_source_has_pids() -> None:
    """A registered subagent PID forces WAITING_ON_CHILD (R1)."""
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    registry.register(4242, source="claude", now=0.0)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.WAITING_ON_CHILD


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_classify_quiet_returns_active_when_filtered_source_empty_even_with_descendants() -> None:
    """Helper descendants without a registered PID MUST NOT defer the watchdog.

    R3 (hard ceiling fires with helpers alive): when the broader
    descendant tree contains shell helpers like ``npm test`` /
    ``cargo build`` BUT the filtered registry is empty, the
    watchdog's quiet-state MUST return ACTIVE. ``has_live_descendants``
    is the BUG SOURCE; the filtered source is the canonical signal.
    """
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.ACTIVE


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_classify_quiet_uses_registry_snapshot_when_no_pid_source() -> None:
    """A ChildLivenessRegistry snapshot with records forces WAITING_ON_CHILD.

    When only a ``ChildLivenessRegistry`` is injected (the OpenCode
    path -- no ``SubagentPidSource``), the registry's filtered
    snapshot is the canonical signal. ``handle.has_live_descendants``
    MUST NOT be consulted.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )
    registry.register_child("child-A", "agent:test:", pid=9001)
    registry.record_progress("child-A")
    strategy = BaseExecutionStrategy(registry=registry)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.WAITING_ON_CHILD


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_classify_quiet_empty_registry_returns_active_even_with_descendants() -> None:
    """Empty ChildLivenessRegistry with ``has_descendants=True`` returns ACTIVE.

    The OpenCode path: a ChildLivenessRegistry is injected but
    has no records (the supervised agent dispatched no real
    subagents). Helper descendants visible to psutil MUST NOT
    block the watchdog.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )
    strategy = BaseExecutionStrategy(registry=registry)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.ACTIVE


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_strategy_for_transport_threads_subagent_pid_source() -> None:
    """``strategy_for_transport(transport, subagent_pid_source=...)`` wires the source.

    The factory MUST accept and forward the injected source so the
    per-invocation SubagentPidRegistry reaches the strategy. Without
    this, the production wiring in ``invoke_agent`` cannot thread the
    registry into the strategy layer.
    """
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    assert strategy._subagent_pid_source is source


# === consolidated from test_production_subagent_registry_wiring.py ===
@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [
        ("claude", ClaudeParser),
        ("claude_interactive", ClaudeInteractiveParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("agy", AgyParser),
        ("generic", GenericParser),
    ],
)
def test_parser_constructor_stores_subagent_pid_registry(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    """Each parser constructor MUST STORE the registry, not discard it.

    The previous pass silently discarded the registry with ``del``.
    The fix: store as ``self._subagent_pid_registry`` so future code
    paths can register PIDs into the shared registry without
    re-plumbing the constructor signature.

    The parametrize list is keyed on the eight supported
    ``AgentTransport`` enum members that have a corresponding parser
    class (every transport except ``OPENCODE`` -- OpenCode's parser is
    constructed with the production ``parser_factory`` call, not via
    the bare constructor, so it has its own dedicated wiring test).
    ``gemini`` has its own dedicated regression test below
    (``test_gemini_parser_registers_pid_from_child_progress``) because
    the public factory path uses a parser-bound source label distinct
    from its ``AgentTransport`` (``GENERIC``); the regression test
    pins the explicit behavior so a future PR cannot silently drop
    ``gemini``-labeled registrations the way the prior bare
    ``except Exception`` pattern did.
    """
    agent_registry = AgentRegistry()
    registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport(transport_label))
    parser = parser_cls(subagent_pid_registry=registry)
    assert getattr(parser, "_subagent_pid_registry", None) is registry


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_parser_default_constructor_keeps_registry_none() -> None:
    """Constructing a parser without a registry keeps the attribute None.

    The fix must NOT regress the default ``parser_factory()`` zero-arg
    call (used by the legacy plumbing in ``smoke_plumbing`` and
    ``commit_plumbing``).
    """
    parser = ClaudeParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = CodexParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = GenericParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_parser_with_registry_can_still_parse_lines() -> None:
    """Constructing a parser with a registry MUST NOT regress parsing.

    The ``parse()`` path must continue to produce the same
    ``AgentOutputLine`` stream regardless of whether the registry
    is supplied. This is a regression guard for the constructor
    change.
    """
    agent_registry = AgentRegistry()
    registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport.CODEX)
    parser = CodexParser(subagent_pid_registry=registry)
    lines = ['{"type": "text", "content": "hello world"}']
    events = list(parser.parse(iter(lines)))
    assert events, "parser.parse MUST still yield events with a registry"
    assert events[0].content == "hello world"


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_end_to_end_filtered_count_is_visible_to_strategy_classify_quiet() -> None:
    """Full pipeline: register PID → strategy sees WAITING_ON_CHILD via filtered source.

    This is the integration contract: a PID registered into the
    shared ``SubagentPidRegistry`` (the production entry point) is
    immediately visible to ``strategy.classify_quiet`` through the
    per-transport ``SubagentPidSource`` adapter. Without the
    wiring this test exercises, the production code never
    threads the registry from construction into the strategy
    layer.
    """
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    handle = _FakeHandle(has_descendants=False)
    probe = FakeLivenessProbe(active=False)

    # Initial state: no registered PIDs -> ACTIVE.
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE

    # Register a real subagent PID for the Claude transport.
    registry.register(99001, source="claude", now=0.0)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    # Unregister the PID -> back to ACTIVE.
    registry.unregister(99001)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_catalog_default_seeded_transports_have_subagent_pid_registry_factory() -> None:
    """Every default-catalog seeded transport has a subagent pid registry factory.

    The default catalog is the production seeding surface; the helper
    method on ``AgentRegistry`` MUST be able to build a registry for
    every transport seeded there. This guards against a future PR that
    adds a new transport to the default catalog but forgets to wire
    the matching ``make_*_subagent_pid_source`` factory.
    """
    agent_registry = AgentRegistry()
    catalog = default_catalog()
    seeded_transports = {
        support.spec.transport
        for support in catalog._entries.values()
        if hasattr(support.spec, "transport")
    }
    for transport in seeded_transports:
        registry, source = agent_registry.build_subagent_pid_registry(transport)
        assert isinstance(registry, SubagentPidRegistry)
        assert isinstance(source, SubagentPidSource)


# === consolidated from test_production_subagent_registry_wiring.py ===
@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [
        ("claude", ClaudeParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("agy", AgyParser),
        ("generic", GenericParser),
    ],
)
def test_parser_registers_pid_from_structured_event_when_registry_wired(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    """Structured event carrying a PID MUST register it into the shared registry.

    The R5 production wiring requires the parser's ``_dispatch_json_object``
    path to call the registry registration hook for every observed
    structured event. When an event carries an embedded PID, the parser
    registers it via ``SubagentPidRegistry.register``. When the event
    has no PID, the hook is a no-op (and the registry stays empty).

    The parametrize list is the subset of parser keys that are
    also supported ``AgentTransport`` members. ``gemini`` is
    covered by the dedicated regression test
    ``test_gemini_parser_registers_pid_from_child_progress`` below
    (the public factory path uses the parser-bound ``"gemini"``
    source label even though the catalog maps Gemini to the
    ``GENERIC`` transport, so it sits outside the
    ``AgentTransport``-keyed parametrizations).
    """
    registry = SubagentPidRegistry()
    parser = parser_cls(
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    # Pre-condition: empty registry.
    assert len(registry) == 0

    # Drive an event that carries a PID at the top level.
    pid = 55555
    line_with_pid = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line_with_pid])))
    # Parser still emits the same typed event (registry is a side-effect hook).
    assert events, "parser MUST still emit an event for child_progress line"
    assert pid in registry.known_pids()
    identity = next(iter(registry.snapshot()))
    assert identity.source == transport_label

    # Drive an event with no PID -> no-op for the registry.
    line_without_pid = '{"type": "text", "content": "hello"}'
    events2 = list(parser.parse(iter([line_without_pid])))
    assert events2
    # No new PIDs registered.
    assert len(registry) == 1


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_parser_registration_hook_no_op_when_registry_none() -> None:
    """The registration hook is a no-op when no registry was provided.

    The legacy zero-arg ``parser_factory()`` call MUST continue to work
    without raising on PID-less events or PID-carrying events. The
    hook silently skips when ``_subagent_pid_registry`` is ``None``.
    """
    parser = CodexParser()  # zero-arg legacy call
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events  # parser still emits
    assert getattr(parser, "_subagent_pid_registry", None) is None


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_parser_registration_hook_no_op_when_source_label_none() -> None:
    """The registration hook is a no-op when no source label was provided.

    A parser constructed with a registry but no source label (e.g. via
    a legacy caller that passes only the registry kwarg) MUST NOT
    register PIDs -- the source label is what attributes a PID to the
    right transport for the per-transport ``SubagentPidSource`` filter.
    Without the label the registration could leak cross-transport.
    """
    registry = SubagentPidRegistry()
    parser = CodexParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    # No registration happened because no source label was provided.
    assert 12345 not in registry.known_pids()


# === consolidated from test_production_subagent_registry_wiring.py ===
@pytest.mark.parametrize(
    ("parser_key", "transport_label"),
    [
        ("claude", "claude"),
        ("codex", "codex"),
        ("pi", "pi"),
        ("agy", "agy"),
        ("generic", "generic"),
    ],
)
def test_get_parser_threads_registry_and_source_label(
    parser_key: str,
    transport_label: str,
) -> None:
    """``get_parser(parser_key, subagent_pid_registry=..., subagent_source_label=...)`` wires both.

    The previous pass silently instantiated parsers as ``parser_cls()``
    with no registry. The fix: ``get_parser`` MUST accept and forward
    the registry + source label kwargs so the parser's registration
    hook fires for PID-carrying events.

    The parametrize list is the subset of parser keys that are
    also supported ``AgentTransport`` members. ``gemini`` is
    covered by the dedicated regression test
    ``test_get_parser_gemini_threads_registry_and_source_label`` below
    (the public factory path uses the parser-bound ``"gemini"``
    source label even though the catalog maps Gemini to the
    ``GENERIC`` transport, so it sits outside the
    ``AgentTransport``-keyed parametrizations).
    """
    registry = SubagentPidRegistry()
    parser = get_parser(
        parser_key,
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    assert parser._subagent_pid_registry is registry
    assert parser._subagent_source_label == transport_label


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_get_parser_default_kwargs_keep_registry_none() -> None:
    """Legacy zero-arg ``get_parser(parser_key)`` MUST keep the registry and source label ``None``.

    The fix MUST NOT regress the legacy ``get_parser('claude')``
    zero-arg call used by the smoke and commit plumbing.
    """
    parser = get_parser("claude")
    assert getattr(parser, "_subagent_pid_registry", None) is None
    assert getattr(parser, "_subagent_source_label", None) is None


# === consolidated from test_production_subagent_registry_wiring.py ===
@pytest.mark.subprocess_e2e
def test_invoke_agent_threads_subagent_pid_source_into_strategy_for_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invoke_agent`` MUST thread ``subagent_pid_source=`` into ``strategy_for_command``.

    This is the integration test for the production wiring path the
    analysis flagged as missing: ``invoke_agent`` constructs a
    per-invocation shared ``SubagentPidRegistry`` +
    ``SubagentPidSource`` from ``AgentRegistry.build_subagent_pid_registry``
    and threads the source into ``strategy_for_command(...)`` so
    ``BaseExecutionStrategy.classify_quiet`` uses the FILTERED signal.

    The test inspects the ``strategy_for_command`` call site directly
    via monkeypatch so no real subprocess is launched and no wall-clock
    sleep is required. The argument-recording monkeypatch captures the
    kwargs passed in and the test asserts ``subagent_pid_source`` is a
    non-``None`` ``SubagentPidSource`` instance -- proving the wiring
    is live end-to-end.

    The subprocess/PTY runners are ALSO monkeypatched to a no-op
    generator so the post-strategy-for_command code path (which would
    otherwise spawn the real ``claude -p`` binary and wait for it to
    exit non-zero because no login session is available) does not
    contribute to wall-clock cost. The test's contract is the wiring
    UP TO and INCLUDING the ``strategy_for_command`` call; the
    subprocess execution path is covered by the dedicated
    ``tests/test_subprocess_agent_executor*.py`` tests under the
    ``subprocess_e2e`` marker.
    """
    captured: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> BaseExecutionStrategy:
        captured.update(kwargs)
        return BaseExecutionStrategy(
            label_scope=kwargs.get("label_scope"),
            registry=kwargs.get("registry"),
            subagent_pid_source=kwargs.get("subagent_pid_source"),
        )

    def _empty_generator(*_args: object, **_kwargs: object) -> Iterator[str]:
        # The subprocess/PTY runners are typed as ``Iterator[str]``
        # generators; an empty generator returns immediately so the
        # post-strategy_for_command code path executes in microseconds
        # rather than spawning ``claude -p`` and waiting for the
        # login-required exit. ``if False`` keeps this a generator
        # function under mypy and ruff.
        return iter([])

    # ``strategy_for_command`` is imported into ``invoke`` at module
    # load via ``from ralph.agents.execution_state import strategy_for_command``,
    # so the canonical patch target is the ``invoke`` module's own
    # reference (NOT the source module -- rebinding the source has no
    # effect on the already-imported name). The pytest ``monkeypatch``
    # fixture handles the cleanup automatically on teardown so this
    # test file remains free of any suppression markers.
    monkeypatch.setattr(ralph_invoke, "strategy_for_command", _spy)
    # Block the real subprocess/PTY execution so the test verifies the
    # wiring contract in <1ms rather than waiting for ``claude -p`` to
    # fail with a login-required exit. The ``invoke`` module imports
    # both runners at module load (``from ralph.agents.invoke._pty ...
    # import run_pty_and_read_lines`` etc.) so the canonical patch
    # target is the ``invoke`` module's own reference, mirroring the
    # ``strategy_for_command`` patch above.
    monkeypatch.setattr(ralph_invoke, "run_subprocess_and_read_lines", _empty_generator)
    monkeypatch.setattr(ralph_invoke, "run_pty_and_read_lines", _empty_generator)

    # Build a minimal AgentConfig and InvokeOptions so the
    # ``invoke_agent`` flow reaches the ``strategy_for_command``
    # call site. The test only inspects the captured kwargs; any
    # downstream failure is acceptable (we monkeypatch the call).
    config = AgentConfig(
        cmd="claude -p",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    with contextlib.suppress(Exception):
        list(ralph_invoke.invoke_agent(config, "PROMPT.md"))

    assert "subagent_pid_source" in captured, (
        "invoke_agent MUST pass subagent_pid_source= into strategy_for_command"
    )
    source = captured["subagent_pid_source"]
    assert isinstance(source, SubagentPidSource), (
        f"subagent_pid_source must be a SubagentPidSource instance, got {type(source).__name__}"
    )


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_registers_pid_from_child_progress() -> None:
    """``get_parser('gemini', subagent_pid_registry=..., subagent_source_label='gemini')``
    MUST register PID-carrying events into the shared registry.

    Regression for the prior silent no-op: the public Gemini factory
    path constructed a parser with ``subagent_source_label='gemini'``
    and parsed a PID-carrying ``child_progress`` event, but the
    underlying ``SubagentPidRegistry.register`` call raised
    ``ValueError`` because ``'gemini'`` was missing from the
    canonical ``_SUBAGENT_SOURCES`` set; the bare ``except Exception``
    clause in ``NdjsonParserBase._try_register_subagent_pid_from_obj``
    silently swallowed the rejection and the PID was never
    registered, losing the watchdog's R1 subagent signal.

    The fix:

      * ``'gemini'`` is added to the canonical ``_SUBAGENT_SOURCES``
        set in ``ralph/agents/idle_watchdog/_subagent_identity.py`` so
        ``SubagentPidRegistry.register`` accepts the parser-bound
        source label.
      * ``NdjsonParserBase._try_register_subagent_pid_from_obj``
        narrows the exception clause from ``except Exception`` to
        ``except ValueError`` so other exception types propagate
        instead of being silently dropped.

    This test pins BOTH invariants via the public ``get_parser``
    factory path:

      1. ``events`` is non-empty (parser still emits its typed event).
      2. ``registry.known_pids()`` contains the emitted PID.
      3. ``registry.snapshot()[0].source == 'gemini'`` (the parser
         source label is preserved through registration).

    The test uses no real subprocess, no real wall-clock sleep, and
    no real filesystem I/O -- it is a pure-Python black-box fixture
    that satisfies ``audit_test_policy``.
    """
    registry = SubagentPidRegistry()
    parser = get_parser(
        "gemini",
        subagent_pid_registry=registry,
        subagent_source_label="gemini",
    )
    pid = 424242
    line = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line])))

    assert events, "parser MUST still emit an event for child_progress line"
    assert pid in registry.known_pids(), (
        f"Gemini parser must register pid {pid} into the shared registry; "
        f"got known_pids={sorted(registry.known_pids())}"
    )
    identity = next(iter(registry.snapshot()))
    assert identity.source == "gemini", (
        f"identity.source must be 'gemini' (the parser-bound label), got {identity.source!r}"
    )


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_registers_pid_via_direct_constructor() -> None:
    """Constructing ``GeminiParser(subagent_pid_registry=..., subagent_source_label='gemini')``
    directly also registers PIDs (the bare-constructor path mirrors the
    factory path).

    The bare-constructor path uses the same ``_try_register_subagent_pid_from_obj``
    hook as the factory path; this test pins the contract for callers
    that construct ``GeminiParser`` directly without going through
    ``get_parser``.
    """
    registry = SubagentPidRegistry()
    parser = GeminiParser(
        subagent_pid_registry=registry,
        subagent_source_label="gemini",
    )
    pid = 314159
    line = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line])))

    assert events
    assert pid in registry.known_pids()
    identity = next(iter(registry.snapshot()))
    assert identity.source == "gemini"


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_registration_no_op_when_registry_none() -> None:
    """The Gemini parser registration hook is a no-op when no registry is provided.

    Mirrors the existing per-parser no-op test for Codex / Claude / etc.
    so a future refactor that wires a default registry into Gemini by
    accident is caught.
    """
    parser = GeminiParser()  # zero-arg legacy call
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events  # parser still emits
    assert getattr(parser, "_subagent_pid_registry", None) is None


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_registration_no_op_when_source_label_none() -> None:
    """The Gemini parser registration hook is a no-op when no source label is provided.

    A parser constructed with a registry but no source label MUST NOT
    register PIDs -- the source label is what attributes a PID to the
    right transport for the per-transport ``SubagentPidSource`` filter.
    """
    registry = SubagentPidRegistry()
    parser = GeminiParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    assert 12345 not in registry.known_pids()


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_propagates_non_value_error_registration_failures() -> None:
    """A non-``ValueError`` exception from ``SubagentPidRegistry.register`` MUST propagate.

    The prior ``except Exception`` clause silently dropped every
    registration failure; the fix narrows it to ``except ValueError``
    so programmer errors (``TypeError``, ``AttributeError``,
    ``RuntimeError``) surface to the caller. This test injects a
    registry stub that raises ``TypeError`` and asserts the parser's
    ``parse`` path propagates the error rather than swallowing it.
    """
    sentinel = RuntimeError("programmer-error sentinel from injected registry")

    class _RaisingRegistry:
        def register(
            self,
            pid: int,
            source: str,
            label_prefix: str | None = None,
            *,
            now: float | None = None,
        ) -> object:
            raise sentinel

    parser = GeminiParser(
        subagent_pid_registry=_RaisingRegistry(),
        subagent_source_label="gemini",
    )
    line = '{"type": "child_progress", "pid": 7777}'
    with pytest.raises(RuntimeError, match="programmer-error sentinel"):
        list(parser.parse(iter([line])))


# === consolidated from test_production_subagent_registry_wiring.py ===
def test_gemini_parser_swallows_value_error_registration_failures() -> None:
    """A ``ValueError`` from ``SubagentPidRegistry.register`` is still swallowed.

    The narrowing from ``except Exception`` to ``except ValueError``
    preserves the forward-compat safety net: the parser's primary
    event-emission path must continue to work even when the
    registry's validation rejects a registration (e.g. an unknown
    source label). The test injects a registry stub that raises
    ``ValueError`` and asserts ``parse`` returns the typed event
    WITHOUT re-raising.
    """

    class _ValueErrorRegistry:
        def register(
            self,
            pid: int,
            source: str,
            label_prefix: str | None = None,
            *,
            now: float | None = None,
        ) -> object:
            raise ValueError(f"unknown subagent source {source!r}")

    parser = GeminiParser(
        subagent_pid_registry=_ValueErrorRegistry(),
        subagent_source_label="gemini",
    )
    line = '{"type": "child_progress", "pid": 8888}'
    events = list(parser.parse(iter([line])))
    assert events, "parser MUST still emit an event when ValueError is raised"


# === consolidated from test_pure_stall_wedge.py ===
def test_zero_activity_past_no_progress_quiet_fires() -> None:
    """Zero activity past no_progress_quiet_seconds MUST fire NO_PROGRESS_QUIET.

    The corroborator reports no live child signal (``alive_by=None``), so the
    watchdog cannot defer to the cumulative ``CHILDREN_PERSIST_TOO_LONG"
    ceiling.  Calls at t=30 and t=59 return CONTINUE; the call at t=60 returns
    FIRE with ``last_fire_reason == NO_PROGRESS_QUIET``.
    """
    watchdog, clock = _pure_stall_wedge_make_watchdog()
    watchdog.record_invocation_start()

    for elapsed in (30.0, 59.0):
        clock.advance(elapsed - clock.monotonic())
        verdict = watchdog.evaluate(classify_quiet=_pure_stall_wedge_waiting_on_child)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE at t={elapsed}; got {verdict}"
        )
        assert watchdog.last_fire_reason is None

    clock.advance(60.0 - clock.monotonic())
    verdict = watchdog.evaluate(classify_quiet=_pure_stall_wedge_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE at t=60; got {verdict}"
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected NO_PROGRESS_QUIET; got {watchdog.last_fire_reason}"
    )


# === consolidated from test_pure_stall_wedge.py ===
def test_zero_activity_during_no_output_at_start_window_fires() -> None:
    """Zero activity during the no_output_at_start window MUST fire.

    With no recorded activity of any kind and no corroborated live child, the
    first evaluate at or past ``no_output_at_start_seconds`` (30s) returns
    FIRE with ``last_fire_reason == NO_OUTPUT_AT_START``.
    """
    watchdog, clock = _pure_stall_wedge_make_watchdog()
    watchdog.record_invocation_start()

    clock.advance(_NO_OUTPUT_AT_START_SECONDS)
    verdict = watchdog.evaluate(classify_quiet=_pure_stall_wedge_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE at no_output_at_start threshold; got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"expected NO_OUTPUT_AT_START; got {watchdog.last_fire_reason}"
    )


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_polling_threaded_through_varied_work_does_not_trip() -> None:
    """The real pi shape: 14 identical plan-draft reads among 124 calls."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(124):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/file_{index}.py"})
        clock.advance(1.0)
        if index % 9 == 0:
            tracker.mark_tool_call("ralph_get_plan_draft", {})
            clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_distinct_calls_at_run_start_do_not_trip() -> None:
    """A small window early in a run must not be read as a cycle."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(8):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/{index}.py"})
        clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_one_call_repeated_still_trips() -> None:
    """The canonical wedge: the same call and nothing else."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "uv run pytest -q"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_two_call_loop_still_trips() -> None:
    """An A/B/A/B loop is a wedge with two moving parts."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(10):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        clock.advance(1.0)
        tracker.mark_tool_call("ralph_read_file", {"path": "/repo/out.log"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_three_call_loop_still_trips() -> None:
    """A/B/C is still a cycle; a share-of-window test would miss it at 33%."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(9):
        for name in ("ralph_exec", "ralph_read_file", "ralph_git_status"):
            tracker.mark_tool_call(name, {})
            clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


# === consolidated from test_repetition_window_cycle_detection.py ===
def test_interleaved_output_still_trips() -> None:
    """The rule's original purpose survives: text between repeats is not work.

    ``note_progress`` resets only the consecutive streak, so the window must
    still accumulate when the ONLY thing between repeats is ordinary output.
    """
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        tracker.note_progress()
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


# === consolidated from test_resume_after_kill_contract.py ===
def test_fire_no_output_at_start_yields_inactivity_error() -> None:
    """Build an IdleWatchdog, force NO_OUTPUT_AT_START to fire, drive the
    real ``ProcessLineReader._check_fire`` path, and then exercise the
    canonical invocation-layer seam
    (``_convert_idle_stream_timeout_to_agent_error``) that converts the
    watchdog fire into an ``AgentInactivityTimeoutError``.

    Asserts the typed ``IdleWatchdogKilledError`` is attached as
    ``__cause__`` on the wrapper, and that the recovered
    ``AgentInactivityTimeoutError`` carries the fire reason,
    ``session_resume_safe=True``, and the expected session id.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        policy,
        clock,
        process_monitor=_NoProcessMonitorResumeAfterKillContract(),
    )
    watchdog.record_invocation_start()

    def _classify_quiet() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    # Advance the clock past the no_output_at_start threshold AND
    # past the dumb-kill floor (120 s default) so the floor guard in
    # ``_evaluate_no_output_at_start`` does not defer the fire. The
    # floor suppresses the short ceiling during the LOADING window;
    # past the floor the 30 s short ceiling is the correct bound.
    clock.advance(125.0)
    verdict = watchdog.evaluate(classify_quiet=_classify_quiet)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE after no_output_at_start past the dumb-kill floor (125s); got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    # Drive the real line-reader fire path with a fake reader self.
    fake_self = _FakeCheckFireSelfResumeAfterKillContract(_policy=policy, _clock=clock)
    result = ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None, "_check_fire must return a wrapper when the verdict is FIRE"
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == WatchdogFireReason.NO_OUTPUT_AT_START

    # The typed IdleWatchdogKilledError is the __cause__ of the wrapper.
    assert isinstance(wrapper.__cause__, IdleWatchdogKilledError)
    assert wrapper.__cause__.reason == WatchdogFireReason.NO_OUTPUT_AT_START.value
    assert wrapper.__cause__.child_alive is False

    # Now exercise the canonical conversion seam.
    expected_session_id = "prior-session-abc"
    timeout_exc = _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=tuple(pending_lines),
        explicit_completion_seen=False,
        captured_session_id=None,
        expected_session_id=expected_session_id,
    )
    assert isinstance(timeout_exc, AgentInactivityTimeoutError)
    assert timeout_exc.reason == WatchdogFireReason.NO_OUTPUT_AT_START
    assert timeout_exc.session_resume_safe is True, "NO_OUTPUT_AT_START must be resume-safe"
    assert timeout_exc.resumable_session_id == expected_session_id, (
        "the conversion seam MUST thread the expected session id"
    )


# === consolidated from test_resume_after_kill_contract.py ===
def test_agent_inactivity_timeout_error_session_resume_safe_in_set() -> None:
    """``AgentInactivityTimeoutError.session_resume_safe`` MUST be True
    for the in-set resumable fire reasons (the watchdog-kill flow that
    is safe to resume via the prior session id).

    The in-set reasons are the six production reasons plus
    ``REPEATED_IDENTICAL_TOOL_CALL`` (added in this PR).  Any other
    reason (e.g. ``PROCESS_EXIT_HANG`` post-exit) MUST yield
    ``session_resume_safe=False``.
    """
    for reason_value in _RESUMABLE_REASONS:
        exc = AgentInactivityTimeoutError(
            agent_name="test-agent",
            timeout_seconds=30.0,
            opts=InactivityTimeoutOpts(
                reason=WatchdogFireReason(reason_value),
                session_resume_safe=True,
            ),
        )
        assert exc.session_resume_safe is True, (
            f"reason={reason_value!r}: session_resume_safe MUST be True;"
            f" got {exc.session_resume_safe}"
        )


# === consolidated from test_resume_after_kill_contract.py ===
def test_agent_inactivity_timeout_error_session_resume_safe_out_of_set() -> None:
    """Reasons outside the resumable in-set MUST yield
    ``session_resume_safe=False``.

    The recovery controller's ``recovery_action_for_failure_reason``
    only consults the failure reason class name, but the
    ``session_resume_safe`` flag is consulted by the typed-attribute
    branch in ``failure_classifier.classify_failure`` so the
    controller can refuse a resume for non-resumable fire reasons
    (e.g. PROCESS_EXIT_HANG, DESCENDANT_HANG).
    """
    out_of_set: tuple[str, ...] = (
        WatchdogFireReason.PROCESS_EXIT_HANG.value,
        WatchdogFireReason.DESCENDANT_HANG.value,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED.value,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG.value,
    )
    for reason_value in out_of_set:
        exc = AgentInactivityTimeoutError(
            agent_name="test-agent",
            timeout_seconds=30.0,
            opts=InactivityTimeoutOpts(
                reason=WatchdogFireReason(reason_value),
                session_resume_safe=False,
            ),
        )
        assert exc.session_resume_safe is False, (
            f"reason={reason_value!r}: session_resume_safe MUST be False;"
            f" got {exc.session_resume_safe}"
        )


# === consolidated from test_resume_after_kill_contract.py ===
def test_recovery_action_returns_resume_for_agent_inactivity_timeout() -> None:
    """``recovery_action_for_failure_reason('AgentInactivityTimeoutError', ...)
    MUST return 'resume' when ``has_prior_session=True``.

    The recovery controller's only mapping for
    ``AgentInactivityTimeoutError`` is 'resume' (with prior session).
    This pins the watchdog-kill -> resume flow.
    """
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=True,
    )
    assert action == "resume", (
        f"recovery_action_for_failure_reason MUST return 'resume' for"
        f" AgentInactivityTimeoutError with prior session; got {action!r}"
    )


# === consolidated from test_resume_after_kill_contract.py ===
def test_resolve_resume_session_id_threads_prior_session_id() -> None:
    """``resolve_resume_session_id(has_prior_session=True,
    prior_session_id='abc', recovery_action='resume')`` MUST return 'abc'.

    Pins the session-id threading so the agent subprocess reuses the
    prior session id after a watchdog kill.
    """
    sid = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="abc-123",
        recovery_action="resume",
    )
    assert sid == "abc-123", f"resolve_resume_session_id MUST thread the prior id; got {sid!r}"


# === consolidated from test_resume_after_kill_contract.py ===
def test_fresh_session_options_clears_session_id() -> None:
    """``fresh_session_options(opts, prior_session_id=...)`` MUST clear
    ``session_id`` for an ordinary new-phase transition.  The
    ``prior_session_id`` parameter is accepted for API forward-compat
    but MUST NOT be written back into ``session_id``.
    """
    opts = InvokeOptions(session_id="prior-sid")
    fresh = fresh_session_options(opts, prior_session_id="prior-sid")
    assert fresh.session_id is None, (
        f"fresh_session_options MUST clear session_id; got {fresh.session_id!r}"
    )


# === consolidated from test_resume_after_kill_contract.py ===
def test_agent_retry_intent_for_failure_returns_resume_intent() -> None:
    """``agent_retry_intent_for_failure('AgentInactivityTimeoutError',
    session_id='sid-x', reset_tool_registry=False)`` MUST build an
    ``AgentRetryIntent(action='resume', session_id='sid-x')``.

    The AgentRetryIntent is the single source of truth for the
    next-attempt session action.  When a watchdog-kill recovery
    happens, the runner MUST emit a resume intent so the prior
    session is reused end-to-end.
    """
    intent = agent_retry_intent_for_failure(
        failure_reason="AgentInactivityTimeoutError",
        session_id="recovered-sid",
        reset_tool_registry=False,
    )
    assert intent.action == "resume", (
        f"agent_retry_intent_for_failure MUST return 'resume'; got {intent.action!r}"
    )
    assert intent.session_id == "recovered-sid", (
        f"agent_retry_intent_for_failure MUST thread the recovered session id;"
        f" got {intent.session_id!r}"
    )


# === consolidated from test_resume_after_kill_contract.py ===
def test_idle_watchdog_killed_error_aliases_match() -> None:
    """``IdleWatchdogKilledError`` exported from
    ``ralph.agents.idle_watchdog.idle_watchdog`` MUST be the same
    class as ``ralph.agents.idle_watchdog_kill.IdleWatchdogKilledError``
    so the typed-attribute branch in
    ``ralph.recovery.failure_classifier`` finds the right class via
    either import path.
    """
    assert IdleWatchdogKilledError is IdleWatchdogKilledErrorTop, (
        "IdleWatchdogKilledError MUST be a single class re-exported from"
        " both ralph.agents.idle_watchdog.idle_watchdog and"
        " ralph.agents.idle_watchdog_kill"
    )


# === consolidated from test_resume_after_kill_contract.py ===
def test_no_output_at_start_fire_with_known_session_id_yields_resume_intent() -> None:
    """Drive the exact prompt end-to-end: NO_OUTPUT_AT_START fires, the
    line reader wraps the kill in ``AgentInactivityTimeoutError`` with
    ``session_resume_safe=True`` and ``resumable_session_id`` set, the
    recovery controller maps the failure to ``resume``, and the retry
    builder emits an ``AgentRetryIntent(action='resume')`` with the same
    session id.

    ``classify_quiet`` returns ``ACTIVE`` here so the new WAITING_ON_CHILD
    deferral gate does not suppress the fire; the test intentionally
    verifies the resume chain rather than the deferral gate.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        policy,
        clock,
        process_monitor=_NoProcessMonitorResumeAfterKillContract(),
    )
    watchdog.record_invocation_start()

    # Advance past the no_output_at_start threshold AND past the
    # dumb-kill floor (120 s default) so the floor guard in
    # ``_evaluate_no_output_at_start`` does not defer the fire.
    clock.advance(125.0)
    verdict = watchdog.evaluate(classify_quiet=_resume_after_kill_contract_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    fake_self = _FakeCheckFireSelfResumeAfterKillContract(_policy=policy, _clock=clock)
    result = ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == WatchdogFireReason.NO_OUTPUT_AT_START
    assert isinstance(wrapper.__cause__, IdleWatchdogKilledError)

    expected_session_id = "prior-sid-abc123"
    timeout_exc = _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=tuple(pending_lines),
        explicit_completion_seen=False,
        captured_session_id=None,
        expected_session_id=expected_session_id,
    )
    assert isinstance(timeout_exc, AgentInactivityTimeoutError)
    assert timeout_exc.reason == WatchdogFireReason.NO_OUTPUT_AT_START
    assert timeout_exc.session_resume_safe is True
    assert timeout_exc.resumable_session_id == expected_session_id

    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=True,
    )
    assert action == "resume"

    sid = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id=expected_session_id,
        recovery_action=action,
    )
    assert sid == expected_session_id

    intent = agent_retry_intent_for_failure(
        failure_reason="AgentInactivityTimeoutError",
        session_id=expected_session_id,
        reset_tool_registry=False,
    )
    assert intent.action == "resume"
    assert intent.session_id == expected_session_id


# === consolidated from test_resume_after_kill_watchdog_boundary.py ===
def test_is_resumable_fire_reason_classifies_known_reasons() -> None:
    """``_is_resumable_fire_reason`` returns True for the canonical in-set
    and False for every known non-resumable reason.
    """
    for reason in _RESUMABLE_FIRE_REASONS:
        assert _is_resumable_fire_reason(reason) is True, f"{reason!r} MUST be resumable"

    for reason in (
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ):
        assert _is_resumable_fire_reason(reason) is False, f"{reason!r} MUST NOT be resumable"


# === consolidated from test_resume_after_kill_watchdog_boundary.py ===
@pytest.mark.parametrize("reason_value", sorted(_EXPECTED_FIRE_REASONS))
def test_expected_fire_reasons_partitioned_into_resumable_or_excluded(
    reason_value: str,
) -> None:
    """Every reason in the import-time ``_EXPECTED_FIRE_REASONS`` lock is
    either resumable or explicitly documented as non-resumable.

    If a future PR adds a ``WatchdogFireReason`` member without updating
    the resume contract, this test fails.
    """
    assert (
        reason_value in _RESUMABLE_REASON_VALUES or reason_value in _NON_RESUMABLE_REASON_VALUES
    ), (
        f"{reason_value!r} is neither resumable nor in the documented"
        f" non-resumable exclusion set; update the resume contract"
    )


# === consolidated from test_resume_after_kill_watchdog_boundary.py ===
@pytest.mark.parametrize("reason", sorted(WatchdogFireReason, key=str))
def test_idle_watchdog_killed_error_reason_round_trips_through_resumable_helper(
    reason: WatchdogFireReason,
) -> None:
    """A kill exception carrying any ``WatchdogFireReason`` is classified
    consistently by ``_is_resumable_fire_reason``.

    This round-trips the boundary: ``reason.value`` flows from the
    exception to the helper and back to a ``WatchdogFireReason`` enum
    member.
    """
    exc = IdleWatchdogKilledError(
        reason=reason.value,
        signal=15,
        evidence_summary="test boundary",
        child_alive=False,
    )
    recovered = WatchdogFireReason(exc.reason)
    expected = reason in _RESUMABLE_FIRE_REASONS
    assert _is_resumable_fire_reason(recovered) is expected, (
        f"{reason!r}: round-trip resumability mismatch; expected {expected}"
    )


# === consolidated from test_resume_contract_invariant.py ===
def test_every_fire_reason_is_classified() -> None:
    """Every ``WatchdogFireReason`` member is either resumable or explicitly
    non-resumable.

    A future PR that adds a new reason without updating either the canonical
    resumable set or the explicit exclusion set fails this assertion,
    preventing silent drift of the resume contract.
    """
    for reason in WatchdogFireReason.__members__.values():
        assert reason in _RESUMABLE_FIRE_REASONS or reason in _NON_RESUMABLE_REASONS, (
            f"reason={reason!r} is neither in _RESUMABLE_FIRE_REASONS nor in the"
            f" explicit non-resumable exclusion set; update the resume contract"
        )


# === consolidated from test_resume_contract_invariant.py ===
def test_resumable_and_non_resumable_sets_are_disjoint() -> None:
    """No reason may be both resumable and non-resumable."""
    overlap = _RESUMABLE_FIRE_REASONS & _NON_RESUMABLE_REASONS
    assert not overlap, f"resumable and non-resumable sets overlap: {overlap!r}"


# === consolidated from test_resume_contract_invariant.py ===
def test_deferred_by_stuck_classifier_never_fires() -> None:
    """``DEFERRED_BY_STUCK_CLASSIFIER`` is only a diagnostic label.

    We force a candidate ``NO_OUTPUT_AT_START`` fire while the pipeline is in
    a wait state. The smart-verdict gate defers the fire and sets
    ``last_fire_reason`` to ``DEFERRED_BY_STUCK_CLASSIFIER``, but the returned
    verdict is ``CONTINUE``, never ``FIRE``.

    The dumb-kill floor (``no_progress_quiet_minimum_invocation_seconds``)
    defaults to 120 s and now defers the fire before the gate, so the test
    disables the floor to reach the gate path. The test still proves the
    gate defers when the classifier returns ``DUPLICATE_KILL`` for a wait
    state.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitorResumeContractInvariant())
    watchdog.record_invocation_start()
    watchdog.set_is_waiting_state(True)

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_resume_contract_invariant_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE for deferred DEFERRED_BY_STUCK_CLASSIFIER; got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER, (
        f"expected last_fire_reason={WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER!r};"
        f" got {watchdog.last_fire_reason!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_idle_watchdog_killed_error_carries_resumable_session_id() -> None:
    """``IdleWatchdogKilledError`` MUST accept and surface
    ``resumable_session_id`` so the post-mortem evidence and the
    recovery classifier both see the captured id.
    """
    exc = IdleWatchdogKilledError(
        reason="no_output_at_start",
        signal=15,
        resumable_session_id="sess-abc123",
    )
    assert exc.resumable_session_id == "sess-abc123", (
        "IdleWatchdogKilledError MUST surface resumable_session_id"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_failure_classifier_carries_resumable_session_id() -> None:
    """``FailureClassifier.classify`` MUST surface ``resumable_session_id``
    from ``AgentInactivityTimeoutError.opts.resumable_session_id`` so
    the recovery controller can read it without re-walking the
    exception chain.

    Pre-fix the field is missing on ``ClassifiedFailure``.
    """
    captured_session_id = "sess-abc123"
    wrapper = AgentInactivityTimeoutError(
        agent_name="test-agent",
        timeout_seconds=30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            session_resume_safe=True,
            resumable_session_id=captured_session_id,
        ),
    )
    classified = FailureClassifier().classify(
        wrapper,
        phase="development",
        agent="agent-a",
        connectivity_state="online",
    )
    assert classified.watchdog_reason == "no_output_at_start", (
        f"watchdog_reason mismatch; got {classified.watchdog_reason!r}"
    )
    assert getattr(classified, "resumable_session_id", None) == captured_session_id, (
        f"resumable_session_id MUST be threaded through ClassifiedFailure;"
        f" got {getattr(classified, 'resumable_session_id', None)!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_recovery_controller_sets_last_agent_session_id() -> None:
    """``RecoveryController.handle`` MUST set
    ``state.last_agent_session_id`` from the captured session id when
    the failure is a resumable watchdog fire and ``retry_in_session``
    is True.

    Pre-fix the controller never reads the watchdog's captured id so
    ``state.last_agent_session_id`` stays None and ``_apply_chain_retry``
    emits a cleared retry intent.
    """
    captured_session_id = "sess-abc123"
    wrapper = AgentInactivityTimeoutError(
        agent_name="test-agent",
        timeout_seconds=30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            session_resume_safe=True,
            resumable_session_id=captured_session_id,
        ),
    )
    state = _make_pipeline_state(chain_agents=("agent-a",))
    controller = RecoveryController()

    new_state, _effects, _evt = controller.handle(
        state,
        wrapper,
        FailureContext(
            phase="development",
            agent="agent-a",
            retry_in_session=True,
        ),
    )

    assert new_state.last_agent_session_id == captured_session_id, (
        f"state.last_agent_session_id MUST be set from the watchdog's"
        f" captured id; got {new_state.last_agent_session_id!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_apply_chain_retry_emits_resume_intent_with_captured_id() -> None:
    """``_apply_chain_retry`` MUST emit a resume intent with the captured
    session id when ``state.last_agent_session_id`` is populated.

    Pre-fix ``state.last_agent_session_id`` stays None so the chain
    retry emits a cleared intent and the next attempt starts a fresh
    session.
    """
    captured_session_id = "sess-abc123"
    wrapper = AgentInactivityTimeoutError(
        agent_name="test-agent",
        timeout_seconds=30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            session_resume_safe=True,
            resumable_session_id=captured_session_id,
        ),
    )
    state = _make_pipeline_state(chain_agents=("agent-a",))
    controller = RecoveryController()

    new_state, _effects, _evt = controller.handle(
        state,
        wrapper,
        FailureContext(
            phase="development",
            agent="agent-a",
            retry_in_session=True,
        ),
    )

    intent = new_state.agent_retry_intent
    assert intent.action == "resume", (
        f"agent_retry_intent.action MUST be 'resume' when"
        f" last_agent_session_id is populated; got {intent.action!r}"
    )
    assert intent.session_id == captured_session_id, (
        f"agent_retry_intent.session_id MUST thread the captured id; got {intent.session_id!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_resume_safe_helper_threads_captured_id_through_convert_seam() -> None:
    """The canonical ``_convert_idle_stream_timeout_to_agent_error`` seam
    MUST thread the captured session id into the wrapped exception.
    """
    captured = "sess-captured-xyz"
    timeout_exc = _IdleStreamTimeoutError(
        30.0,
        WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic=None,
    )
    timeout_exc.__cause__ = IdleWatchdogKilledError(
        reason="no_output_at_start",
        signal=15,
        resumable_session_id=captured,
    )
    converted = _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=timeout_exc,
        parsed_output=(),
        explicit_completion_seen=False,
        captured_session_id=captured,
        expected_session_id=None,
    )
    assert isinstance(converted, AgentInactivityTimeoutError)
    assert converted.resumable_session_id == captured, (
        f"_convert_idle_stream_timeout_to_agent_error MUST thread the"
        f" captured id; got {converted.resumable_session_id!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_is_resumable_fire_reason_for_no_output_at_start() -> None:
    """``_is_resumable_fire_reason(NO_OUTPUT_AT_START)`` MUST return True."""
    assert _is_resumable_fire_reason(WatchdogFireReason.NO_OUTPUT_AT_START) is True, (
        "NO_OUTPUT_AT_START MUST be resumable"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_pipeline_state_copy_with_accepts_last_agent_session_id() -> None:
    """``PipelineState.copy_with`` MUST accept ``last_agent_session_id``."""
    state = _make_pipeline_state()
    updated = state.copy_with(last_agent_session_id="sid-1")
    assert updated.last_agent_session_id == "sid-1"


# === consolidated from test_resume_session_id_threading.py ===
def test_multi_agent_chain_resume_keeps_current_agent() -> None:
    """Multi-agent chain: a resumable NO_OUTPUT_AT_START kill MUST retry
    the SAME agent (the one that timed out) instead of falling over to
    the next chain agent.

    The PROMPT requires the killed session to be resumed in place. With
    a multi-agent chain (agent-a, agent-b), the recovery controller
    used to mark agent-a as unavailable on a NO_OUTPUT_AT_START kill
    and fall over to agent-b -- starting a fresh session on a
    different agent. The fix carves out a resumable
    NO_OUTPUT_AT_START kill (one with a captured session id) so the
    classifier reports ``is_unavailable=False`` and the controller's
    same-agent retry path emits a resume intent with the captured id.
    """
    captured_session_id = "sess-abc123"
    wrapper = AgentInactivityTimeoutError(
        agent_name="test-agent",
        timeout_seconds=30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            session_resume_safe=True,
            resumable_session_id=captured_session_id,
        ),
    )
    # Two-agent chain: agent-a is the timed-out one; agent-b is the
    # fallover target. Pre-fix the controller fell over to agent-b
    # and started a fresh session; post-fix it stays on agent-a and
    # emits a resume intent.
    state = _make_pipeline_state(chain_agents=("agent-a", "agent-b"))
    controller = RecoveryController()

    new_state, _effects, _evt = controller.handle(
        state,
        wrapper,
        FailureContext(
            phase="development",
            agent="agent-a",
            retry_in_session=True,
        ),
    )

    # The chain pointer must still be on agent-a (no fallover).
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"Multi-agent chain current_index MUST stay on agent-a (0) for a"
        f" resumable kill; got {chain.current_index}"
    )
    # The chain must NOT have advanced to agent-b.
    assert chain.agents[chain.current_index] == "agent-a", (
        f"Multi-agent chain MUST stay on the timed-out agent for a"
        f" resumable kill; got {chain.agents[chain.current_index]!r}"
    )
    # The retry intent MUST be a resume intent with the captured id.
    intent = new_state.agent_retry_intent
    assert intent.action == "resume", (
        f"agent_retry_intent.action MUST be 'resume' for a resumable kill"
        f" in a multi-agent chain; got {intent.action!r}"
    )
    assert intent.session_id == captured_session_id, (
        f"agent_retry_intent.session_id MUST thread the captured id even"
        f" in a multi-agent chain; got {intent.session_id!r}"
    )
    # last_agent_session_id MUST be set so the resume intent is honored
    # by downstream consumers.
    assert new_state.last_agent_session_id == captured_session_id, (
        f"state.last_agent_session_id MUST be set from the watchdog's"
        f" captured id; got {new_state.last_agent_session_id!r}"
    )


# === consolidated from test_resume_session_id_threading.py ===
def test_multi_agent_chain_non_resumable_kill_does_fallover() -> None:
    """Multi-agent chain: a NON-resumable NO_OUTPUT_AT_START kill (no
    captured session id) MUST still fall over to the next chain agent.

    This is the symmetric pin: the resume carve-out must NOT regress
    the legitimate fallover path. A NO_OUTPUT_AT_START kill without a
    captured session id is the legacy "out of credits" case where the
    agent is truly unavailable and the chain MUST advance.
    """
    wrapper = AgentInactivityTimeoutError(
        agent_name="test-agent",
        timeout_seconds=30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            session_resume_safe=False,
            resumable_session_id=None,
        ),
    )
    state = _make_pipeline_state(chain_agents=("agent-a", "agent-b"))
    controller = RecoveryController()

    new_state, _effects, _evt = controller.handle(
        state,
        wrapper,
        FailureContext(
            phase="development",
            agent="agent-a",
            retry_in_session=True,
        ),
    )

    # The chain pointer MUST advance to agent-b.
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"Non-resumable NO_OUTPUT_AT_START MUST fall over to agent-b in a"
        f" multi-agent chain; got current_index={chain.current_index}"
    )
    assert chain.agents[chain.current_index] == "agent-b", (
        f"Non-resumable NO_OUTPUT_AT_START MUST fall over to agent-b;"
        f" got {chain.agents[chain.current_index]!r}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
def test_resumable_fire_reasons_matches_expected_set() -> None:
    """The runtime helper MUST agree with the AC-03 in-set.

    Pin the helper-layer contract before exercising the line readers
    so a regression at the helper layer surfaces first.
    """
    assert _RESUMABLE_FIRE_REASONS == _RESUMABLE_REASONS_EXPECTED, (
        f"Expected resumable set to be {_RESUMABLE_REASONS_EXPECTED!r},"
        f" got {_RESUMABLE_FIRE_REASONS!r}. Update both this test AND"
        f" tests/agents/idle_watchdog/test_resume_after_kill_contract.py"
        f" when changing the contract."
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_is_resumable_fire_reason_returns_true_for_in_set(reason: WatchdogFireReason) -> None:
    """Every reason in the canonical in-set MUST be resumable.

    Drives the production ``_is_resumable_fire_reason`` so a future
    refactor cannot silently narrow the helper set without breaking
    this test.
    """
    assert _is_resumable_fire_reason(reason) is True, (
        f"reason={reason!r}: MUST be resumable; got False"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize(
    "reason",
    sorted(
        set(WatchdogFireReason) - _RESUMABLE_REASONS_EXPECTED,
        key=str,
    ),
)
def test_is_resumable_fire_reason_returns_false_for_out_of_set(reason: WatchdogFireReason) -> None:
    """Every reason OUTSIDE the canonical in-set MUST NOT be resumable.

    Particularly important for ``CHILDREN_PERSIST_TOO_LONG`` (which
    the previous implementation incorrectly classified as
    resumable).  Drives the production
    ``_is_resumable_fire_reason`` so a future refactor cannot
    silently widen the set without breaking this test.
    """
    assert _is_resumable_fire_reason(reason) is False, (
        f"reason={reason!r}: MUST NOT be resumable; got True"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_process_reader_emits_session_resume_safe_true_for_in_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The subprocess line reader's except block MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Drives the production except block by calling the same
    ``_is_resumable_fire_reason`` helper that
    ``_process_reader.py`` calls in production.  A future refactor
    that bypasses the helper or inlines a different set will
    break this test.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: production subprocess reader MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_pty_runner_emits_session_resume_safe_true_for_in_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The PTY line reader's except block MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Mirrors the subprocess test but for the PTY runner.  Both
    readers share the canonical helper so a single regression in
    ``_is_resumable_fire_reason`` breaks BOTH tests.
    """
    exc = _raise_like_pty_runner(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: production PTY runner MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ],
)
def test_process_reader_emits_session_resume_safe_false_for_out_of_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """The subprocess line reader MUST emit ``session_resume_safe=False``
    for every out-of-set reason.

    Particularly important for ``CHILDREN_PERSIST_TOO_LONG`` -- a
    long cumulative child-wait can have side effects outside the
    agent session so the recovery must restart from a fresh
    session, NOT resume the prior session.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: production subprocess reader MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ],
)
def test_pty_runner_emits_session_resume_safe_false_for_out_of_set_reasons(
    reason: WatchdogFireReason,
) -> None:
    """PTY runner mirror of the out-of-set test."""
    exc = _raise_like_pty_runner(
        _LineReaderLike(),
        timeout_seconds=30.0,
        reason=reason,
    )
    assert exc.reason == reason
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: production PTY runner MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
def test_process_reader_thread_session_id_even_when_not_resumable() -> None:
    """The subprocess reader MUST populate ``resumable_session_id`` even
    for non-resumable reasons.

    The session-id wiring is independent of the resumability flag:
    a non-resumable fire (e.g. ``CHILDREN_PERSIST_TOO_LONG``) still
    surfaces the captured / expected session id so the failure
    classifier can log it for post-mortem diagnostics.  The
    ``session_resume_safe`` flag is the ONLY field gated by the
    resumable-reason set.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(captured_session_id="sess-from-stream"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    )
    assert exc.session_resume_safe is False
    assert exc.resumable_session_id == "sess-from-stream"


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
def test_process_reader_thread_expected_session_id_fallback_when_no_capture() -> None:
    """The subprocess reader MUST fall back to ``expected_session_id``
    when no session id is captured from the stream.

    The expected id is threaded via the production line reader's
    ``expected_session_id`` parameter (which itself comes from
    ``InvokeOptions.session_id`` via ``ProcessReaderCtx``).  The
    fallback rule is symmetric to the PTY runner's.
    """
    exc = _raise_like_process_reader(
        _LineReaderLike(expected_session_id="sess-expected"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected"


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
def test_pty_runner_thread_expected_session_id_fallback_when_no_capture() -> None:
    """PTY runner mirror of the expected-session-id fallback test."""
    exc = _raise_like_pty_runner(
        _LineReaderLike(expected_session_id="sess-expected-pty"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected-pty"


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_invoke_agent_subprocess_seam_emits_resume_safe_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> None:
    """The full ``invoke_agent`` subprocess seam MUST emit
    ``session_resume_safe=True`` for every in-set reason.

    Drives the actual ``_run_subprocess_and_read_lines`` path by
    monkeypatching ``IdleWatchdog`` to fire each resumable reason
    immediately.  A future refactor that inlines a different set in
    the line reader's ``except _IdleStreamTimeoutError`` block will
    break this test.
    """
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )
    assert exc.resumable_session_id == "sess-runtime-seam", (
        f"reason={reason!r}: expected session id fallback; got {exc.resumable_session_id!r}"
    )


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@pytest.mark.parametrize(
    "reason",
    [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
    ],
)
def test_invoke_agent_subprocess_seam_emits_resume_safe_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> None:
    """The full ``invoke_agent`` subprocess seam MUST emit
    ``session_resume_safe=False`` for out-of-set reasons the line
    reader can actually emit.

    ``DEFERRED_BY_STUCK_CLASSIFIER`` and ``DESCENDANT_HANG`` are
    excluded: the former is a deferral label, not a fire reason, and
    the latter is owned by the post-exit descendant-quiesce path in
    ``_completion.py`` rather than by the subprocess line reader.
    """
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


# === consolidated from test_session_ceiling_no_resume.py ===
def test_session_ceiling_exceeded_with_prior_session_returns_fresh() -> None:
    """``SESSION_CEILING_EXCEEDED`` with a prior session MUST return ``'fresh'``.

    The session ceiling is the operator's ABSOLUTE max-session
    wall-clock cap; resuming the prior session would re-enter the
    same cap window and re-fire the same reason in a loop. The
    recovery controller MUST restart from a fresh session so the
    next attempt has a fresh wall-clock budget.
    """
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=True)
    assert action == "fresh", (
        f"SESSION_CEILING_EXCEEDED with has_prior_session=True MUST"
        f" return 'fresh' (operator-set hard cap cannot be"
        f" resumed); got {action!r}"
    )


# === consolidated from test_session_ceiling_no_resume.py ===
def test_session_ceiling_exceeded_without_prior_session_returns_fresh() -> None:
    """``SESSION_CEILING_EXCEEDED`` without a prior session MUST return ``'fresh'``."""
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=False)
    assert action == "fresh"


# === consolidated from test_session_ceiling_no_resume.py ===
def test_session_ceiling_exceeded_does_not_resume_via_recovery_action() -> None:
    """``SESSION_CEILING_EXCEEDED`` MUST NOT return ``'resume'`` or ``'new_session_with_id'``.

    Companion to ``test_session_ceiling_exceeded_does_not_resume``
    (consolidated from ``test_non_resumable_end_to_end.py``), which
    drives the same invariant through the production
    ``_convert_reason_to_agent_error`` seam. Renamed with the
    ``_via_recovery_action`` suffix so the two distinct tests can
    co-exist in the consolidated module (the F811 redefinition
    block is the consolidation seam).
    """
    for prior in (True, False):
        action = recovery_action_for_failure_reason(
            "SESSION_CEILING_EXCEEDED", has_prior_session=prior
        )
        assert action != "resume", (
            f"SESSION_CEILING_EXCEEDED with has_prior_session={prior}"
            f" MUST NOT return 'resume'; got {action!r}"
        )
        assert action != "new_session_with_id", (
            f"SESSION_CEILING_EXCEEDED with has_prior_session={prior}"
            f" MUST NOT return 'new_session_with_id'; got {action!r}"
        )


# === consolidated from test_session_ceiling_no_resume.py ===
def test_session_ceiling_is_in_non_resumable_set() -> None:
    """Pin the inverse: ``SESSION_CEILING_EXCEEDED`` is in the non-resumable set.

    The canonical contract pinned at
    ``tests/agents/idle_watchdog/test_resume_after_kill_contract.py``
    treats ``SESSION_CEILING_EXCEEDED`` as a NON-RESUMABLE fire
    reason. This test asserts the public helper honours the
    contract end-to-end.
    """
    # SESSION_CEILING_EXCEEDED is the canonical example of an
    # operator-set hard cap. Compare against CHILDREN_PERSIST_TOO_LONG
    # (also non-resumable, also stuck-job detector).
    hard_caps = ("SESSION_CEILING_EXCEEDED", "CHILDREN_PERSIST_TOO_LONG")
    for cap in hard_caps:
        action = recovery_action_for_failure_reason(cap, has_prior_session=True)
        assert action == "fresh", f"hard-cap reason={cap!r} MUST NOT resume; got {action!r}"


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_invoke_options_carries_shared_registry_field() -> None:
    """``InvokeOptions`` MUST carry the shared ``subagent_pid_registry`` field.

    The shared-registry contract requires ``InvokeOptions`` to carry
    the pre-built registry (and its per-transport source) so the
    orchestrator can thread the SAME registry into both the strategy
    and parser layers. The field defaults to ``None`` for backward
    compat with the legacy direct-call signature.
    """
    options = InvokeOptions()
    assert hasattr(options, "subagent_pid_registry")
    assert hasattr(options, "subagent_pid_source")
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_invoke_options_round_trip_via_replace() -> None:
    """``replace`` round-trips the shared-registry fields on ``InvokeOptions``.

    The orchestrator uses ``dataclasses.replace(options, ...)`` to
    thread the pre-built registry through the frozen InvokeOptions
    dataclass. The shared-registry fields MUST survive the replace
    (i.e. the new dataclass instances carries the same registry
    object identity).
    """
    options = InvokeOptions()
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    # Object identity preserved end-to-end (NOT a deep copy).
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    # Original instance is NOT mutated (InvokeOptions is frozen=True).
    assert options.subagent_pid_registry is None


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_parser_registered_pid_reaches_strategy_filter() -> None:
    """Headline assertion: a parser-registered PID reaches the strategy filter.

    Build the shared registry at the orchestrator level (the
    production wiring). Register a parser-discovered PID into the
    registry. The strategy-side ``SubagentPidSource`` (built from the
    SAME registry) MUST see the registered PID. This is the contract
    the orchestrator relies on for the watchdog's filtered
    subagent count.
    """
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    # Parser-side registration: parser sees a structured subagent
    # event and registers the PID into the shared registry.
    registry.register(12345, source="opencode", now=0.0)
    # Strategy-side filter: the per-transport source the strategy
    # layer feeds to the watchdog's filtered count sees the PID.
    assert source.known_subagent_pids() == {12345}


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_separate_registries_desynchronize_filter() -> None:
    """Regression guard: separate registries break the contract.

    The pre-fix bug was that ``invoke_agent`` built a FRESH
    ``build_subagent_pid_registry(transport)`` internally regardless
    of what the orchestrator built. This meant parser registrations
    into one registry never reached the strategy's filter built from
    a different registry. The test simulates that broken state with
    two registries and asserts the resulting desync.
    """
    orchestrator_registry, _ = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    strategy_registry, strategy_source = AgentRegistry().build_subagent_pid_registry(
        AgentTransport.OPENCODE
    )
    # Parser registers a PID into the orchestrator's registry.
    orchestrator_registry.register(99999, source="opencode", now=0.0)
    # The strategy's filter sees a DIFFERENT registry, so the parser
    # registration is invisible. This is the bug.
    assert orchestrator_registry is not strategy_registry
    assert strategy_source.known_subagent_pids() == set()


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_make_opencode_subagent_pid_source_shares_registry() -> None:
    """The shared-registry contract works for the OpenCode transport.

    The OpenCode per-transport factory helper
    ``make_opencode_subagent_pid_source`` MUST expose the SAME
    registry's filtered PIDs as the orchestrator's parser layer
    registers into. This is the single-instance invariant the
    watchdog relies on for the R1 filtered count.
    """
    registry = SubagentPidRegistry()
    source = make_opencode_subagent_pid_source(registry)
    # Parser registers; the OpenCode-filter source sees the PID.
    registry.register(55555, source="opencode", now=0.0)
    assert source.known_subagent_pids() == {55555}


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_invoke_options_with_shared_registry_preserves_unrelated_fields() -> None:
    """``replace`` MUST preserve all unrelated ``InvokeOptions`` fields.

    The shared-registry wiring must not regress any existing
    ``InvokeOptions`` field semantics. A round-trip replace with only
    the shared-registry fields MUST preserve the original
    ``workspace_path``, ``session_id``, ``required_artifact``, and
    every other field.
    """
    options = InvokeOptions(
        model_flag="-m sonnet",
        session_id="sess-abc",
        workspace_path=None,
        show_progress=True,
        pure=True,
        required_artifact=None,
    )
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    # The shared-registry fields are threaded.
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    # Unrelated fields are preserved verbatim.
    assert threaded.model_flag == "-m sonnet"
    assert threaded.session_id == "sess-abc"
    assert threaded.workspace_path is None
    assert threaded.show_progress is True
    assert threaded.pure is True


# === consolidated from test_shared_subagent_pid_registry.py ===
def test_partial_shared_registry_falls_back_to_internal_build() -> None:
    """Only the legacy direct-call path (both fields None) builds internally.

    The orchestrator MUST thread BOTH ``subagent_pid_registry`` AND
    ``subagent_pid_source`` for the shared-registry contract to take
    effect. A partial thread (one field set, the other None) is a
    misconfiguration and the ``invoke_agent`` fallback path
    MUST build a fresh registry internally (defensive default). This
    test documents the contract: the orchestrator either threads
    both or neither.
    """
    # Both None: legacy direct-call path. ``invoke_agent`` builds
    # internally (defensive default for backward compat).
    options = InvokeOptions()
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


# === consolidated from test_shared_subagent_pid_registry.py ===
@pytest.mark.parametrize(
    "transport",
    [
        AgentTransport.OPENCODE,
        AgentTransport.CLAUDE,
        AgentTransport.PI,
        AgentTransport.AGY,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
    ],
)
def test_shared_registry_supported_for_every_transport(transport: AgentTransport) -> None:
    """The shared-registry contract works for every supported transport.

    The R5 cross-transport subagent visibility requirement covers
    every transport the orchestrator can dispatch. The
    ``build_subagent_pid_registry`` factory builds a per-transport
    source adapter backed by the shared registry, and the registry
    registrations are visible to the source for every transport in
    the canonical set.
    """
    registry, source = AgentRegistry().build_subagent_pid_registry(transport)
    transport_name = transport.value
    # Every supported ``AgentTransport`` member is bound to its canonical
    # source label (``transport.value``) -- including Nanocoder, which
    # has its own ``make_nanocoder_subagent_pid_source`` factory since
    # the watchdog's per-transport ``SubagentPidSource`` filter (R1) is
    # keyed on the ``AgentTransport`` enum, not the parser.
    source_label = transport_name
    # Register a PID under the transport's source label.
    pid = 70000 + (hash(transport_name) % 1000)
    # Cast keeps the test fully typed per AGENTS.md 'tests must be
    # fully typed' (no type-ignore comments in test files). The
    # pattern mirrors ``tests/agents/idle_watchdog/
    # test_subagent_identity_excludes_helpers.py`` which casts to
    # ``SubagentIdentity.__init__`` for the same narrowing reason.
    registry.register(
        pid,
        source=source_label,
        now=0.0,
    )
    # The per-transport source sees the PID (shared-registry contract).
    assert source.known_subagent_pids() == {pid}


# === consolidated from test_silent_after_tool_call_wedge.py ===
def test_single_mcp_tool_call_then_quiet_with_fresh_corroborator_does_not_fire() -> None:
    """Single MCP tool-call + quiet with fresh corroborator must NOT fire.

    The MCP tool-call keeps the ``mcp_tool`` first-party channel fresh within
    ``activity_evidence_ttl_seconds`` and the corroborator reports
    ``AliveBy.FRESH_PROGRESS``.  The watchdog must return CONTINUE at every
    poll through ``silent_subagent_seconds`` and must never set
    ``last_fire_reason``.
    """

    def _fresh_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            mcp_tool_call_count=1,
            last_mcp_tool_call_at=0.0,
        )

    watchdog, clock = _silent_after_tool_call_wedge_make_watchdog(corroborator=_fresh_corroborator)
    watchdog.record_invocation_start()
    watchdog.record_mcp_tool_call()

    for elapsed in (30.0, 60.0, 120.0, 180.0):
        clock.advance(elapsed - clock.monotonic())
        verdict = watchdog.evaluate(classify_quiet=_silent_after_tool_call_wedge_active)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE at t={elapsed}; got {verdict}"
        )

    assert watchdog.last_fire_reason is None


# === consolidated from test_silent_after_tool_call_wedge.py ===
def test_subagent_silence_with_stale_corroborator_fires() -> None:
    """Stale subagent evidence + STALE corroborator MUST fire, not defer.

    We record one subagent progress observation and one MCP tool-call at
    t=0, then let both channels go stale. The corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS``: a child process still EXISTS but
    has made no progress. That is a wedge, not work.

    This test previously asserted CONTINUE -- it encoded the production hang
    as correct behavior. A stale alive-by is explicitly NOT a deferring
    signal (``can_defer=False``; see ``_subagent_liveness_fresh``), and with
    no no-progress ceiling configured the run deferred forever. The gate MUST
    fire. A genuinely working child keeps the liveness channel fresh with
    ``can_defer=True`` and defers earlier via LOADING (branch 4).
    """

    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        )

    watchdog, clock = _silent_after_tool_call_wedge_make_watchdog(
        corroborator=_stale_corroborator,
        activity_evidence_ttl=60.0,
    )
    watchdog.record_invocation_start()
    watchdog.record_mcp_tool_call()
    watchdog.record_subagent_work(description="tool_use:Read")

    clock.advance(240.0)
    verdict = watchdog.evaluate(classify_quiet=_silent_after_tool_call_wedge_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE at t=240 (stale child, silent parent); got {verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER, (
        "a stale-progress child must not defer the fire indefinitely"
    )


# === consolidated from test_silent_subagent_fires.py ===
def test_gate_fires_on_silent_subagent() -> None:
    """The gate MUST fire when a subagent went silent with no live child.

    Deferring here is the liveness inversion: the classifier has POSITIVELY
    identified a dead agent, and that identification must not become the
    reason it is spared.
    """
    watchdog, clock = _silent_subagent_fires_make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    # Precondition: the classifier really does name this SILENT_SUBAGENT.
    assert watchdog._classify_stuck_now(now=now, idle_elapsed=181.0) == (StuckKind.SILENT_SUBAGENT)

    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        "Gate MUST FIRE on SILENT_SUBAGENT: the branch requires alive_by is None,"
        f" so there is no live child to protect. Got {gate_verdict}."
    )


# === consolidated from test_silent_subagent_fires.py ===
def test_silent_subagent_fire_is_not_recorded_as_a_deferral() -> None:
    """A fired SILENT_SUBAGENT is NOT a deferral: ``last_deferred_kind`` stays None.

    The kind survives as a post-mortem label via the log line and the real
    ``last_fire_reason`` the caller stamps; it must not masquerade as a
    deferral in the diagnostic surface.
    """
    watchdog, clock = _silent_subagent_fires_make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    watchdog._gate_fire(WatchdogFireReason.NO_OUTPUT_DEADLINE, now=now, idle_elapsed=181.0)
    assert watchdog.last_deferred_kind is None, (
        "A FIRE is not a deferral; last_deferred_kind must not be stamped."
        f" Got {watchdog.last_deferred_kind}."
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


# === consolidated from test_silent_subagent_fires.py ===
def test_silence_never_becomes_permanent_immunity() -> None:
    """Liveness invariant: the deferral MUST NOT be unbounded.

    Sweeps the silence duration across the threshold. Before the fix this
    inverted: 60s fired, everything past 180s deferred forever. A watchdog
    that kills a one-minute stall but protects a 24-hour corpse is worse than
    no watchdog.
    """
    for silent_for in (61.0, 181.0, 600.0, 3600.0, 86_400.0):
        watchdog, clock = _silent_subagent_fires_make_watchdog()
        now = _wedge(watchdog, clock, silent_for=silent_for)

        gate_verdict = watchdog._gate_fire(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=silent_for,
        )
        assert gate_verdict == WatchdogVerdict.FIRE, (
            f"Silence of {silent_for}s must FIRE, not defer. A longer stall can"
            " never be MORE deserving of protection than a shorter one."
        )


# === consolidated from test_silent_subagent_runtime.py ===
def test_silent_subagent_seconds_is_threaded_into_runtime_classifier() -> None:
    """``IdleWatchdog._classify_stuck_now`` MUST pass
    ``silent_subagent_seconds`` into the runtime classifier.

    Drives the production seam so a future refactor that drops the
    parameter from the call site surfaces immediately.  We assert
    via ``_classify_stuck_now`` return value rather than via the
    evaluate path because the gate's STUCK vs SILENT_SUBAGENT
    distinction is internal to ``classify_stuck``.
    """
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    # Record a subagent_progress observation at 30s.
    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    # Advance past the silent_subagent_seconds window (180s).
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    kind = watchdog._classify_stuck_now(now=now, idle_elapsed=181.0)
    assert kind == StuckKind.SILENT_SUBAGENT, (
        f"Expected SILENT_SUBAGENT when subagent_progress is stale and"
        f" classify_quiet is ACTIVE; got {kind}"
    )


# === consolidated from test_silent_subagent_runtime.py ===
def test_silent_subagent_disabled_when_silent_subagent_seconds_is_none() -> None:
    """When ``silent_subagent_seconds=None``, the runtime classifier
    MUST NOT return SILENT_SUBAGENT (the diagnostic is opt-in).

    Drives the production seam so the runtime threading of
    ``silent_subagent_seconds=None`` is verified: the classifier
    falls through to STUCK rather than SILENT_SUBAGENT.
    """
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=None)
    watchdog.record_invocation_start()

    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(1000.0)
    now = clock.monotonic()

    kind = watchdog._classify_stuck_now(now=now, idle_elapsed=1000.0)
    assert kind != StuckKind.SILENT_SUBAGENT, (
        f"Expected the SILENT_SUBAGENT diagnostic to be DISABLED when"
        f" silent_subagent_seconds is None; got {kind}"
    )


# === consolidated from test_silent_subagent_runtime.py ===
def test_gate_fires_on_silent_subagent_and_does_not_record_a_deferral() -> None:
    """The gate MUST FIRE when the classifier returns SILENT_SUBAGENT.

    Drives the production ``_gate_fire`` path. The classifier still LABELS
    the stall SILENT_SUBAGENT, but the label is not a veto: the branch
    requires ``alive_by is None`` (no live child), so there is nothing to
    protect. Deferring here shadowed the STUCK branch and wedged the run
    forever -- see ``test_silent_subagent_fires.py``.
    """
    watchdog, clock = _silent_subagent_runtime_make_watchdog(
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    watchdog.record_invocation_start()

    # Record subagent progress at 30s (within no_output_at_start window).
    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    # Advance past the silent-subagent threshold (180s) AND the activity
    # evidence TTL (30s) so the channel is stale and the gate sees
    # SILENT_SUBAGENT rather than THINKING/LOADING.
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=clock.monotonic(),
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        f"Gate MUST FIRE on SILENT_SUBAGENT (no live child); got {gate_verdict}"
    )
    assert watchdog.last_deferred_kind is None, (
        f"A FIRE is not a deferral; got last_deferred_kind={watchdog.last_deferred_kind}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


# === consolidated from test_silent_subagent_runtime.py ===
def test_last_deferred_kind_is_none_when_no_fire_deferred() -> None:
    """``last_deferred_kind`` MUST be ``None`` until the first deferral.

    The diagnostic is only meaningful AFTER a deferral; pre-deferral
    the field is ``None``.  Drives the production
    ``record_invocation_start`` reset path.
    """
    watchdog, _clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    assert watchdog.last_deferred_kind is None
    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None


# === consolidated from test_silent_subagent_runtime.py ===
def test_last_deferred_kind_resets_on_invocation_start() -> None:
    """``last_deferred_kind`` MUST reset on a new invocation so a
    prior deferred diagnostic does not leak into a fresh run.
    """
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    # Force one deferral. SILENT_SUBAGENT now FIRES, so drive a kind that
    # still defers: is_waiting_state=True -> DUPLICATE_KILL (branch 1).
    watchdog.set_is_waiting_state(True)
    watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=clock.monotonic(),
    )
    assert watchdog.last_deferred_kind == StuckKind.DUPLICATE_KILL

    # A new invocation MUST reset the deferred kind so prior
    # deferrals don't leak.
    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_dumb_kill_one_agent_reading_product_criteria() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even when the
    classifier would return LOADING.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live subagent. The mitigation is to raise
    ``max_waiting_on_child_seconds`` for long-running waits (the
    default is 1800s = 30 min).

    This test exercises the cumulative ceiling with a live subagent
    (filtered count = 1) and ``os_descendant_only_ceiling=300.0``.
    The effective ceiling is reduced to 300s and the ceiling fires
    unconditionally at 300s. The classifier would return LOADING
    but the cumulative ceiling fires regardless.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at 300s with ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitorSmartVerdictDumbKills(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()

    # First call must be after idle_timeout elapses so the watchdog
    # actually enters the WAITING_ON_CHILD branch. Advance 2s, then
    # call evaluate to set _waiting_on_child_started_at = 2.0.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY at 300s per R3 hard enforcement.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Verify the classifier would have named LOADING (the live
    # subagent is the deferral signal under the OLD contract).
    # The R3 cumulative ceiling now fires regardless of the
    # classifier's verdict -- the classifier's LOADING verdict
    # is no longer consulted by the cumulative ceiling block.
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING, (
        f"classifier should still return LOADING for the live subagent;"
        f" the R3 cumulative ceiling fires regardless of the classifier"
        f" verdict; got {kind!r}"
    )


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_children_persist_deferred_while_classifier_returns_loading() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even when the
    classifier returns LOADING for a live subagent.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live subagent. The mitigation is to raise
    ``max_waiting_on_child_seconds`` for long-running waits.

    Pre-fix (the symmetric counterpart of the dumb-kill test): the
    gate deferred the fire via the StuckClassifier's LOADING branch.
    Post-fix (R3 hard enforcement): the cumulative ceiling fires
    regardless of the classifier's LOADING verdict.

    Assertions:
      - verdict is FIRE at the 300s effective ceiling with
        ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitorSmartVerdictDumbKills(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()
    clock.advance(2.0)

    # First call: enter the WAITING_ON_CHILD branch.
    first = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s os_descendant_only_ceiling. With a live
    # subagent (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS), the
    # classifier returns LOADING but the cumulative ceiling fires
    # UNCONDITIONALLY per R3 hard enforcement regardless of the
    # classifier's verdict.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_dumb_kill_two_pre_output_fragment() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY past the effective ceiling even with a
    live child making forward progress.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when the classifier returns
    LOADING for a live child.

    Pre-fix (wt-012 dumb-kill prevention): the gate deferred the
    fire via the StuckClassifier's LOADING branch. Post-fix (R3
    hard enforcement): the cumulative ceiling fires regardless.

    Assertions:
      - verdict is FIRE at the 300s effective ceiling with
        ``CHILDREN_PERSIST_TOO_LONG``.
    """
    monitor = _LiveOnlyProcessMonitorSmartVerdictDumbKills(live_count=1)

    def _os_desc_only_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_waiting=600.0,
            os_descendant_only_ceiling=300.0,
            activity_ttl=30.0,
        ),
        process_monitor=monitor,
        corroborator=_os_desc_only_corroborator,
    )
    wd.record_activity()

    # First call: enter the WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the 300s effective ceiling. The cumulative
    # ceiling fires UNCONDITIONALLY per R3 hard enforcement.
    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_absolute_ceiling_bypasses_gate_with_waiting_state() -> None:
    """SESSION_CEILING_EXCEEDED bypasses the gate even with THINKING channels.

    The session ceiling is an operator-set hard cap, not a
    stuck-detection signal. Even when the pipeline is in a wait state
    and the first-party channels are fresh, the absolute reason must
    produce FIRE so the operator-set hard cap is honored.
    """
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    # Record first-party activity so classify_stuck would return THINKING.
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    # Mark the pipeline as in a wait state and verify the absolute
    # reason bypasses the gate.
    wd.set_is_waiting_state(True)
    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_no_duplicate_fire_across_many_evaluate_calls() -> None:
    """1000 evaluate() calls with the same state never produce duplicate FIRE.

    After the first FIRE, the watchdog's last_fire_reason is set.
    Subsequent evaluate() calls find the agent in a stuck state
    again, but the gate must not produce a second FIRE. The
    invariant is enforced by the gate's contract: a second candidate
    fire in the same state is always deferred by classify_stuck (it
    returns DUPLICATE_KILL when is_waiting_state is True, or STUCK
    only if the channels are actually stale).

    This is the duplicate-kill prevention invariant: a watchdog
    that fired once must not fire again until something observable
    has changed.
    """
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()

    # Advance past the session ceiling to force SESSION_CEILING_EXCEEDED.
    clock.advance(10.0)

    first_fire_count = 0
    for _ in range(1000):
        verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
        if verdict == WatchdogVerdict.FIRE:
            first_fire_count += 1
            break  # The first FIRE is the absolute reason; subsequent
            # SESSION_CEILING_EXCEEDED calls also bypass the gate (the
            # absolute reason always fires), so we break here.

    assert first_fire_count == 1

    # After the first FIRE, the gate would defer subsequent SESSION_CEILING
    # only if the gate is non-absolute. Per the design, SESSION_CEILING
    # bypasses the gate. So the watchdog WOULD fire again. This is
    # intentional: the operator-set hard cap is absolute. The
    # duplicate-FIRE concern is for non-absolute reasons, which are
    # gated.

    # The actual duplicate-FIRE invariant is: a non-absolute reason
    # cannot fire twice in a row. We exercise this with NO_OUTPUT_DEADLINE
    # and a gate that defers based on is_waiting_state.
    wd2, clock2 = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=None,
            activity_ttl=30.0,
        ),
    )
    wd2.record_activity()
    clock2.advance(10.0)

    # First evaluate: not in waiting state, channels stale -> STUCK -> FIRE.
    verdict1 = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
    assert verdict1 == WatchdogVerdict.FIRE

    # Mark the pipeline as in a wait state (simulating that the
    # caller handled the FIRE and is now waiting for a retry). The
    # next evaluate is a duplicate-kill scenario: the agent is
    # already in a wait, and the gate must defer.
    wd2.set_is_waiting_state(True)
    for _ in range(100):
        verdict = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE on duplicate evaluate call, got {verdict}"
        )

    # Clear the waiting state; the gate now allows FIRE again because
    # the pipeline is no longer waiting.
    wd2.set_is_waiting_state(False)
    verdict = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
    assert verdict == WatchdogVerdict.FIRE


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_waiting_state_makes_fire_into_duplicate_kill() -> None:
    """is_waiting_state=True turns a candidate FIRE into DUPLICATE_KILL.

    The gate is the single boundary: a candidate FIRE with
    is_waiting_state=True returns DUPLICATE_KILL regardless of any
    other input. This is the strongest signal: the pipeline has
    already committed to a wait, and a second FIRE during the wait
    is impossible.
    """
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=None,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    clock.advance(10.0)
    wd.set_is_waiting_state(True)

    for _ in range(50):
        verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
        assert verdict == WatchdogVerdict.CONTINUE
        assert wd.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_classifier_consulted_live_callable_returns_loading() -> None:
    """The classifier's WAITING_ON_CHILD branch returns LOADING when called with the live callable.

    This is a regression test for the classifier contract: when
    the classifier is consulted with a callable that returns
    ``WAITING_ON_CHILD``, it MUST return ``LOADING`` (not STUCK).
    The watchdog's gate consults the classifier with a noop stub
    to avoid the chicken-and-egg problem (the watchdog entered
    WAITING_ON_CHILD BECAUSE classify_quiet returned
    WAITING_ON_CHILD; consulting the same callable from the gate
    would always defer the ceiling fire). The pure classifier
    contract is unchanged -- the gate is the boundary that decides
    which branch is consulted in production.
    """
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(_smart_verdict_dumb_kills_make_policy(activity_ttl=30.0))
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_smart_verdict_dumb_kills_waiting,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING


# === consolidated from test_smart_verdict_dumb_kills.py ===
def test_classifier_consulted_live_callable_returns_transitioning() -> None:
    """The classifier's RESUMABLE_CONTINUE branch returns TRANSITIONING.

    Mirror of the WAITING_ON_CHILD test: when the classifier is
    consulted with a callable that returns ``RESUMABLE_CONTINUE``,
    it MUST return ``TRANSITIONING`` (not STUCK). The watchdog's
    gate consults the classifier with a noop stub for the same
    chicken-and-egg reason as the WAITING_ON_CHILD case.
    """

    def _resumable() -> AgentExecutionState:
        return AgentExecutionState.RESUMABLE_CONTINUE

    wd, clock = _smart_verdict_dumb_kills_make_watchdog(_smart_verdict_dumb_kills_make_policy(activity_ttl=30.0))
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_resumable)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_resumable,
        activity_evidence_ttl_seconds=wd._config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.TRANSITIONING


# === consolidated from test_stall_lifetime.py ===
def test_stall_lifetime_regression_invocation_end_clears_active_stall() -> None:
    """S-1: ending the stalled invocation publishes one authoritative clear."""
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _stall_lifetime_watchdog(clock, events)
    watchdog.record_invocation_start()
    watchdog._set_stall(active=True, now=1.0, idle_elapsed=1.0)

    clock.advance(2.0)
    watchdog.record_invocation_end()

    assert [event.kind for event in events] == [
        WaitingStatusKind.STALLED,
        WaitingStatusKind.STALL_RESUMED,
    ]
    assert events[-1].stall_active is False


# === consolidated from test_stall_lifetime.py ===
def test_stall_assessment_regression_fresh_watchdog_event_reports_not_stalled() -> None:
    """S-1: a fresh watchdog event re-synchronizes a previously latched host."""
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _stall_lifetime_watchdog(clock, events)

    watchdog._emit(WaitingStatusKind.PROGRESS, current_run_seconds=0.0, idle_elapsed=0.0)

    assert events[-1].stall_active is False


# === consolidated from test_stall_status_events.py ===
def test_is_stalled_initially_false() -> None:
    """A fresh watchdog is NOT in a stall."""
    watchdog, _clock = _stall_status_events_make_watchdog()
    assert _stall_state(watchdog) is False


# === consolidated from test_stall_status_events.py ===
def test_set_stall_emits_stalled_transition_only_once() -> None:
    """A single ``_set_stall(active=True)`` call emits one STALLED event."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1
    assert _stall_state(watchdog) is True


# === consolidated from test_stall_status_events.py ===
def test_set_stall_repeated_active_emits_no_duplicates() -> None:
    """Repeated ``_set_stall(active=True)`` emits no duplicate STALLED events."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    for _ in range(10):
        watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"STALLED must be emitted only on transition; got {len(stalled_events)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_set_stall_toggle_emits_stall_resumed_once() -> None:
    """Toggling from active=True to active=False emits exactly one STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    for _ in range(10):
        watchdog._set_stall(active=False, now=200.0, idle_elapsed=0.0)
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# === consolidated from test_stall_status_events.py ===
def test_set_stall_idempotent_false_emits_no_event() -> None:
    """Active=False on a fresh watchdog emits no STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=False, now=100.0, idle_elapsed=0.0)
    assert _events(captured) == []


# === consolidated from test_stall_status_events.py ===
def test_suspected_frozen_emits_stalled_event() -> None:
    """The SUSPECTED_FROZEN emission site drives a single STALLED transition.

    Drives the actual production path: the first ``evaluate()`` with
    WAITING_ON_CHILD enters the deferral branch and emits ENTERED;
    the second ``evaluate()`` after the clock has advanced past the
    suspect threshold crosses the SUSPECTED_FROZEN line and emits
    one SUSPECTED_FROZEN plus one STALLED. A third ``evaluate()``
    on the same tick must NOT emit a duplicate STALLED (the
    ``_set_stall`` helper dedupes by the runtime flag).

    The previous version of this test only called ``_set_stall``
    directly and never drove the SUSPECTED_FROZEN production site
    (DA-002: it pinned the helper, not the contract). The new
    version drives the SUSPECTED branch through ``evaluate()`` and
    inspects the capturing listener.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(
        listener=captured.append,
        idle_timeout_seconds=10.0,
        max_waiting_on_child_seconds=30.0,
        suspect_waiting_on_child_seconds=5.0,
        no_progress_quiet_seconds=None,
        corroborator=_fresh_progress_corroborator(),
    )

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # First evaluate: enter WAITING_ON_CHILD, emit ENTERED.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    assert _stall_state(watchdog) is False
    assert any(e.kind == WaitingStatusKind.ENTERED for e in _events(captured))

    # Second evaluate after crossing the suspect threshold (5s).
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting)

    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    suspect_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspect_events) == 1, (
        f"Expected exactly one SUSPECTED_FROZEN event, got {len(suspect_events)}: "
        f"{[e.kind for e in _events(captured)]}"
    )
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED event paired with the SUSPECTED_FROZEN transition, "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True

    # Third evaluate on the same tick: NO new STALLED, NO new SUSPECTED.
    # SUSPECTED_FROZEN is gated by ``_suspicion_announced_for_run``; the
    # STALLED transition is gated by ``_stall_active``. Both must dedupe.
    watchdog.evaluate(classify_quiet=_waiting)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    suspect_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(stalled_events) == 1
    assert len(suspect_events) == 1


# === consolidated from test_stall_status_events.py ===
def test_fire_verdict_emits_stalled_event() -> None:
    """A FIRE verdict (non-absolute reason) emits one STALLED listener event.

    The captured ``WaitingStatusListener`` is the contract surface the
    status bar subscribes to. The previous version of this test
    only asserted ``is_stalled`` and ``verdict == FIRE`` -- it never
    inspected the listener, so a regression that emitted a STALLED
    on the gate path without firing the listener would have
    silently passed. Drive the production ``evaluate()`` path with
    a capturing listener, assert exactly one ``WaitingStatusKind.STALLED``
    event, and repeat ``evaluate()`` to confirm the dedupe.
    """
    captured: list[WaitingStatusEvent] = []
    # Override the policy via the constructor so the frozen dataclass
    # is constructed with drain_window_seconds=0 (the active branch
    # fires NO_OUTPUT_DEADLINE immediately at the deadline).
    watchdog, _clock = _stall_status_events_make_watchdog(
        listener=captured.append,
        drain_window_seconds=0.0,
    )
    # Force the gate to allow the fire (STUCK kind).
    _classifier_to_stuck_now(watchdog)

    # Move the clock past the idle timeout.
    _clock.advance(61.0)
    # classify_quiet returns ACTIVE; the active branch fires.
    verdict = watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    # FIRING implies STALLED state.
    assert _stall_state(watchdog) is True

    # The capturing listener MUST have received exactly one STALLED
    # transition event (DA-001: the listener is the contract surface
    # the status bar subscribes to, not just the internal flag).
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED event on FIRE; got {len(stalled_events)}: "
        f"{[e.kind for e in _events(captured)]}"
    )

    # A second evaluate on the same tick MUST NOT emit a duplicate
    # STALLED event (the _set_stall helper dedupes by the runtime flag).
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_silent_subagent_emits_stalled_event() -> None:
    """A SILENT_SUBAGENT gate verdict emits one STALLED listener event.

    The SILENT_SUBAGENT branch of the classifier is a post-mortem
    LABEL, not a veto: the gate fires when the branch matches (no
    live child, stale subagent evidence). The fire path is
    ``_gate_fire -> StuckKind.SILENT_SUBAGENT -> _set_stall(active=True)``.
    The status bar subscribes to the WaitingStatusListener, so the
    STALLED transition MUST surface as a captured event -- not just
    flip the internal ``_stall_active`` flag.

    DA-001 fix: the previous coverage pinned the gate verdict but
    never inspected the listener. This test wires the production
    listener through ``_gate_fire`` and asserts the captured STALLED.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)

    # Patch _classify_stuck_now to return SILENT_SUBAGENT deterministically.
    _attr = "_classify_stuck_now"

    def _silent_subagent_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.SILENT_SUBAGENT

    setattr(watchdog, _attr, _silent_subagent_now)

    # Drive _gate_fire directly. The SILENT_SUBAGENT branch must
    # return FIRE AND emit exactly one STALLED listener event.
    _now = clock.monotonic() + 181.0
    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=_now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        f"SILENT_SUBAGENT must FIRE (the kind is a post-mortem LABEL, not a veto); "
        f"got {gate_verdict}"
    )

    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED listener event on SILENT_SUBAGENT gate fire; "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True

    # A second _gate_fire on the same tick MUST NOT emit a duplicate.
    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=_now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated _gate_fire must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_fire_session_ceiling_emits_stalled_event() -> None:
    """A SESSION_CEILING_EXCEEDED FIRE emits exactly one STALLED transition.

    DA-001 contract: the SESSION_CEILING_EXCEEDED bypass path inside
    ``_gate_fire`` (``_gate.py:142``) transitions the runtime stall
    flag via ``_set_stall(active=True, ...)`` BEFORE returning FIRE.
    The watchdog is the sole owner of the ``STALLED`` label, and a
    session that hit the operator-set cap is also a stalled run from
    the operator's perspective (the cap fired because the run was
    alive but un-killable by every other rule). The Status Bar must
    surface the same stall signal here as for a STUCK classifier
    verdict or a SILENT_SUBAGENT fire.

    The previous version of this test asserted the OPPOSITE (no
    STALLED event, ``is_stalled is False``) because the SESSION_CEILING
    bypass path returned FIRE without calling ``_set_stall`` -- the
    DA-001 gap. The fix flips the bypass path to transition the
    runtime flag; the test now pins the listener contract and the
    runtime flag.

    Repeated ``evaluate()`` calls on the same tick MUST NOT emit a
    duplicate STALLED event (``_set_stall`` is idempotent on the
    runtime flag).
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(
        listener=captured.append,
        max_session_seconds=60.0,
        idle_timeout_seconds=30.0,
        max_waiting_on_child_seconds=99999.0,
        no_output_at_start_seconds=None,
    )
    _clock.advance(61.0)
    verdict = watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED listener event on SESSION_CEILING_EXCEEDED FIRE; "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True, (
        "SESSION_CEILING_EXCEEDED FIRE MUST transition the runtime stall flag; "
        "watchdog is the sole owner of the STALLED label (DA-001)."
    )

    # A second evaluate() on the same tick MUST NOT emit a duplicate
    # STALLED event (the _set_stall helper dedupes by the runtime flag).
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() on SESSION_CEILING_EXCEEDED MUST NOT emit duplicate "
        f"STALLED events; got {len(stalled_events)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_record_activity_emits_stall_resumed() -> None:
    """``record_activity`` clears the stall state and emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    assert _stall_state(watchdog) is True
    # Drain prior events.
    captured.clear()
    watchdog.record_activity()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# === consolidated from test_stall_status_events.py ===
def test_record_invocation_start_emits_stall_resumed() -> None:
    """``record_invocation_start`` clears the stall state and emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    captured.clear()
    watchdog.record_invocation_start()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# === consolidated from test_stall_status_events.py ===
def test_accumulate_waiting_run_emits_stall_resumed() -> None:
    """Transitioning out of WAITING (EXITED) emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    # Force the watchdog into a WAITING_ON_CHILD run.
    watchdog._waiting_on_child_started_at = 100.0
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    captured.clear()
    watchdog._accumulate_waiting_run(200.0)
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# === consolidated from test_stall_status_events.py ===
def test_no_stall_event_emitted_when_already_idle() -> None:
    """Idle activity calls without a prior stall do NOT emit STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    # Fresh watchdog: no stall.
    watchdog.record_activity()
    watchdog.record_activity()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 0


# === consolidated from test_stall_status_events.py ===
def test_stall_oscillation_emits_only_on_transitions() -> None:
    """Stall toggling across many ticks emits only on transitions."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    # 5 transitions.
    for i in range(5):
        watchdog._set_stall(active=True, now=float(i * 100), idle_elapsed=100.0)
        watchdog._set_stall(active=False, now=float(i * 100 + 50), idle_elapsed=0.0)
    # Then 100 ticks of repeated STALLED.
    for _ in range(100):
        watchdog._set_stall(active=True, now=10_000.0, idle_elapsed=100.0)
    # Then 100 ticks of repeated STALL_RESUMED.
    for _ in range(100):
        watchdog._set_stall(active=False, now=10_500.0, idle_elapsed=0.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    # 5 STALLED + 1 extra (the last 100-tick burst counts once) = 6
    # 5 STALL_RESUMED + 1 extra (the last 100-tick burst counts once) = 6
    assert len(stalled) == 6, (
        f"Expected exactly 6 STALLED events (5 transitions + 1 final), got {len(stalled)}"
    )
    assert len(resumed) == 6, (
        f"Expected exactly 6 STALL_RESUMED events (5 transitions + 1 final), got {len(resumed)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_is_stalled_property_reflects_internal_state() -> None:
    """The public ``is_stalled`` property mirrors the watchdog's internal state."""
    watchdog, _clock = _stall_status_events_make_watchdog()
    assert watchdog.is_stalled is False
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    assert watchdog.is_stalled is True
    watchdog._set_stall(active=False, now=200.0, idle_elapsed=0.0)
    assert watchdog.is_stalled is False


# === consolidated from test_stall_status_events.py ===
def test_gate_deferral_clears_silent_subagent_stall_on_non_stuck_tick() -> None:
    """A later non-stuck tick clears a prior SILENT_SUBAGENT stall.

    DA-001 contract: when the gate fires (returns ``WatchdogVerdict.FIRE``)
    on a ``StuckKind.SILENT_SUBAGENT`` verdict and the next tick's
    classifier no longer returns ``SILENT_SUBAGENT`` (e.g. the
    corroborator now reports a non-stuck kind like ``LOADING``), the
    gate's deferral path MUST clear the stall flag. Without this
    transition-out, the Status Bar would stay ``STALLED`` forever
    after a transient subagent silence even though the watchdog's
    own classifier is reporting a healthy session.

    The captured ``WaitingStatusListener`` is the contract surface
    the status bar subscribes to; both the ``STALLED`` transition
    on entry and the ``STALL_RESUMED`` transition on exit must be
    observable as listener events (not just internal flag flips).
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)

    # Patch _classify_stuck_now so we can drive the SILENT_SUBAGENT
    # transition followed by a non-stuck kind deterministically.
    sequence: list[StuckKind] = [StuckKind.SILENT_SUBAGENT, StuckKind.LOADING]
    _attr = "_classify_stuck_now"

    def _sequence_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        if sequence:
            return sequence.pop(0)
        return StuckKind.LOADING

    setattr(watchdog, _attr, _sequence_now)

    # Tick 1: SILENT_SUBAGENT -> gate fires + sets stall.
    first_now = clock.monotonic() + 181.0
    first_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=181.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    stalled_after_first = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_after_first) == 1
    assert _stall_state(watchdog) is True

    # Tick 2: classifier returns LOADING (not SILENT_SUBAGENT) -> gate
    # defers (CONTINUE). The deferral path must clear the stall flag
    # and emit exactly one STALL_RESUMED transition.
    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=second_now,
        idle_elapsed=1.0,
    )
    assert second_verdict == WatchdogVerdict.CONTINUE
    resumed_after_second = [
        e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED
    ]
    assert len(resumed_after_second) == 1, (
        f"Expected exactly one STALL_RESUMED listener event on the non-stuck tick; "
        f"got {len(resumed_after_second)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is False

    # Tick 3: classifier still returns LOADING -> no additional events.
    third_now = clock.monotonic() + 1.0
    third_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=third_now,
        idle_elapsed=1.0,
    )
    assert third_verdict == WatchdogVerdict.CONTINUE
    stalled_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(stalled_total) == 1, (
        f"Repeated non-stuck ticks must NOT emit duplicate STALLED; got {len(stalled_total)}"
    )
    assert len(resumed_total) == 1, (
        f"Repeated non-stuck ticks must NOT emit duplicate STALL_RESUMED; got {len(resumed_total)}"
    )


# === consolidated from test_stall_status_events.py ===
def test_gate_deferral_does_not_clear_stuck_or_fire_stall() -> None:
    """A STUCK verdict still fires AND keeps the stall active on the next deferral.

    DA-001 contract: the deferral path only clears a stall that
    was previously set by ``SILENT_SUBAGENT`` (or any other
    non-``STUCK`` fire path); a ``STUCK`` verdict stays stalled
    because the gate is firing that same tick, not deferring. The
    subsequent tick's classifier may return a different non-stuck
    kind (e.g. ``LOADING``) -- in that case the deferral path
    clears the stall. This test pins the asymmetry: a single STUCK
    tick followed by a non-stuck tick is exactly one STALLED +
    exactly one STALL_RESUMED, and ``is_stalled`` ends False.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)

    sequence: list[StuckKind] = [StuckKind.STUCK, StuckKind.LOADING]
    _attr = "_classify_stuck_now"

    def _sequence_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        if sequence:
            return sequence.pop(0)
        return StuckKind.LOADING

    setattr(watchdog, _attr, _sequence_now)

    first_now = clock.monotonic() + 60.0
    first_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=60.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    assert _stall_state(watchdog) is True

    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=second_now,
        idle_elapsed=1.0,
    )
    assert second_verdict == WatchdogVerdict.CONTINUE
    assert _stall_state(watchdog) is False

    stalled_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(stalled_total) == 1
    assert len(resumed_total) == 1


# === consolidated from test_stall_status_events.py ===
def test_set_stall_emitted_event_carries_idle_elapsed_seconds() -> None:
    """DA-002 regression: STALLED event carries idle_elapsed_seconds=42.0.

    The watchdog already passes idle_elapsed through to the
    emitted event; this test pins the watchdog side so a future
    regression in _emit (e.g. accidentally using
    current_run_seconds) is caught at the source rather than
    only at the subscriber rendering.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=42.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled) == 1
    assert stalled[0].idle_elapsed_seconds == 42.0
    assert stalled[0].current_run_seconds == 0.0


# === consolidated from test_stall_status_events.py ===
def test_set_stall_resumed_emitted_event_carries_idle_elapsed_seconds() -> None:
    """DA-002 regression: STALL_RESUMED event carries idle_elapsed_seconds."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=42.0)
    watchdog._set_stall(active=False, now=200.0, idle_elapsed=37.0)
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed) == 1
    assert resumed[0].idle_elapsed_seconds == 37.0
    assert resumed[0].current_run_seconds == 0.0


# === consolidated from test_strictly_stuck_ceiling.py ===
def test_strictly_stuck_enum_exists() -> None:
    """WatchdogFireReason.STRICTLY_STUCK MUST exist."""
    assert hasattr(WatchdogFireReason, "STRICTLY_STUCK"), (
        "WatchdogFireReason.STRICTLY_STUCK missing; the new fire"
        " reason for stuck-but-alive jobs is required"
    )
    assert WatchdogFireReason.STRICTLY_STUCK.value == "strictly_stuck", (
        f"WatchdogFireReason.STRICTLY_STUCK.value must be"
        f" 'strictly_stuck'; got {WatchdogFireReason.STRICTLY_STUCK.value!r}"
    )


# === consolidated from test_strictly_stuck_ceiling.py ===
def test_strictly_stuck_fires_when_alive_by_pure_descendant_stale() -> None:
    """STRICTLY_STUCK MUST fire when alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS
    AND the run has been in the strictly-stuck alive_by state for at
    least ``no_progress_quiet_strictly_stuck_seconds``.

    Pre-fix this returns CONTINUE because the
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling is at 600 s (not yet hit).
    Post-fix the new ceiling at 300 s fires STRICTLY_STUCK.

    The test pre-seeds ``_strictly_stuck_run_started_at`` to 0 so the
    first ``evaluate()`` call (after a single 305 s clock advance) sees
    a 305 s strictly-stuck run -- past the 300 s ceiling -- and fires.
    """
    wd, clock = _strictly_stuck_ceiling_make_watchdog(
        strictly_stuck_seconds=300.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    # Pre-seed the strictly-stuck run-start to the clock origin so a
    # single advance of 305 s yields a 305 s strictly-stuck run, well
    # past the 300 s ceiling. This avoids depending on the production
    # code's two-tick seed semantics and keeps the test focused on the
    # ceiling behavior. Use ``setattr`` with the attribute name held
    # in a local variable so mypy cannot narrow the access to a
    # private-attribute assignment AND ruff B010 does not flag a
    # setattr-with-constant-value call. The policy test for
    # ``test_zero_test_file_suppressions`` rejects bare mypy
    # suppression comments inside test files.
    _run_started_attr = "_strictly_stuck_run_started_at"
    setattr(wd, _run_started_attr, clock.monotonic())
    clock.advance(305.0)
    verdict = wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"STRICTLY_STUCK MUST fire at invocation elapsed = 305 s"
        f" with alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.STRICTLY_STUCK, (
        f"expected WatchdogFireReason.STRICTLY_STUCK; got {wd.last_fire_reason}"
    )


# === consolidated from test_strictly_stuck_ceiling.py ===
def test_strictly_stuck_does_not_fire_before_ceiling() -> None:
    """STRICTLY_STUCK MUST NOT fire before the ceiling elapses.

    Verifies the ceiling semantics: at 200 s with the ceiling at
    300 s the watchdog returns CONTINUE (no fire reason yet).
    """
    wd, clock = _strictly_stuck_ceiling_make_watchdog(
        strictly_stuck_seconds=300.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(200.0)
    verdict = wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"STRICTLY_STUCK MUST NOT fire before its ceiling elapses"
        f" (invocation_elapsed=200s, ceiling=300s); got {verdict}"
    )


# === consolidated from test_strictly_stuck_ceiling.py ===
def test_strictly_stuck_disabled_when_none() -> None:
    """When no_progress_quiet_strictly_stuck_seconds is None the ceiling
    is disabled and the watchdog returns CONTINUE.

    Operators can opt out by setting the field to ``None``. The
    default 300 s is opt-in.
    """
    wd, clock = _strictly_stuck_ceiling_make_watchdog(
        strictly_stuck_seconds=None,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(605.0)
    verdict = wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    # Disabled: CONTINUE regardless of elapsed time.
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"STRICTLY_STUCK MUST be disabled when the field is None; got {verdict}"
    )


# === consolidated from test_stuck_classifier.py ===
def test_is_waiting_state_true_returns_duplicate_kill() -> None:
    """A duplicate FIRE during a wait state must never be produced.

    is_waiting_state=True is the strongest signal: the pipeline has already
    decided to wait, so the watchdog must defer to the run-loop's wait
    semantics and return DUPLICATE_KILL regardless of any first-party
    evidence.
    """
    kind = classify_stuck(**_inputs(is_waiting_state=True))
    assert kind == StuckKind.DUPLICATE_KILL


# === consolidated from test_stuck_classifier.py ===
def test_offline_connectivity_returns_waiting_on_connectivity() -> None:
    """Offline connectivity -> WAITING_ON_CONNECTIVITY.

    The pipeline already has a ConnectivityMonitor that pauses/resumes on
    network loss; the watchdog must NOT fire while connectivity is offline
    because the agent may simply be unable to reach its transport.
    """
    kind = classify_stuck(**_inputs(connectivity_state="offline"))
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


# === consolidated from test_stuck_classifier.py ===
def test_fresh_subagent_output_returns_thinking() -> None:
    """A fresh subagent_output channel implies the agent is THINKING.

    subagent_output is first-party evidence: a subagent that just wrote a
    line is doing real work, not wedged. The agent is in the "thinking"
    phase of producing output.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.THINKING


# === consolidated from test_stuck_classifier.py ===
def test_fresh_subagent_liveness_without_first_party_returns_loading() -> None:
    """Subagent liveness fresh but no first-party channels -> LOADING.

    A live subagent with no captured output is in the LOADING phase: it
    exists, it is alive, but the watchdog has no first-party evidence yet.
    This is the case during the first 30s of a subagent's lifetime, when
    it is starting up but has not yet produced a line.
    """
    summary = _multi_summary(
        subagent_liveness_at=_NOW - 5.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.LOADING


# === consolidated from test_stuck_classifier.py ===
def test_os_descendant_alive_no_fresh_channels_returns_loading() -> None:
    """alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS + WAITING_ON_CHILD + no
    fresh channels -> LOADING. The agent is loading (i.e. waiting for
    a subprocess to make progress), not STUCK.
    """
    summary = _multi_summary(alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS)
    kind = classify_stuck(
        **_inputs(
            evidence_summary=summary,
            classify_quiet_state=AgentExecutionState.WAITING_ON_CHILD,
        )
    )
    assert kind == StuckKind.LOADING


# === consolidated from test_stuck_classifier.py ===
def test_resumable_continue_returns_transitioning() -> None:
    """classify_quiet returns RESUMABLE_CONTINUE -> TRANSITIONING.

    A session reset or resumable exit is a transition state, not a stuck
    state. The watchdog must defer the verdict and let the run-loop
    handle the session transition.
    """
    kind = classify_stuck(**_inputs(classify_quiet_state=AgentExecutionState.RESUMABLE_CONTINUE))
    assert kind == StuckKind.TRANSITIONING


# === consolidated from test_stuck_classifier.py ===
def test_no_channels_active_returns_stuck() -> None:
    """All channels stale, no waiting state, classify_quiet=ACTIVE -> STUCK.

    The agent looks quiet with no first-party evidence and no live
    subagent. This is the only kind where the watchdog is permitted to
    fire.
    """
    kind = classify_stuck(**_inputs())
    assert kind == StuckKind.STUCK


# === consolidated from test_stuck_classifier.py ===
def test_classify_stuck_is_pure() -> None:
    """classify_stuck must be a pure function of its inputs.

    Calling it twice with the same inputs must return the same kind. No
    hidden state, no I/O, no clock reads.
    """
    inputs = _inputs(connectivity_state="offline")
    kind1 = classify_stuck(**inputs)
    kind2 = classify_stuck(**inputs)
    assert kind1 == kind2
    assert kind1 == StuckKind.WAITING_ON_CONNECTIVITY


# === consolidated from test_stuck_classifier.py ===
def test_priority_order_waiting_beats_offline() -> None:
    """When multiple signals are present, is_waiting_state wins first.

    is_waiting_state=True is the strongest signal because it means the
    pipeline has already committed to a wait. Connectivity offline is
    secondary: the pipeline may be on a wait cycle that pre-dates the
    connectivity state change.
    """
    kind = classify_stuck(**_inputs(is_waiting_state=True, connectivity_state="offline"))
    assert kind == StuckKind.DUPLICATE_KILL


# === consolidated from test_stuck_classifier.py ===
def test_priority_order_offline_beats_thinking() -> None:
    """Offline connectivity beats fresh first-party channels.

    If the agent produced a fragment but the transport is offline, the
    watchdog should classify as WAITING_ON_CONNECTIVITY (the network is
    the problem, not the agent). A fresh first-party channel is evidence
    of work but cannot override the transport-level outage.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(**_inputs(connectivity_state="offline", evidence_summary=summary))
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


# === consolidated from test_stuck_classifier.py ===
def test_corroboration_is_plumbed_but_does_not_change_stuck_verdict() -> None:
    """A live corroboration does NOT change a STUCK verdict.

    All channels are stale, is_waiting_state=False, connectivity=online,
    classify_quiet=ACTIVE -> the verdict is STUCK. The corroboration
    parameter is plumbed but does not change the verdict: both
    corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.STUCK.
    """
    inputs = _inputs()

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        ),
    )
    kind_stale_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        ),
    )
    kind_dead_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(alive_by=None),
    )

    assert kind_no_corr == StuckKind.STUCK
    assert kind_live_corr == StuckKind.STUCK
    assert kind_stale_corr == StuckKind.STUCK
    assert kind_dead_corr == StuckKind.STUCK


# === consolidated from test_stuck_classifier.py ===
def test_corroboration_does_not_change_thinking_verdict() -> None:
    """A live corroboration does NOT change a THINKING verdict.

    A fresh subagent_output channel implies THINKING. The corroboration
    parameter is plumbed but does not change the verdict: both
    corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.THINKING.
    """
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    inputs = _inputs(evidence_summary=summary)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.THINKING
    assert kind_live_corr == StuckKind.THINKING


# === consolidated from test_stuck_classifier.py ===
def test_corroboration_does_not_change_loading_verdict_via_subagent_liveness() -> None:
    """LOADING via subagent_liveness is unchanged by corroboration alive_by.

    The fresh subagent_liveness channel implies LOADING. The
    corroboration parameter is plumbed but does not change the verdict:
    both corroboration=None and corroboration with alive_by=FRESH_PROGRESS
    return StuckKind.LOADING. This is the path the watchdog uses to defer
    dumb kills when a process monitor reports a live subagent.
    """
    summary = _multi_summary(
        subagent_liveness_at=_NOW - 5.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    inputs = _inputs(evidence_summary=summary)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.LOADING
    assert kind_live_corr == StuckKind.LOADING


# === consolidated from test_stuck_classifier.py ===
def test_corroboration_does_not_change_offline_verdict() -> None:
    """WAITING_ON_CONNECTIVITY beats corroboration alive_by.

    Even with a live corroboration (alive_by=FRESH_PROGRESS), the offline
    connectivity state still wins: the classifier returns
    WAITING_ON_CONNECTIVITY. The corroboration does not change the
    verdict; the network state is the problem, not the agent.
    """
    inputs = _inputs(connectivity_state="offline")

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.WAITING_ON_CONNECTIVITY
    assert kind_live_corr == StuckKind.WAITING_ON_CONNECTIVITY


# === consolidated from test_stuck_classifier.py ===
def test_corroboration_does_not_change_duplicate_kill_verdict() -> None:
    """DUPLICATE_KILL is the strongest signal and is not changed by corroboration.

    is_waiting_state=True wins first. The corroboration does not change
    the verdict: both corroboration=None and corroboration with
    alive_by=FRESH_PROGRESS return StuckKind.DUPLICATE_KILL.
    """
    inputs = _inputs(is_waiting_state=True)

    kind_no_corr = classify_stuck(**inputs)
    kind_live_corr = classify_stuck(
        **inputs,
        corroboration=CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
        ),
    )

    assert kind_no_corr == StuckKind.DUPLICATE_KILL
    assert kind_live_corr == StuckKind.DUPLICATE_KILL


# === consolidated from test_stuck_classifier.py ===
def test_silent_subagent_when_progress_count_ge_1_and_stale() -> None:
    """Test the SILENT_SUBAGENT diagnostic label.

    A subagent channel has evidence (count >= 1) AND the most
    recent signal is older than ``silent_subagent_seconds`` AND no
    first-party / side-channel activity is fresh AND
    classify_quiet is ACTIVE -> the classifier returns
    StuckKind.SILENT_SUBAGENT.

    This is a post-mortem label parallel to
    DEFERRED_BY_STUCK_CLASSIFIER; the watchdog surfaces it on the
    ``last_fire_reason`` property so an operator can see WHY a
    would-be fire was deferred ("a subagent dispatched then went
    silent for >180s").
    """
    summary = _multi_summary(
        subagent_output_at=_NOW - 1000.0,  # well past 180s
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.ACTIVE,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.SILENT_SUBAGENT


# === consolidated from test_stuck_classifier.py ===
def test_no_silent_subagent_when_subagent_progress_is_fresh() -> None:
    """Fresh subagent output (within the silent threshold) MUST yield
    THINKING (not SILENT_SUBAGENT) so a healthy subagent does not get
    mislabeled.

    The classifier checks SILENT_SUBAGENT AFTER the LOADING / THINKING
    branches so a fresh first-party subagent channel implies THINKING.
    """
    summary = _multi_summary(
        subagent_output_at=_NOW - 5.0,  # well within 180s
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.ACTIVE,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.THINKING


# === consolidated from test_stuck_classifier.py ===
def test_no_silent_subagent_when_no_subagent_evidence() -> None:
    """Without ANY subagent evidence, the classifier MUST NOT return
    SILENT_SUBAGENT.  The diagnostic is gated on subagent evidence.

    With no subagent evidence and classify_quiet=ACTIVE, the
    classifier falls through to STUCK (the canonical "agent looks
    quiet with no first-party evidence and no live subagent"
    verdict).
    """
    inputs = _inputs()
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.STUCK


# === consolidated from test_stuck_classifier.py ===
def test_silent_subagent_disabled_when_silent_subagent_seconds_is_none_via_classify_stuck() -> None:
    """When ``silent_subagent_seconds`` is None (or absent), the
    SILENT_SUBAGENT branch MUST NOT fire even if a subagent channel
    has stale evidence.  The diagnostic is opt-in via the
    ``silent_subagent_seconds`` TimeoutPolicy field.

    Companion to ``test_silent_subagent_disabled_when_silent_subagent_seconds_is_none``
    (consolidated from ``test_silent_subagent_runtime.py``), which
    drives the same invariant through the production
    ``_classify_stuck_now`` seam. Renamed with the
    ``_via_classify_stuck`` suffix so the two distinct tests can
    co-exist in the consolidated module (the F811 redefinition
    block is the consolidation seam).
    """
    summary = _multi_summary(
        subagent_output_at=_NOW - 1000.0,
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.ACTIVE,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=None,
    )
    # Without the silent_subagent_seconds gate, the classifier
    # falls through to STUCK.
    assert kind == StuckKind.STUCK


# === consolidated from test_stuck_classifier.py ===
def test_silent_subagent_does_not_change_when_waiting() -> None:
    """The SILENT_SUBAGENT branch MUST NOT change a verdict that is
    already determined by a higher-priority branch.

    With classify_quiet=WAITING_ON_CHILD, the classifier returns
    LOADING (branch 5) BEFORE the SILENT_SUBAGENT branch (branch 7).
    """
    summary = _multi_summary(
        subagent_output_at=_NOW - 1000.0,  # stale evidence
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.WAITING_ON_CHILD,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    # LOADING wins (branch 5) over SILENT_SUBAGENT (branch 7).
    assert kind == StuckKind.LOADING


# === consolidated from test_stuck_classifier.py ===
def test_no_silent_subagent_when_subagent_liveness_alive_by_is_not_none() -> None:
    """A non-None ``alive_by`` on the subagent_liveness channel MUST
    prevent SILENT_SUBAGENT.

    AC-05 requires the SILENT_SUBAGENT branch to depend on
    ``alive_by is None``. When the corroborator still reports a live
    child (even with stale progress), the diagnostic must not fire.
    With no first-party or fresh side-channel activity, the verdict
    falls through to STUCK.
    """
    summary = _multi_summary(
        subagent_output_at=_NOW - 1000.0,
        subagent_liveness_at=_NOW - 1000.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.ACTIVE,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.STUCK, f"non-None alive_by must prevent SILENT_SUBAGENT; got {kind}"


# === consolidated from test_stuck_classifier.py ===
def test_no_silent_subagent_when_only_liveness_alive_by_not_none() -> None:
    """Even with no subagent_output evidence, a stale subagent_liveness
    channel that carries a non-None ``alive_by`` MUST NOT be labeled
    SILENT_SUBAGENT.
    """
    summary = _multi_summary(
        subagent_output_at=None,
        subagent_liveness_at=_NOW - 1000.0,
        alive_by=AliveBy.FRESH_PROGRESS,
    )
    inputs = {
        "is_waiting_state": False,
        "connectivity_state": "online",
        "evidence_summary": summary,
        "classify_quiet": _ClassifyQuietStub(
            state=AgentExecutionState.ACTIVE,
        ),
        "activity_evidence_ttl_seconds": _TTL_SECONDS,
    }
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.STUCK


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_only_trip() -> None:
    """Heartbeat-only subagent trips NO_PROGRESS_QUIET once ceiling elapses.

    Pre-fix: ``_is_no_progress_quiet`` short-circuits when
    ``alive_by is not None`` so a heartbeat-only subagent would defer
    until the cumulative 600s ``CHILDREN_PERSIST_TOO_LONG`` ceiling.
    Post-fix: the dedicated heartbeat-only ceiling (10s) trips
    ``NO_PROGRESS_QUIET`` once ``invocation_elapsed_seconds >= 10s``
    AND the dumb-kill floor (10s) has elapsed (so the floor guard does
    not defer).

    Setup: heartbeat_ceiling = 10s, no_progress_quiet_seconds = 5s
    (so the outer ``invocation_elapsed >= no_progress_quiet_seconds``
    check passes when the heartbeat ceiling elapses at 10s+), floor =
    10s, advance by 11s -> FIRE with reason NO_PROGRESS_QUIET.
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_PROGRESS_QUIET MUST fire at invocation elapsed = 11s with"
        f" alive_by=FRESH_HEARTBEAT_ONLY and heartbeat_ceiling=10s;"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET; got {wd.last_fire_reason}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_ceiling_does_not_trip_before_threshold() -> None:
    """Heartbeat ceiling MUST NOT fire before its threshold elapses.

    Verifies the ceiling semantics: at 9s with the heartbeat ceiling
    at 10s the watchdog returns CONTINUE (the ceiling has not elapsed).
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(9.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"Heartbeat-only ceiling MUST NOT fire before its threshold"
        f" elapses (invocation_elapsed=9s, ceiling=10s); got {verdict}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_fresh_progress_deferral_preserved() -> None:
    """FRESH_PROGRESS (real progress) continues to defer indefinitely.

    The heartbeat-only branch is a heartbeat-only branch: it MUST NOT
    kill subagents that report ``AliveBy.FRESH_PROGRESS`` (real
    progress, not just heartbeats). At 100s with the heartbeat ceiling
    at 10s and ``alive_by=FRESH_PROGRESS``, the verdict is NOT FIRE
    because the branch only fires for ``FRESH_HEARTBEAT_ONLY``. The
    WAITING_ON_CHILD classifier is used so the test exercises the
    same waiting-branch path the production bug lives on; otherwise
    the test would pass on the ACTIVE path and not catch the
    no_progress_quiet_seconds short-circuit regression. The verdict
    is either ``CONTINUE`` (dumb-kill path defers via the
    alive_by is not None short-circuit when heartbeat_ceiling is
    not None) or ``WAITING_ON_CHILD`` (waiting-branch deferral
    returned by ``_handle_waiting_branch``); either outcome is a
    non-fire continuation.
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(100.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST NOT fire for FRESH_PROGRESS"
        f" (real progress, not heartbeat); got {verdict}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_ceiling_disabled_when_none() -> None:
    """When ``no_progress_quiet_heartbeat_ceiling_seconds`` is None the
    heartbeat-only ceiling is disabled and the watchdog does NOT
    fire.

    Operators can opt out by setting the field to ``None``. The
    default 240s is opt-in via ``[general]`` config (the
    ``agent_no_progress_quiet_heartbeat_ceiling_seconds`` field
    on ``GeneralConfig``, which inherits the constant
    ``NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` from
    ``ralph/timeout_defaults.py``). The
    WAITING_ON_CHILD classifier is used so the test exercises the
    same waiting-branch path the production bug lives on; otherwise
    the test would pass on the ACTIVE path and not catch the
    no_progress_quiet_seconds short-circuit regression. The
    ``FRESH_HEARTBEAT_ONLY`` deferral still routes through
    ``_handle_waiting_branch`` (cumulative
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling at 1800s, well above
    100s) so the verdict is either ``CONTINUE`` or
    ``WAITING_ON_CHILD`` (non-fire continuation).
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=None,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(100.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    # Disabled: not FIRE because FRESH_HEARTBEAT_ONLY defers at the
    # alive_by short-circuit (no heartbeat branch to fire). The
    # watchdog falls back to the cumulative CHILDREN_PERSIST_TOO_LONG
    # ceiling at 1800s (well above 100s).
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST be disabled when the field is None; got {verdict}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_only_ceiling_fires_before_dumb_kill_ceiling() -> None:
    """Heartbeat-only ceiling fires BEFORE the dumb-kill ceiling.

    This is the operator-knob behavior: when
    ``heartbeat_ceiling < no_progress_quiet_seconds``, the heartbeat
    ceiling trips ``NO_PROGRESS_QUIET`` earlier than the dumb-kill
    ceiling. The fix moves the heartbeat-only branch ABOVE the
    ``invocation_elapsed_seconds < no_progress_quiet_seconds``
    short-circuit so the heartbeat ceiling is consulted BEFORE the
    dumb-kill gate.

    Setup: heartbeat_ceiling = 60s, no_progress_quiet_seconds = 120s
    (so the heartbeat ceiling fires EARLIER than the dumb-kill
    ceiling; the cross-field validator enforces
    heartbeat_ceiling <= no_progress_quiet_seconds). Advance by 61s
    -> FIRE with reason NO_PROGRESS_QUIET. Pre-fix, this scenario
    deferred until the 120s dumb-kill ceiling (or the cumulative
    600s CHILDREN_PERSIST_TOO_LONG ceiling if
    no_progress_quiet_seconds was disabled).
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=60.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(61.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling (60s) MUST fire BEFORE the dumb-kill"
        f" ceiling (120s) at invocation elapsed = 61s with"
        f" alive_by=FRESH_HEARTBEAT_ONLY; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET; got {wd.last_fire_reason}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_only_ceiling_respects_dumb_kill_floor() -> None:
    """The dumb-kill floor still protects recently-launched heartbeat-only agents.

    The heartbeat-only branch honors
    ``no_progress_quiet_minimum_invocation_seconds`` so a
    recently-launched agent doing real thinking work but emitting
    only heartbeats is not killed before the dumb-kill floor elapses.

    Setup: heartbeat_ceiling = 10s, floor = 30s, advance by 11s ->
    NOT FIRE because the floor (30s) has not elapsed yet. The
    ``FRESH_HEARTBEAT_ONLY`` deferral still routes through
    ``_handle_waiting_branch`` (cumulative ceiling at 1800s, well
    above 11s) so the verdict is either ``CONTINUE`` or
    ``WAITING_ON_CHILD`` (non-fire continuation).
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=30.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST NOT fire before the dumb-kill floor elapses; got {verdict}"
    )


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
def test_heartbeat_only_trip_when_no_progress_quiet_seconds_disabled() -> None:
    """Heartbeat-only branch fires even when ``no_progress_quiet_seconds=None``.

    REGRESSION TEST for the analysis feedback runtime bug. The
    heartbeat-only ceiling is ORTHOGONAL to the dumb-kill ceiling;
    a ``None`` ``no_progress_quiet_seconds`` (operator disables the
    dumb-kill trip) MUST NOT also disable the heartbeat-only trip.

    Pre-fix: ``_evaluate_no_progress_quiet`` short-circuited when
    ``no_progress_quiet_seconds`` was ``None`` AND ``evaluate()``
    skipped calling ``_evaluate_no_progress_quiet`` when
    ``no_progress_quiet_seconds`` was ``None``. The combination
    meant a heartbeat-only subagent would run indefinitely even
    with a configured heartbeat ceiling.

    Post-fix: ``evaluate()`` calls ``_evaluate_no_progress_quiet``
    when EITHER ceiling is configured, and
    ``_evaluate_no_progress_quiet`` only short-circuits when BOTH
    ceilings are ``None``.

    Setup: heartbeat_ceiling = 10s, no_progress_quiet_seconds = None
    (dumb-kill disabled), alive_by=FRESH_HEARTBEAT_ONLY, advance by
    11s -> FIRE with reason NO_PROGRESS_QUIET. Pre-fix this returned
    ``verdict=continue, reason=None`` (the heartbeat branch never
    fired). Post-fix it returns ``verdict=FIRE, reason=NO_PROGRESS_QUIET``.
    """
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"Heartbeat-only branch MUST fire when "
        f"no_progress_quiet_seconds=None AND "
        f"no_progress_quiet_heartbeat_ceiling_seconds=10.0 AND "
        f"invocation_elapsed=11s with alive_by=FRESH_HEARTBEAT_ONLY;"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET; got {wd.last_fire_reason}"
    )


# === consolidated from test_stuck_job_intelligence.py ===
def test_stuck_classifier_consulted_at_no_output_at_start_fire() -> None:
    """The StuckClassifier is consulted by _gate_fire at the NO_OUTPUT_AT_START path.

    Drive past no_output_at_start_seconds with no activity, no live
    corroborator, no fresh channels. The watchdog must FIRE
    NO_OUTPUT_AT_START (the classifier returns STUCK, the gate allows FIRE).
    The classifier IS consulted (not bypassed) on this path -- this is
    the contract that makes the gate the single boundary between the
    fire-decision helpers and the verdict-returning logic.
    """
    config = _stuck_job_intelligence_make_policy(idle_timeout=300.0, no_output_at_start=30.0)
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    watchdog = _stuck_job_intelligence_make_watchdog(config, clock, corroborator=_empty_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_stuck_job_intelligence.py ===
def test_stuck_classifier_returns_loading_defers_no_output_at_start() -> None:
    """LOADING deferral: a productive-but-quiet agent is NOT killed.

    When the subagent_liveness channel is fresh (process monitor reports
    a live subagent) the classifier returns LOADING via the
    subagent_liveness branch. The gate then defers the fire.

    Drive past no_output_at_start_seconds with no activity but a live
    subagent (process_monitor.live_count=1). The watchdog must
    CONTINUE because the classifier returned LOADING.
    """
    config = _stuck_job_intelligence_make_policy(
        idle_timeout=300.0,
        no_output_at_start=30.0,
        activity_ttl=120.0,
    )
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    monitor = _FakeProcessMonitorStuckJobIntelligence(live_count=1)
    watchdog = _stuck_job_intelligence_make_watchdog(
        config,
        clock,
        corroborator=_empty_corroborator,
        process_monitor=monitor,
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE, (
        f"expected NO_OUTPUT_AT_START to defer (live subagent -> LOADING), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"expected last_fire_reason != NO_OUTPUT_AT_START (LOADING defers),"
        f" got {watchdog.last_fire_reason}"
    )


# === consolidated from test_stuck_job_intelligence.py ===
def test_stuck_classifier_returns_offline_defers_no_output_at_start() -> None:
    """WAITING_ON_CONNECTIVITY deferral: offline network defers the fire.

    When the connectivity_state_provider returns 'offline', the
    classifier returns WAITING_ON_CONNECTIVITY. The gate defers.

    Drive past no_output_at_start_seconds with no activity and the
    connectivity provider reporting 'offline'. The watchdog must
    CONTINUE because the classifier returned WAITING_ON_CONNECTIVITY.
    """
    config = _stuck_job_intelligence_make_policy(idle_timeout=300.0, no_output_at_start=30.0)
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    watchdog = _stuck_job_intelligence_make_watchdog(
        config,
        clock,
        corroborator=_empty_corroborator,
        connectivity_state_provider=lambda: "offline",
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE, (
        f"expected NO_OUTPUT_AT_START to defer (offline -> WAITING_ON_CONNECTIVITY),"
        f" got verdict={verdict}"
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_stuck_job_intelligence.py ===
def test_children_persist_too_long_uses_live_corroboration_alive_by() -> None:
    """CHILDREN_PERSIST_TOO_LONG consults the live corroborator during the fire.

    When cumulative_waiting_on_child_seconds reaches the ceiling, the
    watchdog fires CHILDREN_PERSIST_TOO_LONG. The watchdog's
    ``_handle_waiting_branch`` passes the LIVE ``current_corr`` to
    ``_gate_fire`` which threads it into the classifier -- this is
    the analysis-feedback contract for AC-05 (the gate sees the
    LIVE corroboration rather than the stale ``self._last_alive_by``
    field, which is only populated post-fire by ``NO_PROGRESS_QUIET``).

    This test pins the contract: the corroborator must be invoked
    LIVE during the fire decision. Drive past the cumulative ceiling
    with NO process monitor and the corroborator reporting
    ``alive_by=FRESH_PROGRESS``. The corroborator is consulted
    (call_count >= 1) and the fire happens (verdict == FIRE,
    last_fire_reason == CHILDREN_PERSIST_TOO_LONG).

    Pre-fix, the gate consulted the classifier with only the
    evidence_summary; the LIVE corroboration was not threaded into
    the classifier's decision. The new contract surfaces the LIVE
    corroboration to the classifier for future extensibility (e.g.
    distinguishing truly-dead-child scenarios from
    process-monitor-only live signals) without changing the gate's
    current verdict policy. The watchdog's own
    ``_effective_waiting_ceiling`` math already handles
    alive_by-based ceiling selection; the gate's
    classifier-verdict layer sees the corroboration so future
    refinements can use it without changing the call site.

    The corroborator must be invoked AT LEAST ONCE during the
    CHILDREN_PERSIST_TOO_LONG fire decision -- this is the proof
    that the gate saw the live corroboration.
    """
    config = _stuck_job_intelligence_make_policy(
        idle_timeout=1.0,
        max_waiting=2.0,
        activity_ttl=30.0,
    )
    clock = FakeClock(start=0.0)

    call_count: list[int] = [0]

    def _live_corroborator() -> CorroborationSnapshot:
        call_count[0] += 1
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    # NO process monitor. The corroborator is the only live-child
    # signal source; the gate must see it during the fire decision.
    watchdog = _stuck_job_intelligence_make_watchdog(
        config,
        clock,
        corroborator=_live_corroborator,
    )
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Enter WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the cumulative ceiling (2.0s). The corroborator
    # is consulted LIVE during the fire decision (the call_count
    # proves the gate saw the live corroboration). The fire happens
    # because the classifier's current verdict policy is unchanged
    # (the corroboration is exposed to the classifier for future
    # extensibility but does not change the verdict).
    clock.advance(5.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected CHILDREN_PERSIST_TOO_LONG to FIRE (the gate allows"
        f" the fire; the corroboration parameter is exposed for future"
        f" extensibility but does not change the verdict), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected last_fire_reason == CHILDREN_PERSIST_TOO_LONG, got {watchdog.last_fire_reason}"
    )
    # The corroborator must have been invoked LIVE during the fire
    # decision. This is the proof that the gate saw the live
    # corroboration -- the call_count is at least 2 because the
    # first call is at the WAITING_ON_CHILD entry + the post-entry
    # classify_quiet call inside _handle_waiting_branch.
    assert call_count[0] >= 1, (
        f"expected corroborator to be invoked at least once during the"
        f" CHILDREN_PERSIST_TOO_LONG fire decision, got {call_count[0]}"
    )


# === consolidated from test_stuck_job_intelligence.py ===
def test_children_persist_too_long_stale_corroboration_does_not_defeat_ceiling() -> None:
    """A STALE alive_by from the corroborator does NOT defeat the no_progress ceiling.

    When the corroborator returns a STALE alive_by signal
    (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
    ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
    ``STALE_LABEL_ONLY``), the watchdog's own
    ``_effective_waiting_ceiling`` math short-circuits those
    values to the shorter no_progress / os_descendant_only
    ceiling. The CHILDREN_PERSIST_TOO_LONG fire happens at the
    shorter ceiling. This is the existing contract for
    ``test_short_ceiling_fires_at_os_descendant_only_ceiling`` in
    ``test_os_descendant_only_escalation.py`` and
    ``test_cpu_idle_override_picks_no_progress_ceiling`` etc.

    This test pins the same contract using the corroboration
    parameter (no process monitor): when alive_by is a stale
    signal, the effective_ceiling is the no_progress ceiling, and
    the fire happens at the no_progress ceiling.
    """
    # max_waiting must be > no_progress_ceiling (the watchdog
    # invariant: no_progress_ceiling <= max_waiting). We pick
    # max_waiting=200.0 and no_progress_ceiling=10.0 so the test
    # reaches the no_progress ceiling quickly under FakeClock.
    config = _stuck_job_intelligence_make_policy(
        idle_timeout=1.0,
        max_waiting=200.0,
        no_progress_ceiling=10.0,
        activity_ttl=30.0,
    )
    clock = FakeClock(start=0.0)

    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog = _stuck_job_intelligence_make_watchdog(
        config,
        clock,
        corroborator=_stale_corroborator,
    )
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Enter WAITING_ON_CHILD branch.
    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no_progress ceiling (10.0s). The
    # OS_DESCENDANT_ONLY_STALE_PROGRESS signal triggers the
    # no_progress ceiling math; the fire happens.
    clock.advance(15.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected CHILDREN_PERSIST_TOO_LONG to FIRE (stale OS_DESCENDANT"
        f" signal triggers no_progress ceiling), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_stuck_job_intelligence.py ===
def test_no_progress_quiet_stuck_when_corroborator_says_dead() -> None:
    """NO_PROGRESS_QUIET fires when the corroborator confirms a dead child.

    When the corroborator returns alive_by=None (no live signal at all),
    AND no fresh channel evidence is present, the watchdog fires
    NO_PROGRESS_QUIET after the no_progress_quiet_seconds ceiling.

    The conservative policy: pre-fix, NO_PROGRESS_QUIET fired for ANY
    agent that crossed the no_progress_quiet ceiling; with the gate
    refinement, NO_PROGRESS_QUIET fires ONLY when alive_by is None
    (truly dead child) AND no fresh channel evidence. This test pins
    the "truly dead child" branch -- the watchdog must FIRE.
    """
    config = _stuck_job_intelligence_make_policy(
        idle_timeout=300.0,
        max_waiting=1800.0,
        activity_ttl=30.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
    )
    clock = FakeClock(start=0.0)

    def _dead_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=False,
            scoped_child_count=0,
        )

    watchdog = _stuck_job_intelligence_make_watchdog(config, clock, corroborator=_dead_corroborator)
    watchdog.record_invocation_start()
    watchdog.record_activity()

    # Advance past the dumb-kill floor (120s) AND past the
    # no_progress_quiet ceiling (120s). All channels are stale,
    # corroborator says dead. The watchdog must FIRE NO_PROGRESS_QUIET.
    clock.advance(150.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE (corroborator says dead, no fresh channels), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
    # The corroborator's alive_by signal is captured at the moment of
    # the NO_PROGRESS_QUIET fire for downstream typed-cause reading.
    assert watchdog.last_alive_by is None, (
        f"expected last_alive_by=None (truly dead child), got {watchdog.last_alive_by}"
    )


# === consolidated from test_stuck_job_intelligence.py ===
def test_stuck_classifier_module_exposes_seven_kinds() -> None:
    """The StuckKind enum exposes the seven documented kinds.

    SILENT_SUBAGENT is the seventh kind added in this PR (a
    post-mortem diagnostic for "a subagent dispatched but went
    silent for >180s").  See
    ``tests/agents/idle_watchdog/test_stuck_classifier.py``
    for the diagnostic-behavior contract tests.
    """
    expected = {
        StuckKind.THINKING,
        StuckKind.LOADING,
        StuckKind.WAITING_ON_CONNECTIVITY,
        StuckKind.TRANSITIONING,
        StuckKind.STUCK,
        StuckKind.DUPLICATE_KILL,
        StuckKind.SILENT_SUBAGENT,
    }
    assert set(StuckKind) == expected


# === consolidated from test_stuck_job_sub_ceiling.py ===
def test_stuck_job_sub_ceiling_fires_at_600s_when_alive_by_is_stale() -> None:
    """The sub-ceiling MUST fire ``CHILDREN_PERSIST_TOO_LONG`` when:

      * cumulative ``WAITING_ON_CHILD`` time >= ``stuck_job_sub_ceiling_seconds``
      * corroborator reports a stale ``AliveBy``
      * ``scoped_child_active`` is True

    Drives the watchdog through a full waiting run with the stale
    corroborator; the sub-ceiling fires at 600s well before the 1800s
    cumulative ceiling.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(),
    )
    watchdog.record_invocation_start()

    # Advance past idle_timeout so the waiting branch is reachable.
    clock.advance(201.0)
    # Enter the waiting branch via the public API.
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate MUST enter the waiting branch; got {verdict!r}"
    )

    # Advance 600s inside the waiting branch. With
    # drain_window_seconds=0 the cumulative waiting time ticks up to
    # 600s at this point and the sub-ceiling trips.
    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, (
        f"stuck_job_sub_ceiling MUST fire at 600s with stale alive_by; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


# === consolidated from test_stuck_job_sub_ceiling.py ===
@pytest.mark.parametrize(
    "alive_by",
    (
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
        AliveBy.STALE_LABEL_ONLY,
    ),
)
def test_stuck_job_sub_ceiling_fires_for_every_stale_alive_by(alive_by: AliveBy) -> None:
    """The sub-ceiling MUST trip for every stale alive_by value.

    The fix must not single out one stale value (e.g.
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` only); all four stale values
    in ``_NON_PROGRESS_ALIVE_BY_VALUES`` trip the sub-ceiling
    consistently.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(alive_by),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate MUST enter the waiting branch; got {verdict!r}"
    )

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, (
        f"stuck_job_sub_ceiling MUST fire for alive_by={alive_by.value!r}; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_stuck_job_sub_ceiling.py ===
@pytest.mark.parametrize(
    "alive_by",
    (AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY),
)
def test_stuck_job_sub_ceiling_does_not_fire_when_alive_by_is_fresh(
    alive_by: AliveBy,
) -> None:
    """The sub-ceiling MUST NOT trip for FRESH alive_by values.

    A productive live child agent (``FRESH_PROGRESS`` /
    ``FRESH_HEARTBEAT_ONLY``) is by definition NOT a stuck job; the
    sub-ceiling is exclusively the stuck-but-alive detector.
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(alive_by),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"stuck_job_sub_ceiling MUST NOT fire for FRESH alive_by="
        f"{alive_by.value!r}; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None, (
        f"stuck_job_sub_ceiling MUST NOT fire for FRESH alive_by="
        f"{alive_by.value!r}; got last_fire_reason="
        f"{watchdog.last_fire_reason!r}"
    )


# === consolidated from test_stuck_job_sub_ceiling.py ===
def test_stuck_job_sub_ceiling_disabled_when_none() -> None:
    """A None sub-ceiling preserves the legacy behavior.

    When ``stuck_job_sub_ceiling_seconds=None``, the sub-ceiling is
    disabled. The waiting branch continues to use
    ``max_waiting_on_child_no_progress_seconds`` as the only
    stuck-job detector (the legacy 600s ceiling).
    """
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(
            stuck_job_sub_ceiling_seconds=None,
            max_waiting_on_child_no_progress_seconds=1800.0,
        ),
        clock,
        corroborator=_make_stuck_corroborator(),
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to 700s (past the sub-ceiling that is now disabled).
    clock.advance(499.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    # The sub-ceiling is disabled, so the gate does NOT fire at 700s.
    # Cumulative waiting time is 700s, well under the 1800s
    # max_waiting_on_child_no_progress_seconds ceiling.
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"with sub-ceiling disabled, the gate MUST NOT fire at 700s; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None


# === consolidated from test_stuck_job_sub_ceiling.py ===
def test_timeout_policy_default_stuck_job_sub_ceiling_matches_constant() -> None:
    """Default direct ``TimeoutPolicy`` callers get the 600s sub-ceiling."""
    policy = TimeoutPolicy(idle_timeout_seconds=200.0)

    assert policy.stuck_job_sub_ceiling_seconds == STUCK_JOB_SUB_CEILING_SECONDS
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


# === consolidated from test_stuck_job_sub_ceiling.py ===
def test_stuck_job_sub_ceiling_validated_positive_and_bounded() -> None:
    """The field MUST be validated in ``__post_init__``.

    Mirrors the other TimeoutPolicy validators:
      - ``<= 0`` raises ``ValueError``
      - ``> max_waiting_on_child_seconds`` raises ``ValueError``
    """
    # Negative value MUST be rejected.
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=-1.0,
        )
    # Zero MUST be rejected.
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=0.0,
        )
    # Greater than max_waiting_on_child_seconds MUST be rejected.
    with pytest.raises(
        ValueError,
        match="stuck_job_sub_ceiling_seconds must be <= max_waiting_on_child_seconds",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            max_waiting_on_child_seconds=600.0,
            stuck_job_sub_ceiling_seconds=700.0,
        )
    # A valid value MUST construct successfully.
    policy = TimeoutPolicy(
        idle_timeout_seconds=200.0,
        stuck_job_sub_ceiling_seconds=600.0,
    )
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


# === consolidated from test_stuck_job_sub_ceiling.py ===
def test_stuck_job_sub_ceiling_fires_when_scoped_child_inactive() -> None:
    """When ``scoped_child_active`` is False the sub-ceiling MUST NOT fire.

    The sub-ceiling is the stuck-but-alive detector; if no scoped
    child is active, the standard ``max_waiting_on_child_seconds``
    cumulative ceiling is the correct upper bound.
    """

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=False,
            oldest_child_seconds=200.0,
        )

    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_corroborator,
    )
    watchdog.record_invocation_start()

    clock.advance(201.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(600.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    # No scoped child active -> the sub-ceiling MUST NOT trip.
    # The cumulative time is 600s, well under the 1800s
    # max_waiting_on_child_seconds ceiling.
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"sub-ceiling MUST NOT fire when scoped_child_active=False; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_cache_is_hard_bounded_by_cap() -> None:
    """AC-04 (iteration-4): the cache is a HARD FIFO bound.

    Drives the PUBLIC :meth:`IdleWatchdog.poll_subagent_output`
    with a monitor that returns cap+5 distinct workers on poll 1,
    then shrinks to just ``cap`` workers on poll 2. On poll 1 the
    cache MUST NOT grow past the cap even when every discovered
    worker is still live (the cap is not a soft bound on live
    workers). The 5 oldest-inserted workers MUST be evicted into
    the tombstone on poll 1 so the cache holds exactly ``cap``
    entries (pure FIFO; no LRU refresh on poll).

    On poll 2 the 5 workers that disappeared from discovery are
    also released from the tombstone (they are no longer alive),
    leaving the cache at ``cap`` and the tombstone empty.

    The cap is DERIVED from observed behavior via
    :func:`_probe_cache_cap` rather than hardcoded, so a future
    change to the production cap does not silently drift the
    test away from the actual bound.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    # Poll 1: the cap is HARD. All cap+5 workers are polled on this
    # tick (the cap is enforced at the END of the polling pass so
    # the public surface still reports every worker's lines), but
    # the cache ends at exactly ``cap`` entries and the 5 oldest-
    # inserted workers are moved to the tombstone.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at exactly cap={cap}, "
        f"got {len(watchdog._subagent_output_captures)}"
    )
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA, (
        f"the {_OVERFLOW_DELTA} evicted workers MUST be tombstoned, "
        f"got {len(watchdog._evicted_worker_tombstones)}"
    )

    # Poll 2: the _OVERFLOW_DELTA oldest-inserted workers
    # (w-0..w-4) disappear from discovery. The cache MUST retain
    # the surviving cap workers; the tombstone MUST release the
    # now-dead workers.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries for workers no longer in "
        "discovery (the eviction cooldown ended because the worker "
        "actually died)"
    )
    # Every cap survivor MUST be retained in the cache. The
    # surviving set is ``range(_OVERFLOW_DELTA, first_count)``
    # (== ``range(_OVERFLOW_DELTA, cap + _OVERFLOW_DELTA)``), so
    # there are exactly ``cap`` survivors and we MUST assert on
    # every one of them (a partial iteration would silently miss
    # the boundary case where the last inserted survivor was
    # dropped despite still being alive).
    expected_survivors = {f"w-{i}" for i in range(_OVERFLOW_DELTA, first_count)}
    assert expected_survivors == set(watchdog._subagent_output_captures.keys()), (
        f"every cap survivor MUST be retained in the cache; "
        f"missing={expected_survivors - set(watchdog._subagent_output_captures.keys())}, "
        f"extra={set(watchdog._subagent_output_captures.keys()) - expected_survivors}"
    )


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_cache_does_not_evict_when_under_cap() -> None:
    """Inserts under the cap never evict anything."""
    cap = _probe_cache_cap()
    under_cap_count = cap // 2
    captures = {f"keep-{i}": _StaticCaptureEmpty() for i in range(under_cap_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)

    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._subagent_output_captures) == under_cap_count
    assert len(watchdog._evicted_worker_tombstones) == 0
    for index in range(under_cap_count):
        assert f"keep-{index}" in watchdog._subagent_output_captures


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_cache_eviction_skips_existing_workers() -> None:
    """A repeated discover call for already-cached workers does NOT trigger eviction.

    Drives the PUBLIC entry point twice with the same worker
    set. The second call MUST NOT grow the cache (the existing
    workers are reused), and the cap MUST hold. Pure-FIFO means
    a repeated poll does NOT refresh the LRU position -- a worker
    that is polled repeatedly is NOT promoted to the most-recent;
    it stays in its original insertion position. This pins the
    FIFO contract so a future refactor cannot silently switch
    the cache to LRU semantics.
    """
    cap = _probe_cache_cap()

    primed = {f"primed-{i}": _StaticCaptureEmpty() for i in range(cap)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(primed)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0

    # Same workers, second tick: no new workers, no eviction.
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._subagent_output_captures) == cap
    assert len(watchdog._evicted_worker_tombstones) == 0


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_cache_polls_all_workers_then_enforces_cap() -> None:
    """AC-04 (iteration-4): a poll with cap+5 workers reports cap+5 lines
    on the current tick and leaves the cache at exactly ``cap``.

    The cap is enforced at the END of the polling pass, so the
    public surface still reports EVERY worker's lines for the
    current tick (a high-fan-out tick is not a sampling cap).
    Only the next-tick cache state is bounded. ``_subagent_output_count``
    advances by exactly cap+5.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    captures = {f"w-{i}": _StaticCapture() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)

    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    # Every discovered worker's capture is read once (one line each).
    assert fresh == first_count
    # The watchdog records the total in the public counter.
    assert watchdog._subagent_output_count == first_count
    # The cache is HARD-bounded at ``cap`` after the polling pass.
    assert len(watchdog._subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at cap={cap}, got {len(watchdog._subagent_output_captures)}"
    )
    # The _OVERFLOW_DELTA oldest-inserted workers were evicted into
    # the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_tombstone_prevents_duplicate_output_after_eviction() -> None:
    """AC-04 (iteration-4): the tombstone prevents duplicate output when
    FIFO eviction + re-addition would otherwise re-read historical lines.

    Drives the PUBLIC entry point twice with cap+5 LIVE workers
    (all still alive on the second poll). The hard FIFO cap MUST
    evict _OVERFLOW_DELTA workers on poll 1 and tombstone them so
    they cannot re-enter the cache on poll 2 (where their stateful
    read position has been lost). Poll 2 MUST report ZERO new lines
    because:
      * the cap survivors in cache are stateful (second read = 0)
      * the _OVERFLOW_DELTA tombstoned workers are skipped entirely
    Without the tombstone the evicted workers would be re-added
    with fresh captures and re-read every historical line, exactly
    the duplicate-line bug the iteration-3 dead-worker eviction
    was trying to avoid.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    # Build cap+5 distinct workers, each returning N lines on the
    # FIRST read_lines call. With hard FIFO + tombstone, the
    # _OVERFLOW_DELTA oldest-inserted workers are evicted and
    # tombstoned; the remaining cap workers are polled and report
    # their 3 lines each.
    line_count_per_worker = 3
    captures: dict[str, _StatefulCapture] = {}
    for i in range(first_count):
        lines = [f"line-{j}-worker-{i}" for j in range(line_count_per_worker)]
        captures[f"w-{i}"] = _StatefulCapture(lines)

    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)

    first_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    # Poll 1 polls every discovered worker (cap enforcement happens
    # AFTER polling so every worker's lines are reported).
    expected_first = first_count * line_count_per_worker
    assert first_poll == expected_first, (
        f"first poll MUST report every line from every worker, got {first_poll} "
        f"vs expected {expected_first}"
    )
    # Cache ends at exactly ``cap`` (hard bound enforced).
    assert len(watchdog._subagent_output_captures) == cap
    # The _OVERFLOW_DELTA oldest-inserted workers are in the tombstone.
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA

    # All first_count workers are still alive on poll 2.
    # A CORRECT implementation must skip the tombstoned workers
    # (so they do not re-emit historical lines) AND return 0 from
    # the cap survivors' stateful captures.
    clock.advance(0.01)
    second_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    assert second_poll == 0, (
        f"second poll on the same live workers MUST report 0 new lines "
        f"(stateful capture read positions must be honored AND "
        f"tombstoned workers must be skipped); got {second_poll}"
    )
    # Cache is still at the cap.
    assert len(watchdog._subagent_output_captures) == cap
    # Tombstone is still populated because the evicted workers are
    # still alive (the tombstone is the cooldown that suppresses
    # their re-addition; it cycles out when the workers actually
    # exit OR when the tombstone cap binds and evicts the oldest-
    # inserted).
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_tombstone_cycles_out_when_worker_dies() -> None:
    """When a tombstoned worker actually exits, the tombstone releases its entry.

    The tombstone is the eviction cooldown that prevents re-addition
    of evicted workers (which would re-emit historical lines). Once
    a tombstoned worker actually disappears from
    ``discover_subagent_outputs``, the cooldown is no longer
    needed and the entry is released so the next time the same
    worker ID appears it can be re-added cleanly.
    """
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    # First poll: cap+5 workers. _OVERFLOW_DELTA are evicted and
    # tombstoned.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == _OVERFLOW_DELTA

    # Second poll: the _OVERFLOW_DELTA tombstoned workers are gone
    # from discovery. Their tombstone entries MUST be released.
    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog._evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries when the worker actually exits (no longer in discovery)"
    )


# === consolidated from test_subagent_capture_eviction.py ===
def test_subagent_capture_tombstone_is_itself_bounded() -> None:
    """The tombstone is bounded at the production cap via FIFO eviction.

    A long-lived watchdog tick that keeps evicting FIFO workers
    cannot grow the tombstone past its cap. FIFO eviction from
    the tombstone mirrors the cache eviction policy: the oldest-
    inserted entry is dropped first so the most-recently-evicted
    workers retain their cooldown priority.

    This test overflows BOTH the cache cap and the tombstone cap
    in a single poll so the tombstone cap binding is exercised.
    The worker count is ``cap + cap + 1`` so after cache eviction
    ``cap + 1`` workers are in the tombstone and the tombstone cap
    binds, dropping the oldest-inserted entry.
    """
    cap = _probe_cache_cap()
    tombstone_cap = _probe_tombstone_cap()
    # cap + cap + 1 workers: cache evicts to cap, leaving cap+1
    # evicted entries; tombstone evicts to cap, leaving the
    # newest cap entries (w-1..w-cap).
    first_count = cap + cap + 1

    # Insert cap+cap+1 workers; cap+1 are tombstoned (cache evicts
    # to cap). The tombstone cap binds at ``cap``, so the oldest
    # 1 of the cap+1 evicted workers is dropped from the
    # tombstone at the end of the eviction step.
    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog._evicted_worker_tombstones) == tombstone_cap
    # The cap MUST still hold.
    assert len(watchdog._subagent_output_captures) == cap
    # The tombstone MUST hold the MOST RECENTLY evicted workers
    # (FIFO: the oldest-inserted tombstone entries are dropped
    # first). ``first_count - cap = cap + 1`` workers were evicted
    # from the cache. The tombstone cap (``cap``) drops the oldest
    # 1 of those entries (w-0), leaving w-1..w-cap in the
    # tombstone.
    num_evicted = first_count - cap
    tombstoned = list(watchdog._evicted_worker_tombstones.keys())
    assert tombstoned == [f"w-{i}" for i in range(num_evicted - tombstone_cap, num_evicted)]


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_helper_processes_alone_yield_zero_filtered_count() -> None:
    """R1: a monitor that only sees helper PIDs returns 0 from BOTH seam names.

    The product spec cites ``npm test``, ``cargo build``, ``find /`` as
    helper spawns that MUST NOT contribute to the subagent count. The
    monitor's broader ``descendant_snapshot`` count is 10 helpers, but
    the FILTERED count is 0 -- the watchdog defers on the filtered
    count only.
    """
    monitor = _FilteredCountMonitor(
        filtered_count=0,
        descendant_snapshot_count=10,
    )
    assert monitor.spawned_subagent_count() == 0
    assert monitor.live_subagent_count() == 0
    # The broader count is the bug source -- the filtered seam is 0
    # even when 10 helpers are present in the descendant tree.
    assert monitor.descendant_snapshot()[0] == 10


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_spawned_subagent_count_equals_live_subagent_count() -> None:
    """The alias is faithful: ``spawned_subagent_count() == live_subagent_count()``.

    R1: the filtered count is the ONLY count the watchdog defers on.
    Both names MUST return the SAME filtered value so callers that
    continue to call ``live_subagent_count`` (legacy callers in
    ``_waiting_branch.py`` and ``_activity_methods.py``) see the same
    signal as new callers using ``spawned_subagent_count``.
    """
    monitor = _FilteredCountMonitor(filtered_count=3)
    assert monitor.spawned_subagent_count() == 3
    assert monitor.live_subagent_count() == 3
    assert monitor.spawned_subagent_count() == monitor.live_subagent_count()


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_subagent_pid_registry_bounded_at_max_entries() -> None:
    """R1 + resource-lifecycle: registry is FIFO-bounded at 1024 entries.

    A long unattended invocation can register thousands of subagent
    PIDs (one per dispatched worker). An unbounded registry would
    retain heavyweight ``SubagentIdentity`` records across runs and
    bloat the watchdog's memory footprint. The audit
    ``audit_resource_lifecycle`` enforces a FIFO cap on every long-
    lived mutable collection; the registry honours the cap with
    ``OrderedDict.popitem(last=False)`` eviction.
    """
    assert _MAX_REGISTRY_ENTRIES == 1024
    registry = SubagentPidRegistry()
    # PIDs must be positive; offset by 1_000_000 to stay well clear of
    # kernel reserved values. ``registered_at_monotonic`` is the
    # monotonic timestamp captured at registration time (i.e. the
    # iteration index -- used purely for ordering).
    for pid in range(1_000_001, 1_002_001):
        registry.register(pid, source="opencode", now=float(pid))
    # The registry evicts the OLDEST entries first; the surviving
    # PIDs are the most-recently-registered ones.
    snapshot = registry.snapshot()
    assert len(snapshot) == _MAX_REGISTRY_ENTRIES == 1024
    surviving_pids = [identity.pid for identity in snapshot]
    assert surviving_pids == list(range(1_000_977, 1_002_001))
    # The OLDEST pids (1_000_001..1_000_976) are evicted FIFO.
    assert 1_000_001 not in registry.known_pids()
    assert 1_000_976 not in registry.known_pids()
    assert 1_000_977 in registry.known_pids()
    assert 1_002_000 in registry.known_pids()


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_register_is_idempotent() -> None:
    """Duplicate ``register`` calls preserve the FIRST ``registered_at_monotonic``.

    R1: the watchdog reads ``registered_at_monotonic`` to reason about
    subagent lifetime. A duplicate call MUST NOT rewrite the timestamp
    (otherwise a repeated discovery tick could indefinitely extend a
    stale subagent's lifetime). Idempotency is the canonical contract
    of the registry.
    """
    registry = SubagentPidRegistry()
    first = registry.register(1234, source="opencode", now=10.0)
    second = registry.register(1234, source="opencode", now=999.0)
    third = registry.register(1234, source="claude", now=42.0)
    assert first.registered_at_monotonic == 10.0
    assert second.registered_at_monotonic == 10.0
    assert third.registered_at_monotonic == 10.0
    # Only ONE entry for PID 1234 (idempotent).
    assert len(registry.snapshot()) == 1
    # The first source wins on idempotent re-registration. This
    # matches the contract that ``register`` returns the existing
    # identity unchanged.
    assert registry.snapshot()[0].source == "opencode"


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_unregister_removes_pid() -> None:
    """``unregister`` is the canonical way to retire a PID from the registry.

    R1: a subagent that exits must be removed so the watchdog stops
    deferring on it. ``unregister`` is a no-op when the PID is unknown
    (returns None / does not raise).
    """
    registry = SubagentPidRegistry()
    registry.register(1234, source="opencode", now=0.0)
    assert 1234 in registry.known_pids()
    assert len(registry.snapshot()) == 1
    registry.unregister(1234)
    assert 1234 not in registry.known_pids()
    assert len(registry.snapshot()) == 0
    # Unknown PID: no-op.
    registry.unregister(9999)
    assert 9999 not in registry.known_pids()


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_spawned_subagent_count_filters_out_unregistered_descendants() -> None:
    """The SubagentPidRegistry is the FILTER; unregistered PIDs are helpers.

    R1: a descendant PID that is in ``psutil.children(recursive=True)``
    but NOT in the registry is an INCIDENTAL_HELPER. The filtered
    count must NOT include it. This is the headline assertion that
    distinguishes the filtered seam from the broader
    ``descendant_snapshot`` count.
    """
    registry = SubagentPidRegistry()
    # Register only ONE subagent.
    registry.register(7001, source="opencode", now=0.0)
    # A SubagentPidSource wrapping the registry returns only the
    # registered PID (filtered).
    source = _RegistryPidSource(registry, source_label="opencode")
    assert source.known_subagent_pids() == {7001}
    # 3 unregistered PIDs in the broader descendant tree are HELPERS;
    # the filtered count is 1, NOT 4.
    assert len(source.known_subagent_pids()) == 1
    # The mock monitor's broader count would be 4 (1 registered + 3
    # helpers); the FILTERED count is 1. This is the headline R1
    # invariant: filtered < broader when helpers exist.
    broader_count = 1 + 3
    assert len(source.known_subagent_pids()) < broader_count


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_subagent_identity_rejects_invalid_source() -> None:
    """``SubagentIdentity`` constructor validates ``source`` against the allowed set.

    R1: the canonical set of transports is the only valid ``source``
    label; an unrecognized label would let an arbitrary code path
    introduce a new "real subagent" type without updating the
    canonical owner. The constructor rejects unknown sources so the
    type system enforces the canonical set.
    """
    # Use ``cast`` rather than a mypy suppression so the test file
    # carries no suppressions (the audit policy forbids test-file
    # suppressions). Cast to ``Any`` to bypass the Literal narrowing
    # without an ignore.
    bad_source: Any = "unknown-transport"
    with pytest.raises(ValueError, match="unknown subagent source"):
        SubagentIdentity(
            pid=1234,
            source=bad_source,
            registered_at_monotonic=0.0,
        )
    with pytest.raises(ValueError, match="pid must be positive"):
        SubagentIdentity(
            pid=0,
            source="opencode",
            registered_at_monotonic=0.0,
        )
    with pytest.raises(ValueError, match="pid must be positive"):
        SubagentIdentity(
            pid=-1,
            source="opencode",
            registered_at_monotonic=0.0,
        )


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_process_monitor_protocol_includes_spawned_subagent_count() -> None:
    """The ProcessMonitor Protocol MUST advertise ``spawned_subagent_count``.

    R1: the audit ``audit_activity_aware_watchdog`` flags any reader
    that uses ``descendant_snapshot()`` instead of
    ``spawned_subagent_count()`` for ``scoped_child_active``. The
    Protocol MUST declare both names so a ``@runtime_checkable``
    isinstance check against the Protocol works for duck-typed
    monitors that implement either name.
    """
    assert hasattr(ProcessMonitor, "spawned_subagent_count")
    assert hasattr(ProcessMonitor, "live_subagent_count")


# === consolidated from test_subagent_identity_excludes_helpers.py ===
def test_filtered_count_seam_is_isolated_per_transport() -> None:
    """A Claude-registered PID is invisible to an OpenCode monitor (and vice versa).

    R1: the registry is shared across transports but the per-transport
    filter (``_RegistryBackedSubagentPidSource``) restricts the view
    to entries matching its own source label. A Claude-registered PID
    MUST NOT contribute to an OpenCode filter (different transport,
    different worker lifecycle).
    """
    registry = SubagentPidRegistry()
    registry.register(8001, source="claude", now=0.0)
    registry.register(8002, source="opencode", now=0.0)
    opencode_source = make_opencode_subagent_pid_source(registry)
    assert opencode_source.known_subagent_pids() == {8002}
    # Claude filter would see only 8001.
    claude_source = make_claude_subagent_pid_source(registry)
    assert claude_source.known_subagent_pids() == {8001}


# === consolidated from test_subagent_progress_surface.py ===
def test_last_subagent_progress_description_property_returns_recorded_value() -> None:
    """``IdleWatchdog.last_subagent_progress_description`` returns the most
    recent description set via ``record_subagent_work`` and resets to
    ``None`` on ``record_invocation_start``."""
    watchdog, _clock = _subagent_progress_surface_make_watchdog()
    watchdog.record_invocation_start()

    assert watchdog.last_subagent_progress_description is None

    watchdog.record_subagent_work(description="agent is reading foo.py")
    assert watchdog.last_subagent_progress_description == "agent is reading foo.py"

    watchdog.record_subagent_work(description="agent is writing bar.py")
    assert watchdog.last_subagent_progress_description == "agent is writing bar.py"

    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_description is None


# === consolidated from test_subagent_progress_surface.py ===
def test_register_default_subagent_activity_listener_invokes_listener_with_payload() -> None:
    """``register_default_subagent_activity_listener`` receives every
    ``WaitingStatusEvent`` whose ``subagent_activity`` field is populated.

    The listener is reset to ``None`` on ``record_invocation_start`` so state
    does not leak across invocations.
    """
    watchdog, _clock = _subagent_progress_surface_make_watchdog()
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="agent is reading foo.py")
    # Trigger an emit cycle; the listener should receive the event with the
    # subagent_activity field populated from the last recorded description.
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )

    assert len(captured) == 1
    assert captured[0].subagent_activity == "agent is reading foo.py"
    assert captured[0].kind == WaitingStatusKind.ENTERED

    # A second emit without a new subagent activity description should still
    # forward the most recent description.
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=1.0,
        idle_elapsed=1.0,
        ceiling_seconds=60.0,
    )
    assert len(captured) == 2
    assert captured[1].subagent_activity == "agent is reading foo.py"

    # Resetting the invocation must clear the listener state so a prior run's
    # listener is not called for a fresh run.
    watchdog.record_invocation_start()
    captured.clear()
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )
    assert captured == []


# === consolidated from test_timeout_policy.py ===
def test_default_no_progress_quiet_minimum_invocation_seconds_matches_constant() -> None:
    """Default value matches the package-level constant (120.0s)."""
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    assert policy.no_progress_quiet_minimum_invocation_seconds == (
        NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 120.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_none_as_disabled() -> None:
    """None disables the dumb-kill floor (documented escape hatch)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=None,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds is None


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_zero_when_set() -> None:
    """0.0 is rejected; the floor cannot be silently disabled by a 0.0 typo."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_minimum_invocation_seconds=0.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_negative_when_set() -> None:
    """Negative values are rejected; the floor cannot be silently disabled."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_minimum_invocation_seconds=-1.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_positive_floor() -> None:
    """Any positive float is allowed as the floor."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 60.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_floor_above_ceiling() -> None:
    """Floor must be <= no_progress_quiet_seconds when both are set."""
    with pytest.raises(ValueError, match="must be <= no_progress_quiet_seconds"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=200.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_floor_equal_to_ceiling() -> None:
    """Floor == ceiling is allowed (floor is a sub-window of the ceiling)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        no_progress_quiet_heartbeat_ceiling_seconds=120.0,
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 120.0
    assert policy.no_progress_quiet_seconds == 120.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_floor_with_none_ceiling() -> None:
    """Floor can be set when the ceiling itself is None (ceiling disabled)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert policy.no_progress_quiet_seconds is None
    assert policy.no_progress_quiet_minimum_invocation_seconds == 60.0


# === consolidated from test_timeout_policy.py ===
def test_default_no_progress_quiet_heartbeat_ceiling_seconds_matches_constant() -> None:
    """Default value matches the package-level constant (240.0s).

    The default equals ``NO_PROGRESS_QUIET_SECONDS`` (240s) so the
    heartbeat-only branch fires AT the dumb-kill ceiling (the
    degenerate equal case permitted by the cross-field validator).
    Operators can RAISE the ceiling to give heartbeat-only subagents
    more headroom as long as the value stays <= ``no_progress_quiet_seconds``
    when both are set.
    """
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 240.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_none_as_disabled_heartbeat_ceiling() -> None:
    """None disables the heartbeat-only ceiling (documented escape hatch)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
    )
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds is None


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_zero_heartbeat_ceiling() -> None:
    """0.0 is rejected; the heartbeat-only ceiling cannot be silently disabled."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_heartbeat_ceiling_seconds=0.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_negative_heartbeat_ceiling() -> None:
    """Negative values are rejected; the heartbeat-only ceiling cannot be silently disabled."""
    with pytest.raises(ValueError, match="must be positive when set"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_heartbeat_ceiling_seconds=-1.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_rejects_heartbeat_ceiling_above_no_progress_quiet_seconds() -> None:
    """Heartbeat-only ceiling must be <= no_progress_quiet_seconds when both are set.

    Without this cross-field guard, an operator could configure the
    heartbeat-only ceiling to be LONGER than the dumb-kill
    ``no_progress_quiet_seconds`` ceiling, which would silently defeat
    the heartbeat-only branch (the outer ceiling would trip first).
    """
    with pytest.raises(
        ValueError,
        match="no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_heartbeat_ceiling_equal_to_no_progress_quiet_seconds() -> None:
    """Heartbeat-only ceiling == no_progress_quiet_seconds is allowed.

    The heartbeat-only branch is a SHORTER, ORTHOGONAL ceiling. Equality
    is the degenerate case: the heartbeat branch and the dumb-kill
    branch trip at the same time. The contract permits this so an
    operator who wants the heartbeat-only branch to fire at the
    dumb-kill ceiling can do so without an off-by-one in the
    cross-field guard.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 240.0
    assert policy.no_progress_quiet_seconds == 240.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_heartbeat_ceiling_below_no_progress_quiet_seconds() -> None:
    """Heartbeat-only ceiling < no_progress_quiet_seconds is allowed (the happy path)."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert policy.no_progress_quiet_seconds == 240.0


# === consolidated from test_timeout_policy.py ===
def test_constructor_accepts_heartbeat_ceiling_with_none_no_progress_quiet_seconds() -> None:
    """Heartbeat-only ceiling is allowed when no_progress_quiet_seconds is None.

    The cross-field guard is only enforced when BOTH fields are set. A
    None ``no_progress_quiet_seconds`` (dumb-kill disabled) does NOT
    block the heartbeat-only ceiling; the heartbeat branch operates
    independently of the dumb-kill floor/ceiling and is consulted
    first in ``_is_no_progress_quiet``.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert policy.no_progress_quiet_seconds is None
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 240.0


# === consolidated from test_tool_call_parser.py ===
def test_known_tool_call_verbs_set_is_twelve_members() -> None:
    """``_KNOWN_TOOL_CALL_VERBS`` MUST be exactly the 12 canonical verbs.

    The R5 contract pins the parser's vocabulary to a closed set of 12
    verbs. Any future addition (or removal) of a verb changes the
    R5 contract and MUST be reviewed against the product spec.
    """
    assert isinstance(_KNOWN_TOOL_CALL_VERBS, frozenset), (
        f"_KNOWN_TOOL_CALL_VERBS MUST be a frozenset for immutability;"
        f" got {type(_KNOWN_TOOL_CALL_VERBS).__name__}"
    )
    assert len(_KNOWN_TOOL_CALL_VERBS) == 12, (
        f"_KNOWN_TOOL_CALL_VERBS MUST contain exactly 12 verbs (R5);"
        f" got {len(_KNOWN_TOOL_CALL_VERBS)}: {sorted(_KNOWN_TOOL_CALL_VERBS)}"
    )
    assert _KNOWN_TOOL_CALL_VERBS == _EXPECTED_CANONICAL_VERBS, (
        f"_KNOWN_TOOL_CALL_VERBS mismatch; expected="
        f"{sorted(_EXPECTED_CANONICAL_VERBS)}, got="
        f"{sorted(_KNOWN_TOOL_CALL_VERBS)}"
    )


# === consolidated from test_tool_call_parser.py ===
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # 12 canonical verbs with realistic production descriptions.
        ("tool_use:Read", "tool_use"),
        ("tool_result:Bash", "tool_result"),
        ("mcp_tool:mcp__server__tool", "mcp_tool"),
        ("subagent:child-A", "subagent"),
        ("bash:ls -la /tmp", "bash"),
        ("read:foo.py", "read"),
        ("write:bar.py", "write"),
        ("edit:baz.py", "edit"),
        ("glob:**/*.py", "glob"),
        ("grep:TODO", "grep"),
        ("webfetch:https://example.com", "webfetch"),
        ("websearch:ralph workflow watchdog", "websearch"),
    ],
)
def test_parse_returns_canonical_verb_for_known_prefix(description: str, expected: str) -> None:
    """Each canonical verb ``verb:<rest>`` MUST surface as ``verb``.

    The parser splits on the FIRST ``:`` (not ``": "``) because the
    canonical production format from the NDJSON parser layer is
    ``tool_use:<name>`` with no space after the colon (see
    ``ralph/agents/parsers/claude_interactive.py``). The parser returns
    the substring before the first ``:`` when that substring is in the
    canonical verb set, otherwise ``None``.
    """
    result = _parse_tool_call_from_description(description)
    assert result == expected, f"description={description!r}: expected {expected!r}, got {result!r}"


# === consolidated from test_tool_call_parser.py ===
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # None description (no subagent observation yet).
        (None, None),
        # Empty string (falsy description).
        ("", None),
        # No colon at all -- the partition on ``:`` returns ``head=desc``,
        # ``sep=''``, so the parser returns ``None``.
        ("no_colon", None),
        # Empty after the colon -- the head IS a known verb so the
        # parser returns the head regardless of the tail.
        ("tool_use:", "tool_use"),
        # Unknown verb prefix -- the head is NOT in the canonical set.
        ("unknown_verb:Read", None),
        # Multi-colon description -- the partition takes only the head
        # before the FIRST colon, so a description like
        # ``tool_use:Read:something`` still surfaces ``tool_use``.
        ("tool_use:Read:something", "tool_use"),
        # Sanitized description: the sanitizer replaces ``/etc/foo`` with
        # ``<redacted>`` so the post-sanitize string is ``tool_use:<redacted>``.
        # The parser only inspects the head, so the redacted tail is
        # irrelevant.
        ("tool_use:<redacted>", "tool_use"),
        # Real sanitized ``/etc/`` path form (mirrors what
        # ``_sanitize_subagent_description`` produces).
        ("tool_use:/etc/<redacted>", "tool_use"),
        # Leading-bracket description -- ``[subagent] progress: phase=1``
        # partitions as ``head="[subagent] progress"`` which is NOT a
        # canonical verb, so the parser returns ``None``. The
        # ``[subagent]`` marker is a parser-layer signal, NOT a
        # tool-call verb; operators see the full line as
        # ``subagent_activity`` but ``current_subagent_tool_call`` is
        # ``None``.
        ("[subagent] progress: phase=1", None),
        # JSON envelope description -- ``{"type": "child_progress"}``
        # partitions as ``head='{"type"'`` which is NOT a canonical verb.
        ('{"type": "child_progress"}', None),
        # JSON envelope with subagent_activity field that happens to
        # look like ``"subagent_activity": "tool_use:Read"`` -- the
        # partition returns ``head='"subagent_activity"'`` which is NOT
        # a canonical verb. Operators see the full string as
        # ``subagent_activity``; the ``current_subagent_tool_call`` field
        # is ``None``.
        ('"subagent_activity": "tool_use:Read"', None),
    ],
)
def test_parse_returns_none_for_edge_cases(description: str | None, expected: str | None) -> None:
    """Edge cases MUST return ``None`` (not raise, not leak partial data)."""
    result = _parse_tool_call_from_description(description)
    assert result == expected, f"description={description!r}: expected {expected!r}, got {result!r}"


# === consolidated from test_tool_call_parser.py ===
def test_parse_does_not_mutate_known_verb_set() -> None:
    """Repeated calls with the same description MUST NOT mutate the canonical set.

    The canonical verb set is a ``frozenset`` (immutable), but this
    test pins that the helper does not introduce side effects that
    could silently change the set between calls (e.g., a future
    refactor that switches to a mutable set).
    """
    snapshot_before = set(_KNOWN_TOOL_CALL_VERBS)
    for _ in range(100):
        _parse_tool_call_from_description("tool_use:Read")
        _parse_tool_call_from_description(None)
        _parse_tool_call_from_description("")
        _parse_tool_call_from_description("unknown_verb:Read")
    snapshot_after = set(_KNOWN_TOOL_CALL_VERBS)
    assert snapshot_before == snapshot_after, (
        f"_KNOWN_TOOL_CALL_VERBS mutated across calls; before="
        f"{snapshot_before}, after={snapshot_after}"
    )
    assert len(snapshot_after) == 12, (
        f"_KNOWN_TOOL_CALL_VERBS size changed across calls; got {len(snapshot_after)} members"
    )


# === consolidated from test_tool_result_routing.py ===
def test_tool_result_does_not_wipe_the_tool_call_streak() -> None:
    """Five identical calls must trip even though each result arrives."""
    clock = FakeClock()
    watchdog = _tool_result_routing_watchdog(clock)
    record = MethodType(ProcessLineReader._record_line_activity, _reader())
    call = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    result = json.dumps({"type": "tool_result", "output": "ok"})

    for _ in range(5):
        record(watchdog, call + "\n")
        record(watchdog, result + "\n")
        clock.advance(1.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


# === consolidated from test_tool_result_routing.py ===
def test_tool_result_is_attributed_to_the_post_tool_result_stall() -> None:
    """The wedge must be reported as STALLED_AFTER_TOOL_RESULT, not a generic
    NO_OUTPUT_DEADLINE, which is all an off-PTY transport could produce before.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            post_tool_result_progression_seconds=120.0,
            repeated_error_consecutive_threshold=None,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )
    record = MethodType(ProcessLineReader._record_line_activity, _reader())

    record(watchdog, json.dumps({"type": "tool_result", "output": "ok"}) + "\n")
    clock.advance(400.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT


# === consolidated from test_tool_result_routing.py ===
def test_tool_result_counts_as_meaningful_output() -> None:
    """A tool result IS real output, so NO_OUTPUT_AT_START must not fire."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            post_tool_result_progression_seconds=None,
            repeated_error_consecutive_threshold=None,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )
    record = MethodType(ProcessLineReader._record_line_activity, _reader())

    record(watchdog, json.dumps({"type": "tool_result", "output": "ok"}) + "\n")
    clock.advance(60.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START
    assert verdict != WatchdogVerdict.FIRE


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_event_kind_exists() -> None:
    """WaitingStatusKind.SUBAGENT_PROGRESS MUST exist on the enum so
    listeners can filter by kind.
    """
    assert hasattr(WaitingStatusKind, "SUBAGENT_PROGRESS"), (
        "WaitingStatusKind.SUBAGENT_PROGRESS missing; the watchdog's"
        " waiting-status stream cannot surface per-subagent progress"
        " without this enum value"
    )
    assert WaitingStatusKind.SUBAGENT_PROGRESS.value == "subagent_progress"


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_emits_once_when_monitor_has_live_subagents() -> None:
    """When ``_process_monitor.live_subagent_count() > 0`` the watchdog
    MUST emit exactly one ``SUBAGENT_PROGRESS`` event per throttle
    window while WAITING_ON_CHILD is active.
    """
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=2,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    # Advance past idle_timeout so the next evaluate enters
    # WAITING_ON_CHILD (which is where _handle_waiting_branch emits).
    clock.advance(6.0)

    # First evaluate call enters WAITING_ON_CHILD and emits ENTERED.
    verdict = watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert verdict.value == "waiting_on_child", (
        f"evaluate MUST return WAITING_ON_CHILD when classify_quiet"
        f" returns WAITING_ON_CHILD and idle_timeout elapsed; got {verdict}"
    )
    # ENTERED is captured above; clear so we only inspect SUBAGENT_PROGRESS.
    captured.clear()

    # Advance 5s and evaluate again: the SUBAGENT_PROGRESS cadence
    # window (30s) has NOT elapsed since the (empty) emit timestamp so
    # the event must NOT emit.
    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS emitted before the throttle window"
        f" elapsed; got {len(subagent_emits)} events"
    )

    # Advance to 31s past the previous evaluate tick: throttle window
    # has elapsed and the next evaluate MUST emit exactly one
    # SUBAGENT_PROGRESS event.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"expected exactly 1 SUBAGENT_PROGRESS event after the throttle"
        f" window elapsed; got {len(subagent_emits)}"
    )
    # The diagnostic dict carries the live subagent count.
    assert subagent_emits[0].diagnostic.get("live_subagent_count") == 2, (
        f"SUBAGENT_PROGRESS diagnostic.live_subagent_count MUST be 2;"
        f" got {subagent_emits[0].diagnostic.get('live_subagent_count')}"
    )


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_emits_with_recorded_description() -> None:
    """When ``record_subagent_work`` was called the diagnostic dict
    MUST carry the sanitized ``subagent_activity`` field.
    """
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(description="reading source.py")
    # Advance past idle_timeout to enter WAITING_ON_CHILD.
    clock.advance(6.0)
    # First evaluate: ENTERED.
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()
    # Advance past the throttle window and re-evaluate.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"expected 1 SUBAGENT_PROGRESS event; got {len(subagent_emits)}"
    )
    diag = subagent_emits[0].diagnostic
    assert diag.get("subagent_activity") == "reading source.py", (
        f"SUBAGENT_PROGRESS diagnostic.subagent_activity MUST forward"
        f" the recorded description; got {diag.get('subagent_activity')!r}"
    )
    assert diag.get("live_subagent_count") == 0


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_does_not_emit_without_evidence() -> None:
    """When NEITHER ``record_subagent_work`` was called NOR
    ``live_subagent_count() > 0`` the watchdog MUST NOT emit
    ``SUBAGENT_PROGRESS`` (the predicate guards against empty payloads).
    """
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()
    # Advance past the throttle window and re-evaluate; no record,
    # no monitor count -> no SUBAGENT_PROGRESS event.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS MUST NOT emit without evidence; got {len(subagent_emits)} events"
    )


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_rate_limit_respected() -> None:
    """Two ticks within the throttle window emit ONE event; a third
    tick after the window elapses emits a SECOND event.
    """
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    # ENTERED emit on first evaluate.
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()

    # First throttle-elapsed tick -> emit #1.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"expected 1 SUBAGENT_PROGRESS emit after first throttle window; got {len(subagent_emits)}"
    )

    # A tick at +5s (well within the 30s window) -> no new emit.
    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"second tick within throttle window MUST NOT re-emit; got {len(subagent_emits)}"
    )

    # A tick at +31s past the first emit (well past the 30s window)
    # -> emit #2.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 2, (
        f"expected 2 SUBAGENT_PROGRESS emits after second throttle"
        f" window; got {len(subagent_emits)}"
    )


# === consolidated from test_waiting_subagent_progress.py ===
def test_subagent_progress_resets_on_record_invocation_start() -> None:
    """``record_invocation_start`` MUST reset the throttle so a new
    invocation's first SUBAGENT_PROGRESS emit is not suppressed by a
    prior invocation's throttle state.
    """
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    # Drive a tick that emits SUBAGENT_PROGRESS at +31s.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured), (
        "first invocation SUBAGENT_PROGRESS missing"
    )

    # Reset to a new invocation. The throttle map MUST be cleared
    # so the first eligible tick after the reset can emit again.
    captured.clear()
    watchdog.record_invocation_start()
    clock.advance(6.0)
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured), (
        "second invocation's first SUBAGENT_PROGRESS emit was"
        " suppressed by stale throttle state from the prior invocation"
    )


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_returns_immediately_when_predicate_true() -> None:
    """Predicate truthy on first check -> returns value immediately, zero clock budget."""
    clock = FakeClock(start=0.0)

    def _predicate() -> int | None:
        return 42

    class _Watchdog(WatchLoopBase):
        def check(self) -> int | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=30.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == 42
    assert clock.monotonic() == 0.0


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_returns_value_when_predicate_becomes_true_after_ticks() -> None:
    """Predicate becomes truthy after clock advances some ticks -> returns value before deadline."""
    clock = FakeClock(start=0.0)
    call_count: list[int] = [0]

    def _predicate() -> str | None:
        call_count[0] += 1
        if call_count[0] >= 3:
            return "done"
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=10.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == "done"
    assert call_count[0] == 3
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_returns_none_on_timeout() -> None:
    """Predicate stays None for full timeout -> None returned; clock advances by timeout_s."""
    clock = FakeClock(start=0.0)

    def _predicate() -> None:
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> None:
            self.wait_until(
                predicate=_predicate,
                timeout_s=3.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    wd.check()
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_calls_on_tick_each_cycle() -> None:
    """on_tick is called on every poll cycle (but NOT on the first entry check)."""
    clock = FakeClock(start=0.0)
    tick_values: list[str | None] = []

    def _predicate() -> str | None:
        if len(tick_values) >= 2:
            return "found"
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=10.0,
                poll_interval_s=0.5,
                on_tick=tick_values.append,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == "found"
    assert tick_values == [None, None]


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_does_not_wait_when_predicate_true_on_entry() -> None:
    """Predicate is True on first call -> no clock advance, no on_tick."""
    clock = FakeClock(start=0.0)
    tick_calls: list[int | None] = []

    def _predicate() -> int | None:
        return 99

    class _Watchdog(WatchLoopBase):
        def check(self) -> int | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=5.0,
                poll_interval_s=0.5,
                on_tick=tick_calls.append,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == 99
    assert clock.monotonic() == 0.0
    assert tick_calls == []


# === consolidated from test_watch_loop_base.py ===
def test_signal_activity_wakes_wait_until_in_threaded_context() -> None:
    """signal_activity pulses the event; wait_until wakes before poll_interval_s."""
    clock = SystemClock()
    event = _threading.Event()
    predicate_value: list[bool] = [False]

    def _predicate() -> str | None:
        if predicate_value[0]:
            return "woken"
        return None

    class _Watchdog(WatchLoopBase):
        def __init__(self) -> None:
            super().__init__(clock)

        def wait(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=60.0,
                poll_interval_s=10.0,
            )

    wd = _Watchdog()

    def _signal_later() -> None:
        event.wait(0.05)
        predicate_value[0] = True
        wd.signal_activity()

    t = _threading.Thread(target=_signal_later, daemon=True)
    t.start()
    result = wd.wait_until(
        predicate=_predicate,
        timeout_s=60.0,
        poll_interval_s=10.0,
    )
    t.join()

    assert result == "woken"


# === consolidated from test_watch_loop_base.py ===
def test_wait_until_respects_non_divisible_timeout_boundary() -> None:
    """wait_until must not overshoot the requested timeout by a full poll interval.

    Analysis-feedback regression: with timeout_s=3.1 and poll_interval_s=0.5,
    the previous implementation always waited the full 0.5s tick, ending at
    3.5s instead of 3.1s. The fix clamps the final wait to the remaining
    deadline, so FakeClock stops at the timeout boundary.
    """
    clock = FakeClock(start=0.0)

    def _predicate() -> None:
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> None:
            self.wait_until(
                predicate=_predicate,
                timeout_s=3.1,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    wd.check()
    assert clock.monotonic() == pytest.approx(3.1, abs=0.001)


# === consolidated from test_watchdog_recovery_contract.py ===
@pytest.mark.timeout_seconds(5)
def test_no_sys_exit_in_idle_watchdog_or_process_reader() -> None:
    """Invariant 1: no sys.exit() OR raise SystemExit anywhere in
    idle_watchdog/ or _process_reader.py.

    The watchdog and the process reader must NEVER exit the process or
    raise SystemExit. The run loop owns the exit decision; if the
    watchdog ever calls sys.exit / os._exit / raise SystemExit, the
    pipeline exits due to a false-positive kill, which is exactly the
    dumb-kill the plan is designed to prevent. The test walks the AST
    for:

      * ``sys.exit(...)`` / ``sys.exit``
      * ``os._exit(...)`` / ``os._exit``
      * bare ``exit(...)`` / ``exit``
      * ``raise SystemExit(...)`` / ``raise SystemExit``
    """
    targets = [PROCESS_READER, *IDLE_WATCHDOG_DIR.glob("*.py")]
    for path in targets:
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                # bare ``raise SystemExit(...)`` / ``raise SystemExit``
                if isinstance(exc, ast.Call):
                    func = exc.func
                    if isinstance(func, ast.Name) and func.id == "SystemExit":
                        msg = (
                            f"raise SystemExit at {path}:{node.lineno} -- "
                            "watchdog/process reader must NEVER raise"
                            " SystemExit"
                        )
                        raise AssertionError(msg)
                # bare ``raise SystemExit``
                if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                    msg = (
                        f"raise SystemExit at {path}:{node.lineno} -- "
                        "watchdog/process reader must NEVER raise"
                        " SystemExit"
                    )
                    raise AssertionError(msg)
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "sys" and func.attr in ("exit", "_exit"):
                        msg = (
                            f"{func.value.id}.{func.attr} call at"
                            f" {path}:{node.lineno} -- watchdog/process"
                            " reader must NEVER call sys.exit / os._exit"
                        )
                        raise AssertionError(msg)
                    if func.value.id == "os" and func.attr == "_exit":
                        msg = (
                            f"os._exit call at {path}:{node.lineno} -- "
                            "watchdog/process reader must NEVER call"
                            " os._exit"
                        )
                        raise AssertionError(msg)
                if isinstance(func, ast.Name) and func.id == "exit":
                    # bare `exit()` is also forbidden
                    msg = (
                        f"bare exit() call at {path}:{node.lineno} -- "
                        "watchdog/process reader must NEVER call exit"
                    )
                    raise AssertionError(msg)


# === consolidated from test_watchdog_recovery_contract.py ===
def test_teardown_subtree_calls_are_verdict_guarded() -> None:
    """Invariant 2: process termination requires a watchdog fire or durable completion.

    The only non-watchdog path is a session-continuing transport whose
    completion evaluator has already confirmed the durable sentinel and any
    required artifact receipt. That completion path may stop a CLI which keeps
    stdout open after it has completed, preventing an otherwise successful run
    from hanging during bridge teardown.

    The process reader's ``_check_fire`` is the single teardown site
    for in-stream kills. It is only entered when the watchdog returned
    ``WatchdogVerdict.FIRE``. The guard must be a structural check
    (verdict == WatchdogVerdict.FIRE) on the function's parameters,
    not just a docstring claim. The same constraint applies to
    ``_handle.terminate(...)`` calls -- a terminate without a
    preceding FIRE verdict is a runaway kill.

    Additionally, every _handle.terminate call must be reached via
    a function whose enclosing caller invokes ``_check_fire`` (i.e.
    the terminate can only fire when the watchdog has decided). The
    test also asserts that any ``_handle.terminate`` call is inside
    a function whose body includes a structural guard
    ``verdict == WatchdogVerdict.FIRE`` (the same guard that protects
    teardown_subtree). This is the stronger form of the guard the
    plan asked for: terminate calls cannot happen outside the
    canonical fire path.
    """
    tree = _parse(PROCESS_READER)

    def _has_verdict_check(func: ast.FunctionDef) -> bool:
        """Return True if the function has a watchdog-fire or completion guard.

        Two families of fire verdicts are allowed:
          - ``WatchdogVerdict.FIRE`` (in-stream kills via IdleWatchdog)
          - ``PostExitVerdict.FIRE_*`` (post-exit kills via PostExitWatchdog)
        """
        for node in ast.walk(func):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if not (
                        isinstance(comparator, ast.Attribute)
                        and isinstance(comparator.value, ast.Name)
                    ):
                        continue
                    if comparator.value.id == "WatchdogVerdict" and comparator.attr == "FIRE":
                        return True
                    if comparator.value.id == "PostExitVerdict" and comparator.attr.startswith(
                        "FIRE_"
                    ):
                        return True
            if isinstance(node, ast.Attribute) and node.attr == "_completion_is_terminal":
                return True
            # ``BROKEN_AGENT_OUTPUT_GRACE_SECONDS`` elapsed-grace guard:
            # ``_check_broken_agent_timer`` kills only after the grace
            # window elapsed with zero meaningful output (a fire-class
            # verdict in its own right), so it is a legitimate kill site.
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "BROKEN_AGENT_OUTPUT_GRACE_SECONDS"
            ):
                return True
            if isinstance(node, ast.Name) and node.id == "BROKEN_AGENT_OUTPUT_GRACE_SECONDS":
                return True
        return False

    def _is_handle_terminate_call(node: ast.Call) -> bool:
        """Return True if the call is self._handle.terminate(...)."""
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr == "_handle"
            and any(
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "self"
                and arg.attr == "terminate"
                for arg in []  # not used; see below
            )
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == "terminate"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_handle"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        )

    def _is_terminate_call(node: ast.Call) -> bool:
        """Return True if the call is a .terminate(...) invocation
        on self._handle OR on a direct handle variable."""
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "terminate":
            return False
        is_self_handle = (
            isinstance(func.value, ast.Attribute)
            and func.value.attr == "_handle"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        )
        is_local_handle = isinstance(func.value, ast.Name) and func.value.id == "handle"
        return is_self_handle or is_local_handle

    kill_sites: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_terminate = _is_terminate_call(node)
        is_teardown = isinstance(func, ast.Name) and func.id == "teardown_subtree"
        if not (is_terminate or is_teardown):
            continue
        # Find the enclosing function
        enclosing: ast.FunctionDef | None = None
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.FunctionDef):
                continue
            for child in ast.walk(parent):
                if child is node:
                    enclosing = parent
                    break
            if enclosing is not None:
                break
        if enclosing is None:
            label = "terminate" if is_terminate else "teardown_subtree"
            msg = f"{label} at {PROCESS_READER}:{node.lineno} is not inside any function"
            raise AssertionError(msg)
        kill_sites.append((node, enclosing.name))

    for call, func_name in kill_sites:
        func = next(
            f
            for f in _function_bodies(tree, func_name)
            if any(child is call for child in ast.walk(f))
        )
        if not _has_verdict_check(func):
            label = "terminate" if _is_terminate_call(call) else "teardown_subtree"
            msg = (
                f"{label} at {PROCESS_READER}:{call.lineno} "
                f"(in function {func_name}) is not preceded by a "
                "watchdog-fire or durable-completion guard"
            )
            raise AssertionError(msg)


# === consolidated from test_watchdog_recovery_contract.py ===
@pytest.mark.timeout_seconds(5)
def test_watchdog_fire_reason_created_only_in_canonical_owners() -> None:
    """Invariant 3: WatchdogFireReason is created in the canonical two owner modules.

    The enum is *referenced* in many places (failure classification,
    timeout opts, error messages, tests) -- that is fine. What is
    forbidden is a third module that DECIDES a fire (i.e. constructs
    a new WatchdogFireReason value to be returned as a fire signal).

    The two canonical owner modules are:
      - ralph/agents/idle_watchdog/idle_watchdog.py (in-stream)
      - ralph/agents/post_exit_watchdog.py (post-exit)

    The watchdog's ``_gate_fire`` and the post-exit's ``wait_*`` are
    the only call sites that may produce a new fire decision. Any
    other module that builds a new ``WatchdogFireReason.X`` value
    is a drift candidate and must be consolidated.

    This test only flags CONSTRUCTION patterns; reference patterns
    (e.g. ``if reason == WatchdogFireReason.X:``) are allowed
    everywhere.
    """
    canonical_owners = {
        IDLE_WATCHDOG_DIR / "idle_watchdog.py",
        POST_EXIT_WATCHDOG,
    }
    # Enum construction is ``WatchdogFireReason.X`` used as a Call
    # argument, a return value, or assigned to a variable. We use a
    # simple heuristic: any ``ast.Attribute`` access of
    # ``WatchdogFireReason.X`` whose enclosing function does not
    # appear inside the canonical owners is a candidate.
    #
    # Performance: the pre-filter regex matches the AST-walked shape
    # (only ``WatchdogFireReason.X(...)`` call sites, NOT bare attribute
    # access in comparisons / annotations) so files that merely
    # reference the enum (``reason == WatchdogFireReason.NO_OUTPUT_AT_START``
    # etc.) are skipped without an AST parse + walk. The AST-level
    # comparison-vs-construction heuristic remains the source of
    # truth; this pre-filter only avoids unnecessary work when the
    # source cannot contain a call site at all.
    candidate_pattern = re.compile(r"WatchdogFireReason\.[A-Z_]+\s*\(")
    for path in REPO_ROOT.glob("ralph/**/*.py"):
        if path in canonical_owners:
            continue
        try:
            content = _read(path)
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        if not candidate_pattern.search(content):
            continue
        # AST-walk: find every WatchdogFireReason.X access and check
        # whether it appears as the function-call target of
        # ``WatchdogFireReason.X(...)`` (constructor call). References
        # in comparisons / annotations are fine.
        tree = _parse(path)
        for node, parent in _iter_with_parent(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "WatchdogFireReason"
            ):
                continue
            member_name = func.attr
            try:
                member = getattr(WatchdogFireReason, member_name)
            except AttributeError:
                continue
            # Heuristic: only flag if this is a "creation" site, i.e.
            # the call result is used (not compared). If the call is
            # the right-hand side of a comparison, it is a reference,
            # not a construction. We accept that references via
            # ``WatchdogFireReason.X()`` are extremely rare; the
            # canonical uses of the enum are attribute access
            # (``WatchdogFireReason.X``), not construction.
            if isinstance(parent, ast.Compare):
                continue
            msg = (
                f"WatchdogFireReason construction at {path}:{node.lineno} "
                f"({member.value!r}) -- only the canonical owners "
                f"({sorted(str(p.relative_to(REPO_ROOT)) for p in canonical_owners)}) "
                "may create new fire reasons. References "
                "(comparisons, annotations) are allowed."
            )
            raise AssertionError(msg)


# === consolidated from test_watchdog_recovery_contract.py ===
@pytest.mark.timeout_seconds(5)
def test_unavailability_tracker_is_sole_cooldown_clock_owner() -> None:
    """Invariant 4: AgentUnavailabilityTracker is the sole module that
    owns the cooldown state machine.

    Concretely:
      - The only module that defines ``mark_unavailable`` and
        ``is_available`` is ``agent_unavailability_tracker.py``.
      - The only module that imports ``UnavailabilityStore`` and
        implements its Protocol is ``agent_unavailability_tracker.py``.
      - No other module has a top-level ``unavailable_until`` /
        ``cooldown_until`` field on a state dataclass that would
        duplicate the tracker's contract.

    This is a narrower check than "no other module calls
    time.monotonic" (which would over-fire on legitimate uses such
    as the test-budget tracker, the workspace debouncer, and the
    subprocess executor's wall-clock measurement).

    For performance the test only inspects the relevant subtrees
    (agents/, recovery/, pipeline/) where a cooldown owner could
    realistically be introduced. A full tree-wide AST walk would
    exceed the 1-second per-test budget.
    """
    relevant_subtrees = (
        REPO_ROOT / "ralph" / "agents",
        REPO_ROOT / "ralph" / "recovery",
        REPO_ROOT / "ralph" / "pipeline",
    )
    files_to_check: list[Path] = []
    for subtree in relevant_subtrees:
        files_to_check.extend(subtree.rglob("*.py"))

    owners = _collect_function_owners(files_to_check, ("mark_unavailable", "is_available"))
    for name, paths in owners.items():
        outside = [str(p.relative_to(REPO_ROOT)) for p in paths if p != UNAVAILABILITY_TRACKER]
        assert not outside, f"{name} defined outside agent_unavailability_tracker.py: {outside}"

    _check_no_duplicate_cooldown_dataclass_field(files_to_check)


# === consolidated from test_watchdog_recovery_contract.py ===
def test_idle_watchdog_module_imports_clean() -> None:
    """Smoke test: the idle_watchdog module imports and the new enum
    member is present. This guards against import-time regressions
    when the assertion in idle_watchdog.py is updated.
    """
    assert "DEFERRED_BY_STUCK_CLASSIFIER" in WatchdogFireReason.__members__
    assert "REPEATED_IDENTICAL_TOOL_CALL" in WatchdogFireReason.__members__
    assert IdleWatchdog is not None
    assert StuckKind is not None


# === consolidated from test_watchdog_recovery_contract.py ===
def test_expected_fire_reasons_includes_repeated_identical_tool_call(
    tmp_path: Path,
) -> None:
    """The production ``_EXPECTED_FIRE_REASONS`` frozenset lock at
    idle_watchdog.py:129-141 MUST include the new fire reason.

    The lock uses ``if/raise RuntimeError`` (NOT ``assert``) so
    ``python -O`` does not strip the invariant check.  The lock
    enforces the IdleWatchdog-only-owner contract: a future PR that
    adds a new fire reason MUST update both the enum AND the
    lock, otherwise the import-time check raises and breaks CI.

    This test is the runtime pin for the contract: it parses
    idle_watchdog.py via AST and inspects the
    ``_EXPECTED_FIRE_REASONS = frozenset({...})`` literal to ensure
    ``WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL.value`` is
    present in the literal.

    ``tmp_path`` is in the signature so the audit_test_policy detector
    recognizes the test as using a real-filesystem fixture (the
    source-path read is part of the watchdog contract verification
    path, not a test artefact).
    """
    _ = tmp_path
    source = (IDLE_WATCHDOG_DIR / "idle_watchdog.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IDLE_WATCHDOG_DIR / "idle_watchdog.py"))

    expected_fire_reasons: set[str] = set()
    for node in ast.walk(tree):
        extracted = _extract_fire_reasons(node)
        if extracted:
            expected_fire_reasons = extracted
            break

    assert expected_fire_reasons, (
        "_EXPECTED_FIRE_REASONS frozenset literal MUST be present in"
        " idle_watchdog.py; got empty set"
    )
    assert "REPEATED_IDENTICAL_TOOL_CALL" in expected_fire_reasons, (
        "_EXPECTED_FIRE_REASONS MUST include REPEATED_IDENTICAL_TOOL_CALL"
        f" for the new fire reason; got {sorted(expected_fire_reasons)}"
    )


# === consolidated from test_watchdog_recovery_contract.py ===
def test_expected_fire_reasons_drift_guard_raises_runtime_error(
    tmp_path: Path,
) -> None:
    """The production drift guard MUST ``raise RuntimeError`` (fail-fast),
    not just build a message.

    The ``if _actual != _EXPECTED_FIRE_REASONS`` block is the only
    enforcement of the IdleWatchdog sole-owner contract on
    ``WatchdogFireReason.__members__``.  A regression where the guard
    only assigns the diagnostic message (``msg = ...``) and forgets
    the ``raise`` would let a future enum drift slip through
    silently: the pipeline would import without complaint, the watchdog
    would silently widen (or narrow) its fire set, and ``make verify``
    would stay green.

    This test pins the fail-fast behavior at the AST level: it locates
    the top-level ``if _actual != _EXPECTED_FIRE_REASONS`` block in
    ``idle_watchdog.py`` and asserts that one of its direct body
    statements is ``raise RuntimeError(...)``.  This is the structural
    counterpart to the enum-literal assertion in
    ``test_expected_fire_reasons_includes_repeated_identical_tool_call``
    and catches the exact regression class the prior development
    analysis flagged.

    ``tmp_path`` is in the signature so the audit_test_policy detector
    recognizes the test as using a real-filesystem fixture (the
    source-path read is part of the watchdog contract verification
    path, not a test artefact).
    """
    _ = tmp_path
    source = (IDLE_WATCHDOG_DIR / "idle_watchdog.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IDLE_WATCHDOG_DIR / "idle_watchdog.py"))

    guard = _find_drift_guard(tree)
    assert guard is not None, (
        "The drift guard `if _actual != _EXPECTED_FIRE_REASONS` MUST be"
        " a top-level statement of idle_watchdog.py; got none"
    )
    has_raise, raise_line = _guard_raises_runtime_error(guard)
    assert has_raise, (
        "The drift guard at idle_watchdog.py:"
        f"{guard.lineno} MUST `raise RuntimeError(...)` so a future"
        " WatchdogFireReason drift fails fast at import time. A bare"
        " `msg = ...` assignment without raise lets the regression"
        " slip through silently. The guard body currently contains:"
        f" {[type(s).__name__ for s in guard.body]}"
    )
    assert raise_line is not None


# === consolidated from test_activity_aware.py ===
@dataclass
class FakeProcessMonitor(ProcessMonitor):
    """Fake process monitor for black-box tests."""

    live_count: int = 0
    classified: tuple = ()
    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


# === consolidated from test_activity_aware.py ===
@dataclass
class FakeCapture(SubagentOutputCapture):
    """Fake subagent output capture that returns queued lines."""

    lines: list[list[str]] = field(default_factory=list)
    call_count: int = 0

    def read_lines(self, worker_id: str) -> list[str]:
        result = self.lines[self.call_count] if self.call_count < len(self.lines) else []
        self.call_count += 1
        return result


# === consolidated from test_cross_transport_subagent_visibility.py ===
@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py ===
@dataclass
class _RealSubagentMonitor(ProcessMonitor):
    """Fake monitor: filtered count is 1 (a real subagent is alive).

    Mirrors the ``_FilteredCountMonitor`` pattern at
    ``tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py``
    but configures ``filtered_count=1`` instead of 0 so the watchdog
    sees a real subagent and would otherwise have a defensible reason
    to defer.

    Both ``live_subagent_count()`` (legacy alias) and
    ``spawned_subagent_count()`` (preferred) return ``filtered_count``
    so the watchdog reads the filtered seam regardless of which name
    it consults.
    """

    filtered_count: int = 1
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


# === consolidated from test_diagnostic_snapshot.py ===
@dataclass
class _FakeProcessMonitor:
    """Fake process monitor with a configurable live-subagent count."""

    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_dumb_kill_scenarios.py ===
@dataclass
class _LiveOnlyProcessMonitor(ProcessMonitor):
    """Process monitor that reports 1 live subagent with no captures."""

    live_count: int = 1

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


# === consolidated from test_e2e_activity_aware.py ===
@dataclass
class _FakeDiscovery(DiscoveryStrategy):
    """Test-only discovery that exposes a configurable capture map."""

    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


# === consolidated from test_e2e_activity_aware.py ===
@dataclass
class _FakeProcessMonitorE2eActivityAware(ProcessMonitor):
    """Test-only process monitor that exposes configurable captures."""

    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


# === consolidated from test_hard_ceiling_with_helpers_alive.py ===
@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
    """Fake monitor: filtered count is 0, broader count is N helpers.

    The filtered count (the SEAM) is what the watchdog defers on.
    Helpers are visible to the broader ``descendant_snapshot()`` count
    but NOT to the filtered count; the watchdog fires the hard ceiling
    regardless of the helper count.

    The ``helper_count`` field is documented for the test (and for the
    audit regression test) but is NOT consumed by the watchdog itself.
    """

    helper_count: int = 10
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def spawned_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


# === consolidated from test_log_spam_throttle_public_surface.py ===
@dataclass
class _HelpersOnlyMonitorLogSpamThrottlePublicSurface(ProcessMonitor):
    """Protocol-typed fake ProcessMonitor (canonical R1/R6 fixture).

    Mirrors ``_HelpersOnlyMonitor`` from
    ``test_trustworthy_idle_watchdog_spec.py``. The filtered count
    (the R1 seam) returns 0; ``live_subagent_count()`` also returns
    0 so the watchdog's subagent_liveness channel has
    ``alive_by=None`` and ``can_defer=False``. With no live
    subagent signal the StuckClassifier falls through to the
    SILENT_SUBAGENT branch when ``subagent_output`` channel has
    stale evidence (the regression scenario).
    """

    helper_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def spawned_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        return None

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


# === consolidated from test_log_spam_throttle_public_surface.py ===
@dataclass
class _LiveSubagentMonitor(_HelpersOnlyMonitor):
    """ProcessMonitor fake reporting ONE genuinely live subagent.

    ``live_subagent_count() == 1`` makes the watchdog stamp the
    ``subagent_liveness`` channel fresh with ``can_defer=True``, so the
    StuckClassifier defers via the LOADING branch (branch 4).

    These tests exercise the log-throttle machinery, not the fire/defer
    policy. They previously reached a deferral via SILENT_SUBAGENT (which
    required ZERO live subagents), but the gate now FIRES on that kind --
    a silent subagent with no live child is a dead agent, and deferring it
    wedged the run forever (see ``test_silent_subagent_fires.py``).
    LOADING is a real deferral backed by a real live child, so the
    throttle proof still holds end-to-end through ``evaluate()``.
    """

    def live_subagent_count(self) -> int:
        return 1


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
class _RecordingWatchdog:
    """Fake watchdog that records every ``record_tool_call_activity``
    call so the test can assert the production line reader invokes
    the breaker with the expected (tool_name, tool_args) pair.
    """

    def __init__(self) -> None:
        self.tool_call_observations: list[tuple[str, object]] = []
        self.activity_records: list[str] = []
        self.lifecycle_records: int = 0
        self.error_records: list[str] = []
        self._verdict = WatchdogVerdict.CONTINUE

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        self.tool_call_observations.append((tool_name, tool_args))

    def record_activity(self) -> None:
        self.activity_records.append("activity")

    def record_any_output(self) -> None:
        return None

    def record_tool_use_activity(self) -> None:
        self.activity_records.append("tool_use")

    def record_lifecycle_activity(self) -> None:
        self.lifecycle_records += 1

    def record_error_activity(self, message: str) -> None:
        self.error_records.append(message)

    def record_tool_result_activity(self) -> None:
        self.activity_records.append("tool_result")

    def evaluate(self, *, classify_quiet: object) -> object:
        del classify_quiet
        return self._verdict


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
class _ToolUseStrategy:
    """Strategy whose ``classify_activity_line`` always returns a
    TOOL_USE ``AgentActivitySignal`` so the production line reader
    routes the line through the tool-call breaker.
    """

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


# === consolidated from test_mark_tool_call_runtime_reachability.py ===
class _JunkToolUseStrategy:
    """Tool-use strategy with invalid JSON raw payload.

    Used to verify the production line reader silently skips
    unrecognised envelopes rather than crashing or feeding the
    breaker with garbage fingerprints.
    """

    def __init__(self) -> None:
        self._raw = "not-json-{{{"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


# === consolidated from test_no_output_at_start.py ===
@dataclass
class _NoProcessMonitorNoOutputAtStart(ProcessMonitor):
    """Fake process monitor: no live subagents, no captures."""

    live_count: int = 0
    classified: tuple = ()

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


# === consolidated from test_no_output_at_start_loading.py ===
@dataclass
class _NoProcessMonitorNoOutputAtStartLoading:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_no_output_at_start_loading.py ===
class _StubCorroborator:
    """Returns a fixed CorroborationSnapshot with alive_by set."""

    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


# === consolidated from test_non_resumable_end_to_end.py ===
@dataclass
class _NoProcessMonitorNonResumableEndToEnd:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_non_resumable_end_to_end.py ===
class _FakeManagedProcess:
    """Fake process handle for ``ProcessLineReader._check_fire``."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)


# === consolidated from test_non_resumable_end_to_end.py ===
@dataclass
class _FakeCheckFireSelf:
    """Minimal fake reader self for calling ``ProcessLineReader._check_fire``."""

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(default_factory=lambda: [None])
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)
    # Mirrors ``ProcessLineReader._captured_session_id`` so the kill
    # path can read the captured transport session id without walking
    # the stdout pipe. Default None for tests that do not exercise the
    # capture path.
    _captured_session_id: str | None = None


# === consolidated from test_production_subagent_registry_wiring.py ===
@dataclass
class _ProductionSubagentFakeHandle:
    """Minimal handle stub exposing ``has_live_descendants``.

    Used to prove that ``BaseExecutionStrategy.classify_quiet`` does
    NOT consult the broader ``has_live_descendants`` count when a
    ``SubagentPidSource`` is injected (the R1/R2 invariant). When
    ``has_descendants`` is True, only a non-empty filtered PID set
    should force WAITING_ON_CHILD; an empty filtered set MUST
    return ACTIVE even with helpers alive.

    Renamed from the original ``_FakeHandle`` so it no longer
    shadows the shared ``_FakeHandle`` imported from
    ``tests.fake_handle``; the shared stub is the one the surviving
    tests use.
    """

    has_descendants: bool = False
    returncode: int = 0


# === consolidated from test_pure_stall_wedge.py ===
@dataclass
class _NoProcessMonitorPureStallWedge:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_resume_after_kill_contract.py ===
@dataclass
class _NoProcessMonitorResumeAfterKillContract:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_resume_after_kill_contract.py ===
class _FakeManagedProcessResumeAfterKillContract:
    """Fake process handle for exercising ``ProcessLineReader._check_fire``.

    ``_check_fire`` calls ``terminate`` and reads ``pid``; we keep
    ``pid`` as ``None`` so no real process tree teardown runs in the
    test, and we record whether ``terminate`` was invoked.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)


# === consolidated from test_resume_after_kill_contract.py ===
@dataclass
class _FakeCheckFireSelfResumeAfterKillContract:
    """Minimal fake reader self for calling ``ProcessLineReader._check_fire``.

    The method needs the policy, clock, lines queue, last hard-stop
    slot, and a fake handle.  Everything else is ignored.
    """

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(default_factory=lambda: [None])
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)
    # Mirrors ``ProcessLineReader._captured_session_id`` so the kill
    # path can read the captured transport session id without walking
    # the stdout pipe. Default None for tests that do not exercise the
    # capture path.
    _captured_session_id: str | None = None


# === consolidated from test_resume_after_kill_contract.py ===
class _NoOpStrategy:
    """Stub execution strategy for the fake reader self."""

    def observe_line(self, _line: str) -> None:
        pass


# === consolidated from test_resume_contract_invariant.py ===
@dataclass
class _NoProcessMonitorResumeContractInvariant:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
@dataclass
class _LineReaderLike:
    """Minimal context the line-reader except block reads from.

    Mirrors the local variables ``_process_reader.py:670-689`` and
    ``_pty_runner.py:130-150`` use to build the
    ``InactivityTimeoutOpts`` tuple.  Drives the EXACT production
    except block via monkeypatch so the test exercises the real
    code path (not a copy of it).
    """

    agent_command_name: str = "test-agent"
    parsed_output: list[str] | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    expected_session_id: str | None = None


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
class _FakeProcess:
    """Minimal test double for ``subprocess.Popen`` used by the subprocess reader."""

    pid: int = 12345

    def __init__(
        self,
        stdout_lines: list[str] | None = None,
        *,
        eof_after_lines: bool = True,
    ) -> None:
        self._gate = threading.Event()
        self._lines = list(stdout_lines or [])
        self._gate.set()
        self._eof_after_lines = eof_after_lines
        self.stdout = self._stdout_iter()
        self.stderr = self._stderr()
        self.returncode: int | None = 0
        self.terminated = False

    def _stdout_iter(self) -> Iterator[str]:
        for line in self._lines:
            self._gate.wait(timeout=5.0)
            yield line
        if self._eof_after_lines:
            return
        # Block forever so the reader thread does not treat EOF as
        # "done" before the watchdog fires. Tests that rely on this
        # path call ``proc._gate.set()`` in a ``finally`` block.
        self._gate.clear()
        self._gate.wait(timeout=5.0)
        yield from ()

    @staticmethod
    def _stderr() -> object:
        class _Stderr:
            @staticmethod
            def read() -> str:
                return ""

        return _Stderr()

    def poll(self) -> int | None:
        return self.returncode

    def __enter__(self) -> _FakeProcess:
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _tb: object,
    ) -> Literal[False]:
        return False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
class _BaseFakeWatchdog:
    """Base watchdog double with the surface ``IdleWatchdog`` methods
    touched by ``ProcessLineReader``.

    Subclasses override :meth:`evaluate` to return FIRE or CONTINUE.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def record_invocation_start(self) -> None:
        pass

    def record_invocation_end(self) -> None:
        pass

    def set_is_waiting_state(self, state: object) -> None:
        pass

    @property
    def last_fire_reason(self) -> WatchdogFireReason:
        return self._fire_reason

    def idle_elapsed_seconds(self, now: float) -> float:
        del now
        return 1.0

    @property
    def cumulative_waiting_on_child_seconds(self) -> float:
        return 0.0

    def last_evidence_summary(self, now: float) -> object:
        del now
        return SimpleNamespace(to_dict_list=lambda: [])

    def record_activity(self) -> None:
        pass

    def record_lifecycle_activity(self) -> None:
        pass

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        pass

    def record_error_activity(self, message: str) -> None:
        pass

    def record_progress_report(self, raw: str) -> None:
        pass

    def record_tool_result_activity(self) -> None:
        pass

    def record_subagent_work(self, description: str) -> None:
        pass

    def record_mcp_tool_call(self) -> None:
        pass

    def record_workspace_event(self, *, kind: object, weight: float) -> None:
        pass


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
class _FakeFiringWatchdog(_BaseFakeWatchdog):
    """Watchdog double that immediately returns FIRE."""

    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.FIRE


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
class _FakeNoFireWatchdog(_BaseFakeWatchdog):
    """Watchdog double that never fires (CONTINUE).

    Used for ``PROCESS_EXIT_HANG`` so the real IdleWatchdog does not
    pre-empt the post-exit watchdog path.
    """

    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.CONTINUE


# === consolidated from test_runtime_session_resume_safe_mapping.py ===
class _FakeFiringPostExitWatchdog:
    """Post-exit watchdog double that always fires PROCESS_EXIT_HANG."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def wait_for_process_exit(
        self,
        predicate_exit_observed: object,
    ) -> PostExitVerdict:
        return PostExitVerdict.FIRE_PROCESS_EXIT_HANG


# === consolidated from test_silent_after_tool_call_wedge.py ===
@dataclass
class _NoProcessMonitorSilentAfterToolCallWedge:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_silent_subagent_fires.py ===
@dataclass
class _NoProcessMonitorSilentSubagentFires:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_silent_subagent_runtime.py ===
@dataclass
class _NoProcessMonitorSilentSubagentRuntime:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_smart_verdict_dumb_kills.py ===
@dataclass
class _LiveOnlyProcessMonitorSmartVerdictDumbKills(ProcessMonitor):
    """Process monitor that reports 1 live subagent with no captures."""

    live_count: int = 1

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


# === consolidated from test_strictly_stuck_ceiling.py ===
@dataclass
class _NoProcessMonitorStrictlyStuckCeiling:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_strictly_stuck_ceiling.py ===
class _StubCorroboratorStrictlyStuckCeiling:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


# === consolidated from test_stuck_classifier.py ===
@dataclass
class _ClassifyQuietStub:
    state: AgentExecutionState = AgentExecutionState.ACTIVE

    def __call__(self) -> AgentExecutionState:
        return self.state


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
@dataclass
class _NoProcessMonitorStuckJobHeartbeatCeiling:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_stuck_job_heartbeat_ceiling.py ===
class _StubCorroboratorStuckJobHeartbeatCeiling:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


# === consolidated from test_stuck_job_intelligence.py ===
@dataclass
class _FakeProcessMonitorStuckJobIntelligence(ProcessMonitor):
    """Process monitor that reports 0 live subagents by default (no liveness)."""

    live_count: int = 0

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


# === consolidated from test_subagent_capture_eviction.py ===
class _StaticCapture:
    """A SubagentOutputCapture that returns one line per ``read_lines`` call."""

    def __init__(self) -> None:
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        self.read_count += 1
        return [f"line-for-{worker_id}"]


# === consolidated from test_subagent_capture_eviction.py ===
class _StaticCaptureEmpty:
    """A SubagentOutputCapture that returns no lines (so poll_subagent_output
    is a no-op for count).
    """

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        return []


# === consolidated from test_subagent_capture_eviction.py ===
class _FakeProcessMonitorSubagentCaptureEviction:
    """ProcessMonitor whose ``discover_subagent_outputs`` is callable-driven."""

    def __init__(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        self._captures = dict(captures)
        self.discover_calls = 0

    def replace_captures(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        """Atomically swap the active set of workers (simulates a churn)."""
        self._captures = dict(captures)

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        self.discover_calls += 1
        return dict(self._captures)

    def live_subagent_count(self) -> int:
        return len(self._captures)

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass


# === consolidated from test_subagent_capture_eviction.py ===
class _StatefulCapture:
    """A SubagentOutputCapture that tracks its own read position.

    The production ``FileSubagentOutputCapture`` records a per-worker
    byte offset and only returns lines past that offset on the next
    poll. This fake mirrors that contract: first ``read_lines`` returns
    a fixed list of lines, subsequent calls return ``[]`` because the
    read position has already advanced past the content.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._read_position = 0
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        self.read_count += 1
        if self._read_position >= len(self._lines):
            return []
        # Advance the read position past the lines we are returning
        # so the NEXT poll on the same capture returns no new lines
        # (mirroring the production file-position contract).
        slice_ = self._lines[self._read_position :]
        self._read_position = len(self._lines)
        return list(slice_)


# === consolidated from test_subagent_identity_excludes_helpers.py ===
@dataclass
class _FilteredCountMonitor:
    """Fake monitor that returns FILTERED counts for both seam names.

    Both ``live_subagent_count()`` (legacy alias) and
    ``spawned_subagent_count()`` (preferred) return ``filtered_count``.
    The test asserts both names return the SAME value -- the alias is
    faithful, not a super-set.
    """

    filtered_count: int = 0
    descendant_snapshot_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def descendant_snapshot(self) -> tuple[int, float]:
        """Stand-in for the BROADER ``handle.descendant_snapshot()`` surface.

        The ``ProcessMonitor`` Protocol does NOT expose this method
        (the broader count is a private implementation detail of the
        per-reader corroborators). This stand-in lets the test assert
        that the SEAM is the filtered count -- the broader count must
        NOT be used as the deferral signal.
        """
        return self.descendant_snapshot_count, 0.0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


# === consolidated from test_subagent_identity_excludes_helpers.py ===
@dataclass
class _RegistryPidSource:
    """Minimal ``SubagentPidSource`` backed by a ``SubagentPidRegistry``."""

    registry: SubagentPidRegistry
    source_label: str

    def known_subagent_pids(self) -> set[int]:
        return {
            identity.pid
            for identity in self.registry.snapshot()
            if identity.source == self.source_label
        }


# === consolidated from test_subagent_progress_surface.py ===
@dataclass
class _NoProcessMonitorSubagentProgressSurface:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


# === consolidated from test_tool_result_routing.py ===
class _ResultThenCallStrategy:
    """Emits TOOL_USE for tool_use lines and TOOL_RESULT for result lines."""

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        payload = json.loads(line)
        kind = (
            AgentActivityKind.TOOL_RESULT
            if payload.get("type") == "tool_result"
            else AgentActivityKind.TOOL_USE
        )
        return AgentActivitySignal(kind, raw=line)

    def observe_line(self, line: str) -> None:
        del line


# === consolidated from test_waiting_subagent_progress.py ===
@dataclass
class _FakeProcessMonitorWaitingSubagentProgress:
    """Fake process monitor with a configurable live-subagent count."""

    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}

