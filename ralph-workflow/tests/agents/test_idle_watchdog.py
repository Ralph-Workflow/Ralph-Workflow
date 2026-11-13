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
    CHANNEL_DEFERS_BY_DEFAULT,
    EXPECTED_FIRE_REASONS,
    KNOWN_TOOL_CALL_VERBS,
    MAX_SUBAGENT_REGISTRY_ENTRIES,
    AliveBy,
    ChannelEvidenceSummary,
    ChannelName,
    ClassifyStuckInputs,
    CorroborationSnapshot,
    EvidenceSummary,
    EvidenceTier,
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
    WatchLoopBase,
    WorkspaceChangeKind,
    classify_stuck,
    parse_tool_call_from_description,
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
    TerminationRecorder,
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
from ralph.agents.invoke.attempt_supervision import TerminationOutcome
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
    from ralph.agents.parsers import AgentParser

_IDLE_TIMEOUT = 0.1

_DRAIN_WINDOW = 0.0

_MAX_WAITING = 10.0

_ACTIVITY_TTL = 30.0

_REAL_PROGRESS_LINE = "[subagent] progress: phase=phase-1"

_REAL_HEARTBEAT_LINE = "[subagent] heartbeat"

_REAL_CHILD_JSON_LINE = '{"type": "child_progress", "child_id": "child-A", "phase": "phase-2"}'

_NON_AGY_TRANSPORTS: tuple[AgentTransport, ...] = tuple(
    transport for transport in AgentTransport if transport is not AgentTransport.AGY
)

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
        WatchdogFireReason.CONFLICT_INACTIVITY.value,
        WatchdogFireReason.OPERATOR_CAP_REACHED.value,
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
        WatchdogFireReason.CONFLICT_INACTIVITY,
        WatchdogFireReason.OPERATOR_CAP_REACHED,
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


def _activity_aware_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _activity_aware_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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
        "stuck_job_sub_ceiling_seconds": None,
        "silent_subagent_seconds": silent_subagent_seconds,
    }
    return TimeoutPolicy(**kwargs)


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


def _cross_transport_subagent_visib_make_watchdog() -> IdleWatchdog:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())


def _cumulative_waiting_ceiling_fir_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _cumulative_waiting_ceiling_fir_make_watchdog(
    *,
    clock: FakeClock,
    process_monitor: ProcessMonitor,
    policy: TimeoutPolicy,
    corroborator: Callable[[], CorroborationSnapshot] | None = None,
) -> IdleWatchdog:
    return IdleWatchdog(
        policy,
        clock,
        listener=None,
        corroborator=corroborator,
        process_monitor=process_monitor,
    )


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


def _dumb_kill_scenarios_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _dumb_kill_scenarios_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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


def _emit_info_log_throttle_make_watchdog(
    *,
    idle_timeout: float = 1.0,
    status_interval: float = 10.0,
    subagent_progress_interval: float = 3600.0,
    max_waiting: float = 600.0,
    listener: WaitingStatusListener | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
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
    return IdleWatchdog(policy, clock, listener), clock


def _emit_info_log_throttle_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _evidence_deferral_throttle_make_watchdog(
    *, throttle_seconds: float = 30.0
) -> tuple[IdleWatchdog, FakeClock]:
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


def _evidence_deferral_throttle_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _hard_ceiling_with_helpers_aliv_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _hard_ceiling_with_helpers_aliv_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _invocation_start_full_reset_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock), clock


def _log_spam_throttle_make_watchdog(
    throttle_seconds: float = 30.0,
) -> tuple[IdleWatchdog, FakeClock]:
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


def _no_output_at_start_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _no_output_at_start_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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
        silent_subagent_seconds=silent_subagent_seconds,
    )


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


def _no_output_at_start_lifecycle_p_make_policy(
    *, no_output_at_start_seconds: float = 30.0
) -> TimeoutPolicy:
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


def _no_output_at_start_lifecycle_p_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _no_output_at_start_loading_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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


def _non_resumable_end_to_end_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _non_resumable_end_to_end_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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
    listener: WaitingStatusListener | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
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
    return IdleWatchdog(config, clock, listener, corroborator=corroborator), clock


def _os_descendant_only_escalation_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _pure_stall_wedge_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _pure_stall_wedge_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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


def _resume_after_kill_contract_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _resume_contract_invariant_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _resume_contract_invariant_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(
        policy, clock, process_monitor=_NoProcessMonitorResumeContractInvariant()
    ), clock


def _silent_after_tool_call_wedge_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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


def _silent_subagent_runtime_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _smart_verdict_dumb_kills_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _smart_verdict_dumb_kills_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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


def _stall_lifetime_watchdog(
    clock: FakeClock,
    events: list[WaitingStatusEvent],
) -> IdleWatchdog:
    return IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=60.0),
        clock,
        listener=events.append,
    )


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
    clock = FakeClock(start=0.0)
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


def _strictly_stuck_ceiling_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _strictly_stuck_ceiling_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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


def _stuck_job_heartbeat_ceiling_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _stuck_job_heartbeat_ceiling_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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
    if (
        no_progress_quiet_heartbeat_ceiling_seconds is None
        and no_progress_quiet_seconds is not None
    ):
        no_progress_quiet_heartbeat_ceiling_seconds = no_progress_quiet_seconds
    if (
        no_progress_ceiling is not None
        and no_output_at_start is not None
        and no_output_at_start >= no_progress_ceiling
    ):
        no_output_at_start = None
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
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


def _stuck_job_sub_ceiling_waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _subagent_capture_eviction_make_watchdog(
    monitor: _FakeProcessMonitorSubagentCaptureEviction,
) -> tuple[IdleWatchdog, FakeClock]:
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


def _subagent_progress_surface_make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(
        policy, clock, process_monitor=_NoProcessMonitorSubagentProgressSurface()
    ), clock


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


def _waiting_subagent_progress_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


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
        _input_prompt=None,
    )
    return clock, watchdog, MethodType(ProcessLineReader._record_line_activity, reader)


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


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


def _bind_subagent_sink_to_watchdog(
    watchdog: IdleWatchdog,
) -> tuple[object, object]:

    def _mcp_sink(_tool_name: str) -> None:
        watchdog.record_mcp_tool_call()

    def _subagent_sink(line: str) -> None:
        watchdog.record_subagent_work(description=line)

    sink_token = set_active_sink(_mcp_sink)
    subagent_token = set_subagent_sink(_subagent_sink)
    return (sink_token, subagent_token)


def _reset_sink_tokens(tokens: tuple[object, object]) -> None:
    sink_token, subagent_token = tokens
    reset_active_sink(sink_token)
    reset_subagent_sink(subagent_token)


def _parse_tool_call_expected(description: str | None) -> str | None:
    if not description:
        return None
    head, sep, _tail = description.partition(":")
    if not sep:
        return None
    if head not in _KNOWN_TOOL_CALL_VERBS_FOR_TEST:
        return None
    return head


def _force_classify_stuck_kind(
    watchdog: IdleWatchdog,
    kind: StuckKind,
) -> None:
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


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


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


def _info_log_records(records: list[str]) -> list[str]:
    return [r for r in records if _INFO_LOG_SUBSTRING in r]


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


def _remove_sink(handler_id: int) -> None:
    logger.remove(handler_id)


def _populate_per_invocation_state(
    watchdog: IdleWatchdog,
    clock: FakeClock,
) -> None:
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
    watchdog.record_process_liveness(is_alive=False)
    clock.advance(40.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict.name in {"FIRE", "CONTINUE"}


def _per_invocation_fields() -> dict[str, object]:
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
        "_process_exit_observed_at": None,
    }


def _patch_classifier_to_deferring_kind(watchdog: IdleWatchdog) -> None:

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.LOADING

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)


def _stale_subagent_corroborator() -> CorroborationSnapshot:
    return CorroborationSnapshot(
        scoped_child_active=True,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )


def _build_deferred_fire_watchdog(
    *,
    listener: Callable[[WaitingStatusEvent], None] | None,
    clock: FakeClock,
    silent_subagent_seconds: float = 1.0,
    stuck_job_sub_ceiling_seconds: float = 5.0,
    max_waiting_on_child_seconds: float = 10_000.0,
    watchdog_log_throttle_seconds: float = 30.0,
) -> IdleWatchdog:
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        no_progress_quiet_seconds=None,
        no_progress_quiet_strictly_stuck_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
        activity_evidence_ttl_seconds=10_000.0,
        silent_subagent_seconds=silent_subagent_seconds,
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


def _build_pty_reader_with_strategy(strategy: object) -> PtyLineReader:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = SimpleNamespace(
        master_fd=master_fd,
        poll=lambda: None,
        terminate=lambda grace_period_s=None, **_kwargs: None,
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
        os.close(master_fd)
    return reader


def _claude_assistant_tool_use_line(tool_input: dict[str, object]) -> str:
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


def _make_watchdog_with_corroborator(
    corroborator: WaitingCorroborator,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(
            _no_output_at_start_lifecycle_p_make_policy(), clock, corroborator=corroborator
        ),
        clock,
    )


def _make_watchdog_for_waiting_fire() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=15.0,
        stuck_job_sub_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        waiting_status_interval_seconds=100.0,
    )
    return IdleWatchdog(
        policy, clock, process_monitor=_NoProcessMonitorNonResumableEndToEnd()
    ), clock


def _make_watchdog_for_session_ceiling() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=5.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        max_session_seconds=10.0,
    )
    return IdleWatchdog(
        policy, clock, process_monitor=_NoProcessMonitorNonResumableEndToEnd()
    ), clock


def _fire_in_stream_reason(
    reason: WatchdogFireReason,
) -> tuple[list[str], _IdleStreamTimeoutError]:
    if reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
        watchdog, clock = _make_watchdog_for_session_ceiling()
        watchdog.record_invocation_start()
        clock.advance(11.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_active)
    else:
        watchdog, clock = _make_watchdog_for_waiting_fire()
        watchdog.record_invocation_start()
        clock.advance(6.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_waiting_on_child)
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(15.0)
        verdict = watchdog.evaluate(classify_quiet=_non_resumable_end_to_end_waiting_on_child)

    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE for {reason}; got {verdict}"
    assert watchdog.last_fire_reason == reason

    fake_self = _FakeCheckFireSelf(_policy=watchdog.config, _clock=clock)
    result = ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == reason
    return pending_lines, wrapper


def _fire_process_exit_hang() -> _IdleStreamTimeoutError:
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


def _fire_descendant_hang() -> _IdleStreamTimeoutError:
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


def _convert_reason_to_agent_error(
    wrapper: _IdleStreamTimeoutError,
    pending_lines: tuple[str, ...] | list[str] = (),
) -> AgentInactivityTimeoutError:
    return _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=pending_lines,
        explicit_completion_seen=False,
        captured_session_id=None,
        expected_session_id="prior-session-abc",
    )


def _resolve_recovery_session_id_for_test(exc: AgentInactivityTimeoutError) -> str | None:
    if _failure_requires_fresh_session(exc, AgentInactivityTimeoutError):
        return None
    return getattr(exc, "resumable_session_id", None) or None


def _assert_non_resumable_recovery_chain(exc: AgentInactivityTimeoutError) -> None:
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


def _frame(event_type: str) -> str:
    return json.dumps(
        {
            "type": event_type,
            "timestamp": 1785133506972,
            "sessionID": "ses_1",
            "part": {"id": "prt_1", "type": event_type.replace("_", "-")},
        }
    )


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


def _opencode_tool_line(
    tool: str,
    tool_input: dict[str, object],
    *,
    call_id: str = "call_1",
    status: str = "completed",
    include_part_type: bool = True,
) -> str:
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


def _make_os_descendant_only_corroborator() -> WaitingCorroborator:
    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


def _active_strategy_with_source(
    source: SubagentPidSource,
) -> BaseExecutionStrategy:
    return BaseExecutionStrategy(subagent_pid_source=source)


def _tracker(clock: FakeClock) -> RepetitionTracker:
    return RepetitionTracker(
        clock,
        consecutive_threshold=5,
        window_count=8,
        window_seconds=600.0,
    )


def _make_pipeline_state(
    *,
    chain_agents: tuple[str, ...] = ("agent-a",),
    retries: int = 0,
    connectivity_state: str | None = "online",
) -> PipelineState:
    chain = AgentChainState(agents=list(chain_agents), current_index=0, retries=retries)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain},
    )
    if connectivity_state is not None:
        state = state.copy_with(last_connectivity_state=connectivity_state)
    return state


def _raise_like_process_reader(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
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


def _raise_like_pty_runner(
    ctx: _LineReaderLike,
    *,
    timeout_seconds: float,
    reason: WatchdogFireReason,
    diagnostic: dict[str, object] | None = None,
) -> AgentInactivityTimeoutError:
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


def _make_fake_watchdog_class(fire_reason: WatchdogFireReason) -> type[_BaseFakeWatchdog]:

    class _Cls(_FakeFiringWatchdog):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self._fire_reason = fire_reason

    return _Cls


def _drive_invoke_agent_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> AgentInactivityTimeoutError:
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


def _wedge(watchdog: IdleWatchdog, clock: FakeClock, silent_for: float) -> float:
    watchdog.record_invocation_start()
    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(silent_for)
    return clock.monotonic()


def _events(captured: list[WaitingStatusEvent]) -> list[WaitingStatusEvent]:
    return captured


def _stall_state(watchdog: IdleWatchdog) -> bool:
    return bool(watchdog.is_stalled)


def _classifier_to_stuck_now(
    watchdog: IdleWatchdog,
    *,
    reason: WatchdogFireReason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
) -> None:
    _attr = "_classify_stuck_now"

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.STUCK

    setattr(watchdog, _attr, _stuck_now)


def _fresh_progress_corroborator() -> WaitingCorroborator:

    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


def _summary_with_channel(
    *,
    channel: ChannelName,
    last_at: float | None,
    can_defer: bool = True,
) -> EvidenceSummary:
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


def _multi_summary(
    *,
    subagent_output_at: float | None = None,
    subagent_liveness_at: float | None = None,
    alive_by: AliveBy | None = None,
) -> EvidenceSummary:
    channels: list[ChannelEvidenceSummary] = []
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
    sub_liv_age = None if subagent_liveness_at is None else max(0.0, _NOW - subagent_liveness_at)
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


def _make_stuck_corroborator(
    alive_by: AliveBy = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
) -> Callable[[], CorroborationSnapshot]:

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=200.0,
        )

    return _corroborator


def _probe_cache_cap() -> int:
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog.subagent_output_captures)


def _probe_tombstone_cap() -> int:
    captures = {f"probe-{i}": _StaticCaptureEmpty() for i in range(_PROBE_WORKER_COUNT)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    return len(watchdog.evicted_worker_tombstones)


def _reader() -> object:
    return SimpleNamespace(
        _strategy=_ResultThenCallStrategy(),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        _input_prompt=None,
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@functools.cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _walk(tree: ast.AST) -> list[ast.AST]:
    return list(ast.walk(tree))


def _iter_with_parent(tree: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    parent_map: dict[int, ast.AST] = {}
    for node in _walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return [(node, parent_map.get(id(node))) for node in _walk(tree)]


def _function_bodies(tree: ast.Module, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        n
        for n in _walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]


def _collect_function_owners(
    files_to_check: list[Path],
    target_names: tuple[str, ...],
) -> dict[str, list[Path]]:
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


def _check_no_duplicate_cooldown_dataclass_field(
    files_to_check: list[Path],
) -> None:
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


def _extract_fire_reasons(node: ast.AST) -> set[str]:
    target_name: str | None = None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target_name = node.target.id
    elif (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        target_name = node.targets[0].id
    if target_name != "EXPECTED_FIRE_REASONS":
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


def _find_drift_guard(tree: ast.Module) -> ast.If | None:
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
            and test.comparators[0].id == "EXPECTED_FIRE_REASONS"
        ):
            continue
        return node
    return None


def _guard_raises_runtime_error(guard: ast.If) -> tuple[bool, int | None]:
    for stmt in guard.body:
        if not isinstance(stmt, ast.Raise):
            continue
        exc = stmt.exc
        if not isinstance(exc, ast.Call):
            continue
        if isinstance(exc.func, ast.Name) and exc.func.id == "RuntimeError":
            return True, stmt.lineno
    return False, None


@pytest.fixture
def captured_info_records() -> tuple[io.StringIO, list[str]]:
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


@pytest.fixture
def captured_debug_records() -> tuple[io.StringIO, list[str]]:
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


@pytest.fixture
def captured_log_records() -> tuple[io.StringIO, list[str]]:
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


def test_first_party_mcp_tool_defers_no_output_deadline() -> None:
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


def test_first_party_subagent_output_defers_no_output_deadline() -> None:
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
    assert wd.subagent_output_count == 1

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_first_party_subagent_progress_defers_no_output_deadline() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE


def test_dead_subagent_detected_within_idle_window() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    wd.record_activity()
    wd.record_subagent_work()
    clock.advance(31.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_truly_idle_fires_on_time() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_side_channel_workspace_source_defers_log_does_not() -> None:
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


def test_bare_subagent_liveness_defers_fire() -> None:
    monitor = FakeProcessMonitor(live_count=1)
    wd, clock = _activity_aware_make_watchdog(
        _activity_aware_make_policy(activity_ttl=1000.0),
        process_monitor=monitor,
    )
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE


def test_session_ceiling_unaffected_by_first_party_activity() -> None:
    wd, clock = _activity_aware_make_watchdog(
        _activity_aware_make_policy(max_session=5.0, activity_ttl=1000.0)
    )
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_cumulative_waiting_ceiling_unaffected_by_activity() -> None:
    wd, clock = _activity_aware_make_watchdog(
        _activity_aware_make_policy(idle_timeout=0.1, max_waiting=2.0, activity_ttl=1000.0)
    )
    wd.record_activity()
    clock.advance(0.1)

    fire_observed = False
    for _ in range(30):
        wd.record_mcp_tool_call()
        verdict = wd.evaluate(classify_quiet=_activity_aware_waiting)
        clock.advance(0.1)
        if verdict == WatchdogVerdict.FIRE:
            fire_observed = True
            break

    assert fire_observed, (
        "cumulative ceiling MUST fire unconditionally past the"
        " ceiling (R3 hard enforcement) regardless of mcp_tool"
        " freshness; never observed FIRE in 30 calls"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_evidence_summary_labels_tiers() -> None:
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


def test_process_monitor_disabled_gracefully() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    summary = wd.last_evidence_summary(clock.monotonic())
    liveness = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert liveness is not None
    assert liveness.last_at is None
    assert liveness.can_defer is False


def test_subagent_output_unavailable_when_no_process_monitor() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    assert wd.poll_subagent_output() == 0
    output = wd.last_evidence_summary(clock.monotonic()).by_name(ChannelName.SUBAGENT_OUTPUT)
    assert output is not None
    assert output.last_at is None


def test_fire_diagnostic_includes_evidence_summary() -> None:
    wd, clock = _activity_aware_make_watchdog(_activity_aware_make_policy())
    clock.advance(1.0)
    wd.evaluate(classify_quiet=_activity_aware_active)
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    summary = wd.last_evidence_summary(clock.monotonic())
    assert len(summary.channels) == 5
    assert all(
        c.tier in {EvidenceTier.FIRST_PARTY, EvidenceTier.SIDE_CHANNEL} for c in summary.channels
    )


def test_mcp_timeout_storm_with_varying_args_trips_the_error_dimension() -> None:
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


def test_repeated_failing_command_with_varying_text_trips_the_tool_dimension() -> None:
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


def test_successful_calls_feed_no_error_dimension() -> None:
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


def test_interactive_tool_use_carries_its_arguments() -> None:
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line(_transcript_line("git status --short"))

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "Bash",
        {"command": "git status --short"},
    )


def test_interactive_distinct_commands_do_not_trip_the_breaker() -> None:
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


def test_interactive_marker_without_metadata_still_falls_back() -> None:
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line("claude tool: read_file\n")

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == ("read_file", {})


def test_pi_session_event_id_is_preserved_for_clean_exit_recovery(tmp_path: Path) -> None:
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


def test_captured_session_id_preserved(tmp_path: Path) -> None:
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


def test_legacy_extractor_recovers_from_bounded_output(tmp_path: Path) -> None:
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


def test_pty_visible_tui_recovers_from_ansi_wrapped_line(tmp_path: Path) -> None:
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


def test_no_session_id_in_bounded_output_still_raises_none_id(tmp_path: Path) -> None:
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


def test_opencode_discovery_strategy_is_registry_backed_with_registry() -> None:
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


def test_opencode_discovery_strategy_is_null_without_registry() -> None:
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


def test_opencode_surfaces_real_extracted_progress_via_registry() -> None:
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
    assert watchdog.subagent_output_count >= 1

    clock.advance(0.01)
    registry.record_progress("child-A", phase="phase-2")
    fresh2 = watchdog.poll_subagent_output(now=clock.monotonic())
    assert fresh2 >= 1
    assert watchdog.subagent_output_count >= 2


def test_opencode_capture_lines_consumable_by_record_subagent_work() -> None:
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


def _assert_transport_strategy_surfaces_real_extracted_progress(transport: AgentTransport) -> None:
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
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" progress line; got {snapshot['subagent_progress_count']}"
        )
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real progress line; got {last_activity!r}"
        )
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


def test_transport_strategy_surfaces_real_extracted_progress_to_watchdog() -> None:

    for transport in _NON_AGY_TRANSPORTS:
        _assert_transport_strategy_surfaces_real_extracted_progress(transport)


@pytest.mark.parametrize("transport", [AgentTransport.AGY], ids=["agy"])
def test_transport_strategy_surfaces_real_extracted_progress_to_watchdog_agy(
    transport: AgentTransport,
) -> None:
    _assert_transport_strategy_surfaces_real_extracted_progress(transport)


def test_transport_strategy_surfaces_real_extraction() -> None:
    for real_line in (_REAL_HEARTBEAT_LINE, _REAL_CHILD_JSON_LINE):
        watchdog = _cross_transport_subagent_visib_make_watchdog()
        tokens = _bind_subagent_sink_to_watchdog(watchdog)
        try:
            watchdog.record_invocation_start()
            assert watchdog.last_subagent_progress_description is None

            strategy = strategy_for_transport(AgentTransport.CLAUDE, registry=_make_registry())
            strategy.observe_line(real_line)

            assert watchdog.last_subagent_progress_description == real_line, (
                "watchdog did not capture the real extracted line; got "
                f"{watchdog.last_subagent_progress_description!r}"
            )
            snapshot = watchdog.diagnostic_snapshot(now=0.0)
            assert snapshot["subagent_progress_count"] >= 1, (
                "diagnostic_snapshot MUST report subagent_progress_count >= 1 "
                f"after a real line; got {snapshot['subagent_progress_count']}"
            )
            last_activity = snapshot["last_subagent_progress_at"]
            assert (
                last_activity is not None
                and isinstance(last_activity, float)
                and last_activity >= 0.0
            ), (
                "diagnostic_snapshot MUST report last_subagent_progress_at as a "
                f"non-None float >= 0.0 after a real line; got {last_activity!r}"
            )
            assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(real_line), (
                "diagnostic_snapshot MUST report current_subagent_tool_call matching the"
                " parser output for the description; got"
                f" {snapshot['current_subagent_tool_call']!r}"
            )
        finally:
            _reset_sink_tokens(tokens)


def test_transport_strategy_surfaces_real_extraction_to_listener() -> None:
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        captured: list[WaitingStatusEvent] = []

        def _listener(event: WaitingStatusEvent) -> None:
            captured.append(event)

        watchdog.record_invocation_start()
        watchdog.register_default_subagent_activity_listener(_listener)

        strategy = strategy_for_transport(AgentTransport.CLAUDE, registry=_make_registry())
        strategy.observe_line(_REAL_PROGRESS_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_PROGRESS_LINE

        clock = watchdog.clock
        assert isinstance(clock, FakeClock)
        clock.advance(61.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        watchdog.evaluate(classify_quiet=_waiting)
        assert captured, (
            "watchdog MUST emit a waiting status event with subagent_activity after"
            " evaluate() transitions into WAITING_ON_CHILD"
        )
        latest = captured[-1]
        assert latest.subagent_activity == _REAL_PROGRESS_LINE, (
            f"listener did not receive real extracted progress; got {latest.subagent_activity!r}"
        )
        assert (
            latest.last_subagent_progress_at is not None
            and isinstance(latest.last_subagent_progress_at, float)
            and latest.last_subagent_progress_at >= 0.0
        ), (
            "WaitingStatusEvent MUST carry last_subagent_progress_at as a non-None"
            " float >= 0.0 after a real progress line; got"
            f" {latest.last_subagent_progress_at!r}"
        )
        assert latest.current_subagent_tool_call == _parse_tool_call_expected(
            _REAL_PROGRESS_LINE
        ), (
            "WaitingStatusEvent MUST carry current_subagent_tool_call matching the"
            " parser output for the observed description; got"
            f" {latest.current_subagent_tool_call!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


def test_cross_transport_subagent_activity_sink_is_wired() -> None:
    watchdog = _cross_transport_subagent_visib_make_watchdog()
    captured: list[tuple[str, str]] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append((event.kind.value, event.subagent_activity or ""))

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="first")
    clock = watchdog.clock
    assert isinstance(clock, FakeClock)
    clock.advance(61.0)

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    watchdog.evaluate(classify_quiet=_waiting)
    assert captured, (
        "watchdog.evaluate MUST emit at least one waiting-status event"
        " carrying the recorded subagent description; got no events"
    )
    assert all(description == "first" for _kind, description in captured), (
        "Every waiting-status event forwarded to the listener MUST"
        " carry the recorded subagent description; got: {captured}"
    )

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

    reset_snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert reset_snapshot["last_subagent_progress_at"] is None
    assert reset_snapshot["current_subagent_tool_call"] is None


def test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        max_waiting_on_child_seconds=10.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
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
    _force_classify_stuck_kind(watchdog, StuckKind.SILENT_SUBAGENT)
    watchdog.record_invocation_start()
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(
        classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child
    )
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(
        classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child
    )
    assert pre_ceiling_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"pre-ceiling evaluate() MUST defer, got {pre_ceiling_verdict!r}"
    )
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


def test_cumulative_ceiling_fires_when_classify_stuck_returns_loading() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        max_waiting_on_child_seconds=10.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
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
    _force_classify_stuck_kind(watchdog, StuckKind.LOADING)
    watchdog.record_invocation_start()
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(
        classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child
    )
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(
        classify_quiet=_cumulative_waiting_ceiling_fir_waiting_on_child
    )
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


def test_different_shell_commands_stay_distinct() -> None:
    first = extract_tool_call_from_activity_signal(_shell_call_line("ls -la", call_id="t_1"))
    second = extract_tool_call_from_activity_signal(_shell_call_line("pwd", call_id="t_2"))

    assert first != second
    assert first == (
        "shellToolCall",
        {"command": "ls -la", "workingDirectory": "/repo", "timeout": 30000},
    )


def test_diagnostic_snapshot_has_all_required_keys() -> None:
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


def test_diagnostic_snapshot_is_json_serializable() -> None:
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    encoded = json.dumps(snapshot)
    assert isinstance(encoded, str)
    decoded = json.loads(encoded)
    assert decoded["last_fire_reason"] is None
    assert decoded["last_deferred_kind"] is None


def test_diagnostic_snapshot_reflects_record_subagent_work() -> None:
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
    assert snapshot["idle_elapsed_seconds"] == 5.0, (
        f"snapshot.idle_elapsed_seconds MUST be 5.0; got {snapshot['idle_elapsed_seconds']}"
    )


def test_diagnostic_snapshot_live_subagent_count() -> None:
    watchdog, _clock = _diagnostic_snapshot_make_watchdog(monitor_count=3)
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert snapshot["live_subagent_count"] == 3, (
        f"snapshot.live_subagent_count MUST be 3; got {snapshot['live_subagent_count']}"
    )


def test_diagnostic_snapshot_evidence_summary_has_channels() -> None:
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    summary = snapshot["evidence_summary"]
    assert isinstance(summary, list), (
        f"snapshot.evidence_summary MUST be a list; got {type(summary)}"
    )
    assert len(summary) == 5, f"snapshot.evidence_summary MUST have 5 entries; got {len(summary)}"


def test_diagnostic_snapshot_after_record_invocation_start_resets() -> None:
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_subagent_work(description="reading source.py")
    populated = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert populated["last_subagent_progress_description"] == "reading source.py"
    assert populated["subagent_progress_count"] == 1
    watchdog.record_invocation_start()
    clock.advance(2.0)
    reset = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert reset["last_subagent_progress_description"] is None, (
        f"record_invocation_start MUST reset"
        f" last_subagent_progress_description; got"
        f" {reset['last_subagent_progress_description']!r}"
    )


def test_diagnostic_snapshot_is_pure_read_no_side_effects() -> None:
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot_a = watchdog.diagnostic_snapshot(now=clock.monotonic())
    snapshot_b = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot_a == snapshot_b, (
        f"diagnostic_snapshot MUST be a pure read; got {snapshot_a} vs {snapshot_b}"
    )


def test_diagnostic_snapshot_is_method_not_coroutine() -> None:
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    assert not inspect.iscoroutinefunction(watchdog.diagnostic_snapshot), (
        "diagnostic_snapshot MUST be a synchronous method"
    )


def test_diagnostic_snapshot_uses_injected_now_argument() -> None:
    watchdog, clock = _diagnostic_snapshot_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(5.0)
    snapshot = watchdog.diagnostic_snapshot(now=42.5)
    assert snapshot["idle_elapsed_seconds"] == 42.5, (
        f"snapshot.idle_elapsed_seconds MUST use injected now; got"
        f" {snapshot['idle_elapsed_seconds']}"
    )


def test_diagnostic_snapshot_records_fire_reason() -> None:
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
    assert verdict.name == "FIRE", (
        f"watchdog.evaluate MUST fire NO_OUTPUT_AT_START after the"
        f" threshold with no activity; got verdict={verdict}"
    )
    snapshot = watchdog.diagnostic_snapshot(now=clock.monotonic())
    assert snapshot["last_fire_reason"] == "no_output_at_start", (
        f"snapshot.last_fire_reason MUST be 'no_output_at_start'; got"
        f" {snapshot['last_fire_reason']!r}"
    )


def test_diagnostic_snapshot_resumable_session_id_is_always_none() -> None:
    watchdog, _clock = _diagnostic_snapshot_make_watchdog()
    snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert "resumable_session_id" in snapshot, (
        "diagnostic_snapshot MUST keep resumable_session_id as a stable key"
    )
    assert snapshot["resumable_session_id"] is None, (
        f"diagnostic_snapshot.resumable_session_id MUST be None"
        f" (the watchdog itself does not populate the field); got"
        f" {snapshot['resumable_session_id']!r}"
    )


def test_diagnostic_snapshot_resumable_session_id_remains_none_after_fire() -> None:
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


def test_resumable_session_id_contract_documented_in_spec() -> None:
    spec_path = (
        Path(__file__).resolve().parent.parent.parent / "docs" / "agents" / "watchdog-spec.md"
    )
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "merged_diag" in spec_text, (
        "watchdog-spec.md MUST document that resumable_session_id"
        " lives on the OUTER merged_diag payload"
    )
    assert "IdleWatchdogKilledError" in spec_text, (
        "watchdog-spec.md MUST document that resumable_session_id"
        " also lives on the typed IdleWatchdogKilledError attribute"
    )
    assert "diagnostic_snapshot" in spec_text, (
        "watchdog-spec.md MUST reference diagnostic_snapshot in the"
        " R4 resumable_session_id location contract"
    )


def test_dumb_kill_agent_reading_product_criteria_with_subagent_progress() -> None:
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

    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_dumb_kill_first_output_fragment_with_live_subagent() -> None:
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

    wd.record_mcp_tool_call()

    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_dumb_kill_repeated_evaluate_with_progress_does_not_drift_to_fire() -> None:
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

    fire_observed = False
    for _ in range(50):
        clock.advance(6.0)
        wd.record_subagent_work()
        verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
        if verdict == WatchdogVerdict.FIRE:
            fire_observed = True
            break

    assert fire_observed, (
        "cumulative ceiling MUST fire at the effective ceiling"
        " (R3 hard enforcement); never observed FIRE in 50 calls"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_dumb_kill_recovery_controller_never_advances_to_failed_on_unavailable() -> None:
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
    assert chain.current_index != 0, (
        f"chain must advance past the unavailable agent, got current_index={chain.current_index}"
    )
    assert not controller.unavailability_store.is_available("development", "claude"), (
        "claude must be on cooldown after no_progress_quiet typed cause"
    )


def test_dumb_kill_three_agent_dispatching_parallel_scouts() -> None:
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

    clock.advance(151.0)

    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE at idle_elapsed=151s invocation_elapsed=194s"
        f" (the user's exact log scenario), got {verdict}"
    )


def test_no_progress_quiet_does_not_fire_within_dumb_kill_floor() -> None:

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

    clock.advance(119.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must not FIRE at invocation_elapsed=119s (under 120s floor), got {verdict}"
    )


def test_no_progress_quiet_still_fires_after_dumb_kill_floor_when_genuinely_stuck() -> None:

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

    clock.advance(150.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"watchdog must FIRE when the agent is genuinely stuck past"
        f" the dumb-kill floor + ceiling, got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


def test_no_progress_quiet_does_not_fire_when_corroborator_reports_live_child() -> None:

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

    clock.advance(151.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire NO_PROGRESS_QUIET when the corroborator"
        f" reports a live child past the no_progress_quiet ceiling, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None (NO fire happened -- the gate"
        f" refinement deferred), got {wd.last_fire_reason}"
    )


def test_cumulative_ceiling_remains_upper_bound_for_live_child_stalls() -> None:

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

    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"watchdog must enter WAITING_ON_CHILD at idle_elapsed=11s, got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None on entry to WAITING_ON_CHILD, got {wd.last_fire_reason}"
    )

    for _ in range(29):
        clock.advance(10.0)
        verdict = wd.evaluate(classify_quiet=_dumb_kill_scenarios_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"watchdog must NOT fire at cumulative=290s of waiting (under"
        f" the 300s os_descendant_only ceiling), got {verdict}"
    )
    assert wd.last_fire_reason is None, (
        f"last_fire_reason must be None at cumulative=290s, got {wd.last_fire_reason}"
    )

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
    original_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    try:
        assert discovery.discover_subagent_outputs(0) == {}
    finally:
        os.chdir(str(original_cwd))


def test_subagent_output_first_party_deferral(tmp_path: Path) -> None:
    log_file = tmp_path / ".agent" / "workers" / "w1" / "output.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line 1\n", encoding="utf-8")

    policy = _activity_aware_make_policy(activity_ttl=1000.0)
    monitor = _FakeProcessMonitorE2eActivityAware(
        captures={"w1": FileSubagentOutputCapture(str(log_file))}
    )
    wd, clock = _activity_aware_make_watchdog(policy, process_monitor=monitor)
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd.subagent_output_count >= 1

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_activity_aware_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
@pytest.mark.timeout_seconds(5)
def test_process_monitor_discovers_and_classifies_subagent() -> None:
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
        for proc in [host_proc, *children]:
            with contextlib.suppress(psutil.Error):
                proc.kill()
        host.wait(timeout=0.5)


def test_emit_info_log_fires_at_most_once_per_status_interval_when_no_subagent_listener(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_info_records
    captured_events: list[WaitingStatusEvent] = []
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
        listener=captured_events.append,
    )

    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    assert watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting) is not None

    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    info_records = _info_log_records(records)
    max_emissions = 4
    assert len(info_records) == max_emissions, (
        f"INFO log spam regression on the no-subagent-listener path:"
        f" expected exactly {max_emissions} INFO records (1 ENTERED +"
        f" 1 SUBAGENT_PROGRESS first-call + 2 PROGRESS windows) for"
        f" 22 record_subagent_work calls across 2 status intervals;"
        f" got {len(info_records)}. Records: {info_records[:5]}"
    )

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


def test_emit_info_log_carries_latest_recorded_description(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_info_records
    captured_events: list[WaitingStatusEvent] = []
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
        listener=captured_events.append,
    )

    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    clock.advance(10.0)
    watchdog.record_subagent_work(description="tool_use:Write")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    clock.advance(10.0)
    watchdog.record_subagent_work(description="bash:ls")
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    info_records = _info_log_records(records)
    assert len(info_records) == 4, (
        f"expected exactly 4 INFO records (1 ENTERED + 1"
        f" SUBAGENT_PROGRESS first-call + 2 PROGRESS);"
        f" got {len(info_records)}. Records: {info_records}"
    )
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


def test_emit_no_info_log_when_subagent_activity_is_none(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_info_records
    captured_events: list[WaitingStatusEvent] = []
    watchdog, clock = _emit_info_log_throttle_make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
        listener=captured_events.append,
    )

    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_emit_info_log_throttle_waiting)

    assert len(captured_events) == 3, (
        f"main listener MUST receive ENTERED + 2 PROGRESS events; got {len(captured_events)}"
    )

    info_records = _info_log_records(records)
    assert info_records == [], (
        f"INFO log MUST NOT fire when subagent_activity is None"
        f" for every emit; got {len(info_records)} records."
        f" Records: {info_records}"
    )


def test_evidence_deferral_throttles_identical_channel_emission() -> None:
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
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


def test_evidence_deferral_throttle_uses_configured_window() -> None:
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


def test_evidence_deferral_throttle_is_per_channel() -> None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            watchdog_log_throttle_seconds=0.5,
            activity_evidence_ttl_seconds=180.0,
        ),
        clock,
    )

    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert (
        watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        == WatchdogVerdict.CONTINUE
    )

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

    clock.advance(200.0)
    watchdog.record_invocation_start()

    clock.advance(61.0)
    watchdog.record_subagent_work(now=clock.monotonic())
    assert (
        watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        == WatchdogVerdict.CONTINUE
    )
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


def test_evidence_deferral_throttle_resets_on_invocation_start() -> None:
    watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert (
        watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        == WatchdogVerdict.CONTINUE
    )
    watchdog.record_invocation_start()
    clock.advance(0.0)
    watchdog.record_mcp_tool_call(now=clock.monotonic())
    clock.advance(61.0)
    assert (
        watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        == WatchdogVerdict.CONTINUE
    )


def test_evidence_deferral_returns_continue_when_throttled() -> None:
    watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    for _ in range(50):
        verdict = watchdog.evaluate(classify_quiet=_evidence_deferral_throttle_active)
        assert verdict == WatchdogVerdict.CONTINUE


def test_evidence_deferral_uses_correlation_snapshot_when_no_channel() -> None:
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _evidence_deferral_throttle_make_watchdog(throttle_seconds=30.0)
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


def test_session_ceiling_fires_with_helpers_alive() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        max_session_seconds=300.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    clock.advance(305.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


def test_cumulative_waiting_ceiling_fires_with_helpers_alive() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        max_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(
        classify_quiet=_hard_ceiling_with_helpers_aliv_waiting_on_child
    )
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD
    clock.advance(5.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


def test_idle_timeout_fires_with_helpers_alive() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        drain_window_seconds=0.0,
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    clock.advance(65.0)
    verdict = watchdog.evaluate(classify_quiet=_hard_ceiling_with_helpers_aliv_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


def test_record_invocation_start_resets_all_per_invocation_fields() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    _populate_per_invocation_state(watchdog, clock)
    watchdog.record_invocation_start()
    for field_name, baseline in _per_invocation_fields().items():
        actual = getattr(watchdog, field_name)
        assert actual == baseline, (
            f"record_invocation_start MUST reset {field_name} to {baseline!r}; got {actual!r}"
        )


def test_record_invocation_start_resets_alive_by_signal() -> None:
    watchdog, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    watchdog.record_invocation_start()
    clock.advance(11.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
    assert watchdog.last_alive_by == AliveBy.FRESH_HEARTBEAT_ONLY

    watchdog.record_invocation_start()
    assert watchdog.last_alive_by is None, (
        f"record_invocation_start MUST clear last_alive_by; got {watchdog.last_alive_by!r}"
    )


def test_record_invocation_start_resets_progress_fingerprint() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    watchdog.record_invocation_start()
    watchdog.record_progress_report("phase=alpha")
    assert watchdog.last_progress_fingerprint is not None

    clock.advance(10.0)
    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == 10.0
    watchdog.record_progress_report("phase=alpha")
    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == 10.0

    watchdog.record_invocation_start()
    assert watchdog.last_progress_fingerprint is None, (
        "record_invocation_start MUST clear last_progress_fingerprint"
    )

    clock.advance(10.0)
    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == 10.0
    watchdog.record_progress_report("phase=alpha")
    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == 0.0, (
        "fresh progress report in new invocation MUST reset idle baseline"
    )


def test_record_invocation_start_resets_subagent_progress_emit_at() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured)
    assert watchdog.last_subagent_progress_emit_at is not None

    captured.clear()
    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert not any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured)

    captured.clear()
    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_emit_at is None, (
        "record_invocation_start MUST clear last_subagent_progress_emit_at"
    )

    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured), (
        "second invocation's first SUBAGENT_PROGRESS emit was suppressed by stale throttle"
    )


def test_record_invocation_start_resets_per_channel_timestamps() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(5.0)
    now = clock.monotonic()
    watchdog.record_mcp_tool_call(now=now)
    watchdog.record_subagent_work(description="x")
    watchdog.record_subagent_output(now=now)
    watchdog.record_workspace_event()

    summary_before = watchdog.last_evidence_summary(now)
    ts_before = {c.channel_name: c.last_at for c in summary_before.channels}
    assert ts_before[ChannelName.MCP_TOOL] == 5.0
    assert ts_before[ChannelName.SUBAGENT_LIVENESS] == 5.0
    assert ts_before[ChannelName.SUBAGENT_OUTPUT] == 5.0
    assert ts_before[ChannelName.WORKSPACE] == 5.0

    watchdog.record_invocation_start()

    summary_after = watchdog.last_evidence_summary(now)
    ts_after = {c.channel_name: c.last_at for c in summary_after.channels}
    assert ts_after[ChannelName.MCP_TOOL] is None
    assert ts_after[ChannelName.SUBAGENT_LIVENESS] is None
    assert ts_after[ChannelName.SUBAGENT_OUTPUT] is None
    assert ts_after[ChannelName.WORKSPACE] is None


def test_record_invocation_start_resets_cumulative_waiting() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    watchdog.record_invocation_start()
    watchdog.record_any_output()
    clock.advance(61.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(50.0)
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == 50.0

    watchdog.record_invocation_start()
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0, (
        "record_invocation_start MUST reset cumulative_waiting_on_child_seconds"
    )


def test_record_invocation_start_resets_coarse_any_deferred_log_at() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    watchdog.record_invocation_start()
    clock.advance(10.0)
    now = clock.monotonic()
    watchdog.record_mcp_tool_call(now=now)

    watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_AT_START,
        now=now,
        idle_elapsed=10.0,
    )
    watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=10.0,
    )
    assert watchdog.deferred_throttle_reasons == {
        WatchdogFireReason.NO_OUTPUT_AT_START.value,
        WatchdogFireReason.NO_OUTPUT_DEADLINE.value,
    }

    watchdog.record_invocation_start()
    assert watchdog.deferred_throttle_reasons == frozenset(), (
        f"record_invocation_start MUST reset coarse deferral throttles;"
        f" got {watchdog.deferred_throttle_reasons!r}"
    )


def test_second_invocation_starts_from_clean_baseline_no_stale_throttle() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(61.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    watchdog.record_mcp_tool_call(now=0.0)
    assert watchdog.evaluate(classify_quiet=_active).name == "CONTINUE"
    assert watchdog.has_active_deferral_throttles, (
        "first invocation MUST populate the deferral throttle map"
    )
    watchdog.record_invocation_start()
    clock.advance(0.1)
    assert not watchdog.has_active_deferral_throttles, (
        "record_invocation_start MUST reset all deferral throttle maps"
    )
    assert watchdog.deferred_throttle_reasons == frozenset()


def test_second_invocation_fingerprint_does_not_skip_fresh_lines() -> None:
    watchdog, clock = _invocation_start_full_reset_make_watchdog()
    clock.advance(5.0)
    watchdog.record_progress_report("phase=alpha")
    watchdog.record_invocation_start()
    clock.advance(5.0)
    watchdog.record_progress_report("phase=alpha")
    assert watchdog.last_progress_fingerprint == "phase=alpha", (
        f"second invocation MUST update last_progress_fingerprint;"
        f" got {watchdog.last_progress_fingerprint!r}"
    )


def test_gate_fire_throttles_identical_deferred_emission(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_debug_records
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=30.0)
    _patch_classifier_to_deferring_kind(watchdog)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(1000):
        verdict = watchdog.evaluate_fire_gate(
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


def test_gate_fire_throttle_uses_configured_window(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_debug_records
    watchdog, clock = _log_spam_throttle_make_watchdog(throttle_seconds=0.01)
    _patch_classifier_to_deferring_kind(watchdog)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(100):
        watchdog.evaluate_fire_gate(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    clock.advance(0.05)
    for _ in range(100):
        watchdog.evaluate_fire_gate(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    matching = [r for r in records if "deferred fire" in r and "CHILDREN_PERSIST_TOO_LONG" in r]
    assert len(matching) <= 3, f"throttle window 0.01s produced too many emissions: {len(matching)}"


def test_gate_fire_throttle_is_per_key() -> None:
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

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    call_log = [StuckKind.DUPLICATE_KILL]
    assert (
        watchdog.evaluate_fire_gate(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    call_log = [StuckKind.LOADING]
    assert (
        watchdog.evaluate_fire_gate(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    assert fire_reason.value in watchdog.deferred_throttle_reasons, (
        f"fire_reason key missing from coarse throttle reasons; keys={list(watchdog.deferred_throttle_reasons)}"
    )
    assert watchdog.last_deferred_kind == StuckKind.LOADING, (
        f"expected last_deferred_kind=LOADING (the most recent);"
        f" got {watchdog.last_deferred_kind!r}"
    )


def test_coarse_single_key_throttle_caps_emissions_across_kind_cycles(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, records = captured_debug_records
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

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    kinds = [StuckKind.DUPLICATE_KILL, StuckKind.LOADING]
    for i in range(1000):
        call_log = [kinds[i % 2]]
        watchdog.evaluate_fire_gate(
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


def test_scoped_child_active_appears_in_hard_stop_diag() -> None:
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


def test_log_spam_throttle_public_surface_reaches_deferred_fire_branch(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
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
    first_verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD deferral; got {first_verdict!r}"
    )

    clock.advance(5.1)

    for i in range(1000):
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

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

    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the deferred-fire DEBUG log emitted at"
        f" _gate.py:174; got {len(deferred_fire_records)} records"
        f" matching 'deferred fire' + 'children_persist_too_long'"
        f" (a zero-sink bypass means the bound is meaningless)."
    )

    assert watchdog.last_deferred_kind in (StuckKind.THINKING, StuckKind.LOADING), (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST report the"
        f" deferring kind after 1000 deferred-fire cycles; got"
        f" {watchdog.last_deferred_kind!r}"
    )

    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )


def test_log_spam_throttle_public_surface_deferred_fire_throttle_window(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
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

    for _ in range(100):
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    clock.advance(0.1)

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


def test_log_spam_throttle_public_surface_kind_cycle_via_public_surface(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
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

    for i in range(1000):
        watchdog.set_is_waiting_state(i % 2 == 1)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

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

    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the first deferred-fire DEBUG log; got"
        f" {len(deferred_fire_records)} records (a zero-sink bypass"
        f" would make the throttle bound meaningless)."
    )

    assert watchdog.last_deferred_kind in (
        StuckKind.THINKING,
        StuckKind.LOADING,
        StuckKind.DUPLICATE_KILL,
    ), (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST report a"
        f" deferring kind after 1000 cycle iterations; got"
        f" {watchdog.last_deferred_kind!r}"
    )

    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )


def test_extract_tool_call_from_plain_tool_use_envelope() -> None:
    line = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


def test_extract_tool_call_from_claude_content_block_start_envelope() -> None:
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


def test_extract_tool_call_from_arguments_field() -> None:
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


def test_extract_tool_call_from_pi_tool_execution_start_envelope() -> None:
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


def test_extract_tool_call_from_pi_toolcall_end_envelope() -> None:
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


def test_extract_tool_call_from_live_cursor_nested_tool_call_envelope() -> None:
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


def test_extract_tool_call_returns_none_for_non_tool_use_envelope() -> None:
    line = json.dumps({"type": "text", "text": "hello"})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is None


def test_extract_tool_call_returns_none_for_invalid_json() -> None:
    assert _extract_tool_call_from_activity_signal("not json {{{") is None
    assert _extract_tool_call_from_activity_signal("") is None


def test_extract_tool_call_returns_unknown_for_missing_name() -> None:
    line = json.dumps({"type": "tool_use", "input": {"foo": "bar"}})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "unknown"
    assert tool_args == {"foo": "bar"}


def test_extract_tool_call_from_claude_prefixed_plain_text() -> None:
    result = _extract_tool_call_from_activity_signal("claude tool: Bash")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {}


def test_extract_tool_call_from_plain_tool_prefix() -> None:
    result = _extract_tool_call_from_activity_signal("[plain] tool: Read")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Read"
    assert tool_args == {}


def test_extract_tool_call_returns_none_for_plain_text_without_tool_marker() -> None:
    assert _extract_tool_call_from_activity_signal("random log line") is None


def test_pi_strategy_classifies_tool_execution_start_as_tool_use() -> None:
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


def test_pi_strategy_classifies_toolcall_end_as_tool_use() -> None:
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
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


def test_pi_strategy_classifies_message_update_error_as_error_line() -> None:
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


def test_pi_strategy_classifies_tool_execution_error_as_error_line() -> None:
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


def test_cursor_strategy_classifies_live_tool_call_lifecycle() -> None:
    strategy = _make_cursor_strategy()
    started_line = json.dumps(
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
    completed_line = json.dumps(
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

    for line, expected_kind in (
        (started_line, AgentActivityKind.TOOL_USE),
        (completed_line, AgentActivityKind.TOOL_RESULT),
    ):
        signal = strategy.classify_activity_line(line)

        assert signal is not None
        assert signal.kind == expected_kind
        assert signal.raw == line


def test_pty_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
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


def test_pty_line_reader_routes_repeated_tool_use_to_trip_breaker() -> None:
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


def test_pty_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
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


def test_pty_line_reader_silently_skips_unrecognised_envelopes() -> None:
    reader = _build_pty_reader_with_strategy(_JunkToolUseStrategy())
    watchdog = _RecordingWatchdog()

    list(reader._handle_queued_line("not-json-{{{\n", watchdog))

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )


def test_process_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        _input_prompt=None,
    )
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


def test_process_line_reader_routes_repeated_tool_use_to_breaker() -> None:
    raw = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
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


def test_process_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
    raw = json.dumps({"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
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


def test_repeated_tool_call_timeout_diagnostic_identifies_command() -> None:
    raw = json.dumps({"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}})
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
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


def test_repeated_tool_call_timeout_messages_identify_command() -> None:
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


def test_process_line_reader_routes_plain_text_tool_use_to_breaker() -> None:
    strategy = _ToolUseStrategy("claude tool: Bash")
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
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


def test_process_line_reader_silently_skips_unrecognised_tool_envelopes() -> None:
    strategy = _JunkToolUseStrategy()
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
        _input_prompt=None,
    )
    bound_method = MethodType(ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    bound_method(watchdog, "not-json-{{{\n")

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )


def test_identical_tool_calls_trip_even_when_each_result_arrives() -> None:
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


def test_extract_tool_call_from_claude_assistant_message() -> None:
    line = _claude_assistant_tool_use_line({"command": "echo one"})

    result = _extract_tool_call_from_activity_signal(line)

    assert result == ("Bash", {"command": "echo one"})


def test_claude_distinct_commands_produce_distinct_fingerprints() -> None:
    first = _extract_tool_call_from_activity_signal(
        _claude_assistant_tool_use_line({"command": "echo one"})
    )
    second = _extract_tool_call_from_activity_signal(
        _claude_assistant_tool_use_line({"command": "echo two"})
    )

    assert first != second


def test_extract_tool_call_skips_claude_streaming_placeholder() -> None:
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


def test_claude_strategy_distinct_commands_do_not_trip_the_breaker() -> None:
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


def test_claude_identical_commands_still_trip_the_breaker() -> None:
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


def test_defers_when_subagent_progress_observed_within_window() -> None:
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    clock.advance(31.0)
    wd.record_subagent_work(description="tool_use:Bash")
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer when subagent progress is fresh; got {verdict}"
    )


def test_does_not_defer_after_evidence_ttl_expired() -> None:
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    clock.advance(31.0)
    wd.record_subagent_work(description="tool_use:Bash")
    clock.advance(_ACTIVITY_TTL_SECONDS + 1.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire after the activity TTL expires; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_no_output_at_start_defers_on_first_waiting_on_child_entry() -> None:
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_waiting_on_child)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer on first WAITING_ON_CHILD entry; got {verdict}"
    )
    assert wd.last_fire_reason is None


def test_no_output_at_start_fires_after_waiting_ceiling_reached() -> None:
    wd, clock = _no_output_at_start_make_watchdog()
    wd.record_invocation_start()

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


def test_no_output_at_start_fires_at_threshold_even_when_floor_unreached() -> None:
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
    clock.advance(60.0)
    verdict = wd.evaluate(classify_quiet=_no_output_at_start_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold regardless of"
        f" the dumb-kill floor (invocation_elapsed=60s, threshold=30s,"
        f" floor=120s); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


@pytest.mark.parametrize("alive_by", _FRESH_ALIVE_BY_STATES)
def test_no_output_at_start_defers_when_alive_by_is_fresh(alive_by: AliveBy) -> None:

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


@pytest.mark.parametrize("alive_by", _STALE_ALIVE_BY_STATES)
def test_no_output_at_start_fires_when_alive_by_is_stale(alive_by: AliveBy) -> None:

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


def test_no_output_at_start_full_lifecycle_parity() -> None:

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


def test_no_output_at_start_fires_at_threshold_with_stale_alive_by() -> None:
    wd, clock = _no_output_at_start_loading_make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()

    clock.advance(60.0)

    verdict = wd.evaluate(classify_quiet=_no_output_at_start_loading_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold even when the"
        f" dumb-kill floor is not yet elapsed and alive_by is stale"
        f" (invocation_elapsed=60s, threshold=30s, floor=120s);"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_no_output_at_start_fires_for_truly_silent_run() -> None:
    wd, clock = _no_output_at_start_loading_make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=None,
    )
    wd.record_invocation_start()
    clock.advance(150.0)

    verdict = wd.evaluate(classify_quiet=_no_output_at_start_loading_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire at the threshold for a truly silent run; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_watchdog_fires_no_progress_quiet_on_prompt_signature() -> None:
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

    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE

    clock.advance(12.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


def test_watchdog_does_not_fire_no_progress_quiet_when_post_tool_result_fresh() -> None:
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

    watchdog.record_tool_result_activity()

    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE


def test_no_progress_quiet_diagnostic_payload_contains_required_fields() -> None:
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
    stalled_evt = next(e for e in captured_events if e.kind == WaitingStatusKind.STALLED)
    assert stalled_evt is not None
    hard_stop_evt = next(e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP)
    assert hard_stop_evt is not None
    evt = hard_stop_evt
    assert evt.kind.value == "hard_stop"

    diag = evt.diagnostic
    assert "invocation_elapsed" in diag, "diagnostic must contain invocation_elapsed"
    assert "idle_elapsed" in diag, "diagnostic must contain idle_elapsed"
    assert "ceiling" in diag, "diagnostic must contain ceiling"
    assert diag["ceiling"] == 10.0
    assert "effective_ceiling" in diag, "diagnostic must contain effective_ceiling"
    assert diag["effective_ceiling"] == "no_progress_quiet"
    assert "evidence_summary" in diag, "diagnostic must contain evidence_summary"


def test_post_exit_fire_reasons_do_not_resume() -> None:
    for fire, expected in (
        (_fire_process_exit_hang, WatchdogFireReason.PROCESS_EXIT_HANG),
        (_fire_descendant_hang, WatchdogFireReason.DESCENDANT_HANG),
    ):
        wrapper = fire()
        timeout_exc = _convert_reason_to_agent_error(wrapper)
        assert timeout_exc.reason == expected
        _assert_non_resumable_recovery_chain(timeout_exc)


def test_in_stream_absolute_fire_reasons_do_not_resume() -> None:
    for reason in (
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    ):
        pending_lines, wrapper = _fire_in_stream_reason(reason)
        timeout_exc = _convert_reason_to_agent_error(wrapper, pending_lines)
        assert timeout_exc.reason == reason
        _assert_non_resumable_recovery_chain(timeout_exc)


def test_step_frames_classify_as_lifecycle() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    assert strategy.classify_activity_line(_frame("step_start")) is not None
    assert strategy.classify_activity_line(_frame("step_start")).kind == (
        AgentActivityKind.LIFECYCLE
    )
    assert strategy.classify_activity_line(_frame("step_finish")).kind == (
        AgentActivityKind.LIFECYCLE
    )


def test_wedge_trips_on_the_real_interleaved_stream() -> None:
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


def test_distinct_calls_on_the_real_stream_do_not_trip() -> None:
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


def test_extract_tool_call_from_opencode_part_nested_envelope() -> None:
    line = _opencode_tool_line("ralph_git_status", {"format": "compact"})

    result = extract_tool_call_from_activity_signal(line)

    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "ralph_git_status"
    assert tool_args == {"format": "compact"}


def test_opencode_distinct_tool_calls_produce_distinct_fingerprints() -> None:
    first = extract_tool_call_from_activity_signal(
        _opencode_tool_line("ralph_git_status", {"format": "compact"})
    )
    second = extract_tool_call_from_activity_signal(
        _opencode_tool_line("todowrite", {"todos": [{"content": "a"}]}, call_id="call_2")
    )

    assert first != second


def test_extract_tool_call_ignores_opencode_non_tool_part() -> None:
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {"type": "step-start", "id": "prt_1"},
        }
    )

    assert extract_tool_call_from_activity_signal(line) is None


def test_extract_tool_call_returns_none_when_nothing_distinguishing() -> None:
    line = json.dumps({"type": "tool_use"})

    assert extract_tool_call_from_activity_signal(line) is None


def test_opencode_strategy_regression_classifies_untyped_tool_part_as_tool_use() -> None:
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


def test_opencode_strategy_regression_classifies_untyped_running_task_as_child_progress() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line(
        "task", {"prompt": "inspect"}, status="running", include_part_type=False
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS


def test_opencode_strategy_classifies_tool_use_as_tool_use() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_read_file", {"path": "/tmp/x"})

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


def test_opencode_strategy_keeps_an_errored_tool_in_the_tool_dimension() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_exec", {"command": "uv run pytest -q"}, status="error")

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "ralph_exec",
        {"command": "uv run pytest -q"},
    )


def test_opencode_repeated_failing_tool_trips_the_breaker() -> None:
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


def test_opencode_errored_subagent_releases_its_child_progress_signal() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("task", {"prompt": "inspect"}, status="error")

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_TERMINAL_ACK


def test_opencode_strategy_classifies_tool_result_as_tool_result() -> None:
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


def test_opencode_distinct_tool_calls_do_not_trip_the_breaker() -> None:
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


def test_opencode_identical_tool_calls_still_trip_the_breaker() -> None:
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


def test_short_ceiling_fires_at_os_descendant_only_ceiling() -> None:
    events: list[WaitingStatusEvent] = []
    events: list[WaitingStatusEvent] = []
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        os_descendant_only_ceiling=120.0,
        corroborator=_make_os_descendant_only_corroborator(),
        listener=events.append,
    )

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


def test_suspect_event_fires_at_os_descendant_only_suspect_seconds() -> None:
    events: list[WaitingStatusEvent] = []
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        suspect=500.0,
        os_descendant_only_ceiling=120.0,
        os_descendant_only_suspect=60.0,
        status_interval=100.0,
        corroborator=_make_os_descendant_only_corroborator(),
        listener=events.append,
    )

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


@pytest.mark.parametrize(
    ("alive_by", "policy_override"),
    [
        (AliveBy.CPU_IDLE_WHILE_ALIVE, {"cpu_idle_seconds": 60.0}),
        (AliveBy.LOG_STALE_WHILE_ALIVE, {"log_growth_seconds": 30.0}),
    ],
    ids=["cpu_idle_while_alive", "log_stale_while_alive"],
)
def test_alive_by_override_picks_no_progress_ceiling(
    alive_by: AliveBy,
    policy_override: dict[str, float],
) -> None:

    def _alive_by_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    events: list[WaitingStatusEvent] = []
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        no_progress_ceiling=180.0,
        corroborator=_alive_by_corr,
        listener=events.append,
        **policy_override,
    )

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


def test_fresh_progress_path_unchanged() -> None:

    def _fresh_progress_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    events: list[WaitingStatusEvent] = []
    watchdog, clock = _os_descendant_only_escalation_make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        status_interval=100.0,
        corroborator=_fresh_progress_corr,
        listener=events.append,
    )

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


def test_post_exit_watchdog_fire_reasons_do_not_resume_with_prior_session() -> None:

    for failure_reason in (
        "PROCESS_EXIT_HANG",
        "DESCENDANT_HANG",
        "CHILDREN_PERSIST_TOO_LONG",
        "DEFERRED_BY_STUCK_CLASSIFIER",
    ):
        action = recovery_action_for_failure_reason(failure_reason, has_prior_session=True)
        assert action == "fresh", (
            f"failure_reason={failure_reason!r} with has_prior_session=True"
            f" MUST return 'fresh' (the half-dead process tree cannot"
            f" be safely resumed); got {action!r}"
        )


def test_process_exit_hang_without_prior_session_returns_fresh() -> None:
    action = recovery_action_for_failure_reason("PROCESS_EXIT_HANG", has_prior_session=False)
    assert action == "fresh"


def test_process_exit_hang_is_not_in_resumable_set() -> None:
    action = recovery_action_for_failure_reason("PROCESS_EXIT_HANG", has_prior_session=True)
    assert action != "resume", (
        f"PROCESS_EXIT_HANG MUST NOT resume (the post-exit hang is"
        f" a half-dead process tree); got {action!r}"
    )
    assert action != "new_session_with_id", (
        f"PROCESS_EXIT_HANG MUST NOT new_session_with_id (the prior"
        f" session id is unsafe to reuse); got {action!r}"
    )


def test_resumable_reasons_still_resume_for_prior_session() -> None:
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


def _assert_agent_registry_build_subagent_pid_registry_per_transport(
    transport: AgentTransport,
) -> None:
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(transport)
    assert isinstance(registry, SubagentPidRegistry)
    assert isinstance(source, SubagentPidSource)
    registry.register(12345, source=transport.value, now=0.0)
    assert 12345 in source.known_subagent_pids()
    other_transport = (
        AgentTransport.CLAUDE if transport != AgentTransport.CLAUDE else AgentTransport.PI
    )
    other_registry, other_source = agent_registry.build_subagent_pid_registry(other_transport)
    other_registry.register(67890, source=other_transport.value, now=0.0)
    assert 67890 not in source.known_subagent_pids()
    assert 67890 in other_source.known_subagent_pids()


def test_agent_registry_build_subagent_pid_registry_per_transport() -> None:

    for transport in _NON_AGY_TRANSPORTS:
        _assert_agent_registry_build_subagent_pid_registry_per_transport(transport)


@pytest.mark.parametrize("transport", [AgentTransport.AGY], ids=["agy"])
def test_agent_registry_build_subagent_pid_registry_per_transport_agy(
    transport: AgentTransport,
) -> None:
    _assert_agent_registry_build_subagent_pid_registry_per_transport(transport)


def test_agent_registry_build_subagent_pid_registry_rejects_unknown_transport() -> None:
    agent_registry = AgentRegistry()
    with pytest.raises(ValueError, match="no SubagentPidSource factory"):
        agent_registry.build_subagent_pid_registry("not-a-transport")


def test_agent_registry_from_config_provides_subagent_registry_helper() -> None:
    config = UnifiedConfig()
    agent_registry = AgentRegistry.from_config(config)
    assert hasattr(agent_registry, "build_subagent_pid_registry")
    for transport in AgentTransport:
        registry, source = agent_registry.build_subagent_pid_registry(transport)
        assert isinstance(registry, SubagentPidRegistry)
        assert isinstance(source, SubagentPidSource)


def test_classify_quiet_returns_waiting_when_filtered_source_has_pids() -> None:
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    registry.register(4242, source="claude", now=0.0)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.WAITING_ON_CHILD


def test_classify_quiet_returns_active_when_filtered_source_empty_even_with_descendants() -> None:
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.ACTIVE


def test_classify_quiet_uses_registry_snapshot_when_no_pid_source() -> None:
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


def test_classify_quiet_empty_registry_returns_active_even_with_descendants() -> None:
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


def test_strategy_for_transport_threads_subagent_pid_source() -> None:
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    assert strategy._subagent_pid_source is source


def _assert_parser_constructor_stores_subagent_pid_registry(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    agent_registry = AgentRegistry()
    registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport(transport_label))
    parser = parser_cls(subagent_pid_registry=registry)
    assert getattr(parser, "_subagent_pid_registry", None) is registry


def test_parser_constructor_stores_subagent_pid_registry() -> None:

    for transport_label, parser_cls in [
        ("claude", ClaudeParser),
        ("claude_interactive", ClaudeInteractiveParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("generic", GenericParser),
    ]:
        _assert_parser_constructor_stores_subagent_pid_registry(transport_label, parser_cls)


@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [("agy", AgyParser)],
    ids=["agy-AgyParser"],
)
def test_parser_constructor_stores_subagent_pid_registry_agy(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    _assert_parser_constructor_stores_subagent_pid_registry(transport_label, parser_cls)


def test_parser_default_constructor_keeps_registry_none() -> None:
    parser = ClaudeParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = CodexParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = GenericParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_parser_with_registry_can_still_parse_lines() -> None:
    agent_registry = AgentRegistry()
    registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport.CODEX)
    parser = CodexParser(subagent_pid_registry=registry)
    lines = ['{"type": "text", "content": "hello world"}']
    events = list(parser.parse(iter(lines)))
    assert events, "parser.parse MUST still yield events with a registry"
    assert events[0].content == "hello world"


def test_end_to_end_filtered_count_is_visible_to_strategy_classify_quiet() -> None:
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    handle = _FakeHandle(has_descendants=False)
    probe = FakeLivenessProbe(active=False)

    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE

    registry.register(99001, source="claude", now=0.0)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    registry.unregister(99001)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


def test_catalog_default_seeded_transports_have_subagent_pid_registry_factory() -> None:
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


def _assert_parser_registers_pid_from_structured_event_when_registry_wired(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    registry = SubagentPidRegistry()
    parser = parser_cls(
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    assert len(registry) == 0

    pid = 55555
    line_with_pid = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line_with_pid])))
    assert events, "parser MUST still emit an event for child_progress line"
    assert pid in registry.known_pids()
    identity = next(iter(registry.snapshot()))
    assert identity.source == transport_label

    line_without_pid = '{"type": "text", "content": "hello"}'
    events2 = list(parser.parse(iter([line_without_pid])))
    assert events2
    assert len(registry) == 1


def test_parser_registers_pid_from_structured_event_when_registry_wired() -> None:

    for transport_label, parser_cls in [
        ("claude", ClaudeParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("generic", GenericParser),
    ]:
        _assert_parser_registers_pid_from_structured_event_when_registry_wired(
            transport_label, parser_cls
        )


@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [("agy", AgyParser)],
    ids=["agy-AgyParser"],
)
def test_parser_registers_pid_from_structured_event_when_registry_wired_agy(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    _assert_parser_registers_pid_from_structured_event_when_registry_wired(
        transport_label, parser_cls
    )


def test_parser_registration_hook_no_op_when_registry_none() -> None:
    parser = CodexParser()
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_parser_registration_hook_no_op_when_source_label_none() -> None:
    registry = SubagentPidRegistry()
    parser = CodexParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    assert 12345 not in registry.known_pids()


def _assert_get_parser_threads_registry_and_source_label(
    parser_key: str,
    transport_label: str,
) -> None:
    registry = SubagentPidRegistry()
    parser = get_parser(
        parser_key,
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    assert parser._subagent_pid_registry is registry
    assert parser._subagent_source_label == transport_label


def test_get_parser_threads_registry_and_source_label() -> None:

    for parser_key, transport_label in [
        ("claude", "claude"),
        ("codex", "codex"),
        ("pi", "pi"),
        ("generic", "generic"),
    ]:
        _assert_get_parser_threads_registry_and_source_label(parser_key, transport_label)


@pytest.mark.parametrize(
    ("parser_key", "transport_label"),
    [("agy", "agy")],
    ids=["agy-agy"],
)
def test_get_parser_threads_registry_and_source_label_agy(
    parser_key: str,
    transport_label: str,
) -> None:
    _assert_get_parser_threads_registry_and_source_label(parser_key, transport_label)


def test_get_parser_default_kwargs_keep_registry_none() -> None:
    parser = get_parser("claude")
    assert getattr(parser, "_subagent_pid_registry", None) is None
    assert getattr(parser, "_subagent_source_label", None) is None


@pytest.mark.subprocess_e2e
def test_invoke_agent_threads_subagent_pid_source_into_strategy_for_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> BaseExecutionStrategy:
        captured.update(kwargs)
        return BaseExecutionStrategy(
            label_scope=kwargs.get("label_scope"),
            registry=kwargs.get("registry"),
            subagent_pid_source=kwargs.get("subagent_pid_source"),
        )

    def _empty_generator(*_args: object, **_kwargs: object) -> Iterator[str]:
        return iter([])

    monkeypatch.setattr(ralph_invoke, "strategy_for_command", _spy)
    monkeypatch.setattr(ralph_invoke, "run_subprocess_and_read_lines", _empty_generator)
    monkeypatch.setattr(ralph_invoke, "run_pty_and_read_lines", _empty_generator)

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


def test_gemini_parser_registers_pid_from_child_progress() -> None:
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


def test_gemini_parser_registers_pid_via_direct_constructor() -> None:
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


def test_gemini_parser_registration_no_op_when_registry_none() -> None:
    parser = GeminiParser()
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_gemini_parser_registration_no_op_when_source_label_none() -> None:
    registry = SubagentPidRegistry()
    parser = GeminiParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    assert 12345 not in registry.known_pids()


def test_gemini_parser_propagates_non_value_error_registration_failures() -> None:
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


def test_gemini_parser_swallows_value_error_registration_failures() -> None:

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


def test_zero_activity_past_no_progress_quiet_fires() -> None:
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


def test_zero_activity_during_no_output_at_start_window_fires() -> None:
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


def test_polling_threaded_through_varied_work_does_not_trip() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(124):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/file_{index}.py"})
        clock.advance(1.0)
        if index % 9 == 0:
            tracker.mark_tool_call("ralph_get_plan_draft", {})
            clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


def test_distinct_calls_at_run_start_do_not_trip() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(8):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/{index}.py"})
        clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


def test_one_call_repeated_still_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "uv run pytest -q"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_two_call_loop_still_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(10):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        clock.advance(1.0)
        tracker.mark_tool_call("ralph_read_file", {"path": "/repo/out.log"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_three_call_loop_still_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(9):
        for name in ("ralph_exec", "ralph_read_file", "ralph_git_status"):
            tracker.mark_tool_call(name, {})
            clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_interleaved_output_still_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        tracker.note_progress()
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_fire_no_output_at_start_yields_inactivity_error() -> None:
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

    clock.advance(125.0)
    verdict = watchdog.evaluate(classify_quiet=_classify_quiet)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE after no_output_at_start past the dumb-kill floor (125s); got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    fake_self = _FakeCheckFireSelfResumeAfterKillContract(_policy=policy, _clock=clock)
    result = ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
    assert result is not None, "_check_fire must return a wrapper when the verdict is FIRE"
    pending_lines, wrapper = result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    assert wrapper.reason == WatchdogFireReason.NO_OUTPUT_AT_START

    assert isinstance(wrapper.__cause__, IdleWatchdogKilledError)
    assert wrapper.__cause__.reason == WatchdogFireReason.NO_OUTPUT_AT_START.value
    assert wrapper.__cause__.child_alive is False

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


def test_agent_inactivity_timeout_error_session_resume_safe_in_set() -> None:
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


def test_agent_inactivity_timeout_error_session_resume_safe_out_of_set() -> None:
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


def test_recovery_action_returns_resume_for_agent_inactivity_timeout() -> None:
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=True,
    )
    assert action == "resume", (
        f"recovery_action_for_failure_reason MUST return 'resume' for"
        f" AgentInactivityTimeoutError with prior session; got {action!r}"
    )


def test_resolve_resume_session_id_threads_prior_session_id() -> None:
    sid = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="abc-123",
        recovery_action="resume",
    )
    assert sid == "abc-123", f"resolve_resume_session_id MUST thread the prior id; got {sid!r}"


def test_fresh_session_options_clears_session_id() -> None:
    opts = InvokeOptions(session_id="prior-sid")
    fresh = fresh_session_options(opts, prior_session_id="prior-sid")
    assert fresh.session_id is None, (
        f"fresh_session_options MUST clear session_id; got {fresh.session_id!r}"
    )


def test_agent_retry_intent_for_failure_returns_resume_intent() -> None:
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


def test_idle_watchdog_killed_error_aliases_match() -> None:
    assert IdleWatchdogKilledError is IdleWatchdogKilledErrorTop, (
        "IdleWatchdogKilledError MUST be a single class re-exported from"
        " both ralph.agents.idle_watchdog.idle_watchdog and"
        " ralph.agents.idle_watchdog_kill"
    )


def test_no_output_at_start_fire_with_known_session_id_yields_resume_intent() -> None:
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


def test_is_resumable_fire_reason_classifies_known_reasons() -> None:
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


def test_expected_fire_reasons_partitioned_into_resumable_or_excluded() -> None:

    for reason_value in sorted(EXPECTED_FIRE_REASONS):
        assert (
            reason_value in _RESUMABLE_REASON_VALUES or reason_value in _NON_RESUMABLE_REASON_VALUES
        ), (
            f"{reason_value!r} is neither resumable nor in the documented"
            f" non-resumable exclusion set; update the resume contract"
        )


def test_idle_watchdog_killed_error_reason_round_trips_through_resumable_helper() -> None:

    for reason in sorted(WatchdogFireReason, key=str):
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


def test_every_fire_reason_is_classified() -> None:
    for reason in WatchdogFireReason.__members__.values():
        assert reason in _RESUMABLE_FIRE_REASONS or reason in _NON_RESUMABLE_REASONS, (
            f"reason={reason!r} is neither in _RESUMABLE_FIRE_REASONS nor in the"
            f" explicit non-resumable exclusion set; update the resume contract"
        )


def test_resumable_and_non_resumable_sets_are_disjoint() -> None:
    overlap = _RESUMABLE_FIRE_REASONS & _NON_RESUMABLE_REASONS
    assert not overlap, f"resumable and non-resumable sets overlap: {overlap!r}"


def test_deferred_by_stuck_classifier_never_fires() -> None:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(
        policy, clock, process_monitor=_NoProcessMonitorResumeContractInvariant()
    )
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


def test_idle_watchdog_killed_error_carries_resumable_session_id() -> None:
    exc = IdleWatchdogKilledError(
        reason="no_output_at_start",
        signal=15,
        resumable_session_id="sess-abc123",
    )
    assert exc.resumable_session_id == "sess-abc123", (
        "IdleWatchdogKilledError MUST surface resumable_session_id"
    )


def test_failure_classifier_carries_resumable_session_id() -> None:
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


def test_recovery_controller_sets_last_agent_session_id() -> None:
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


def test_apply_chain_retry_emits_resume_intent_with_captured_id() -> None:
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


def test_resume_safe_helper_threads_captured_id_through_convert_seam() -> None:
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


def test_is_resumable_fire_reason_for_no_output_at_start() -> None:
    assert _is_resumable_fire_reason(WatchdogFireReason.NO_OUTPUT_AT_START) is True, (
        "NO_OUTPUT_AT_START MUST be resumable"
    )


def test_pipeline_state_copy_with_accepts_last_agent_session_id() -> None:
    state = _make_pipeline_state()
    updated = state.copy_with(last_agent_session_id="sid-1")
    assert updated.last_agent_session_id == "sid-1"


def test_multi_agent_chain_resume_keeps_current_agent() -> None:
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

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"Multi-agent chain current_index MUST stay on agent-a (0) for a"
        f" resumable kill; got {chain.current_index}"
    )
    assert chain.agents[chain.current_index] == "agent-a", (
        f"Multi-agent chain MUST stay on the timed-out agent for a"
        f" resumable kill; got {chain.agents[chain.current_index]!r}"
    )
    intent = new_state.agent_retry_intent
    assert intent.action == "resume", (
        f"agent_retry_intent.action MUST be 'resume' for a resumable kill"
        f" in a multi-agent chain; got {intent.action!r}"
    )
    assert intent.session_id == captured_session_id, (
        f"agent_retry_intent.session_id MUST thread the captured id even"
        f" in a multi-agent chain; got {intent.session_id!r}"
    )
    assert new_state.last_agent_session_id == captured_session_id, (
        f"state.last_agent_session_id MUST be set from the watchdog's"
        f" captured id; got {new_state.last_agent_session_id!r}"
    )


def test_multi_agent_chain_non_resumable_kill_does_fallover() -> None:
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


def test_resumable_fire_reasons_matches_expected_set() -> None:
    assert _RESUMABLE_FIRE_REASONS == _RESUMABLE_REASONS_EXPECTED, (
        f"Expected resumable set to be {_RESUMABLE_REASONS_EXPECTED!r},"
        f" got {_RESUMABLE_FIRE_REASONS!r}. Update both this test AND"
        f" tests/agents/idle_watchdog/test_resume_after_kill_contract.py"
        f" when changing the contract."
    )


def test_is_resumable_fire_reason_returns_true_for_in_set() -> None:

    for reason in sorted(_RESUMABLE_REASONS_EXPECTED, key=str):
        assert _is_resumable_fire_reason(reason) is True, (
            f"reason={reason!r}: MUST be resumable; got False"
        )


def test_is_resumable_fire_reason_returns_false_for_out_of_set() -> None:

    for reason in sorted(
        set(WatchdogFireReason) - _RESUMABLE_REASONS_EXPECTED,
        key=str,
    ):
        assert _is_resumable_fire_reason(reason) is False, (
            f"reason={reason!r}: MUST NOT be resumable; got True"
        )


def test_process_reader_emits_session_resume_safe_true_for_in_set_reasons() -> None:

    for reason in sorted(_RESUMABLE_REASONS_EXPECTED, key=str):
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


def test_pty_runner_emits_session_resume_safe_true_for_in_set_reasons() -> None:

    for reason in sorted(_RESUMABLE_REASONS_EXPECTED, key=str):
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


def test_process_reader_emits_session_resume_safe_false_for_out_of_set_reasons() -> None:

    for reason in [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ]:
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


def test_pty_runner_emits_session_resume_safe_false_for_out_of_set_reasons() -> None:

    for reason in [
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
    ]:
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


def test_process_reader_thread_session_id_even_when_not_resumable() -> None:
    exc = _raise_like_process_reader(
        _LineReaderLike(captured_session_id="sess-from-stream"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    )
    assert exc.session_resume_safe is False
    assert exc.resumable_session_id == "sess-from-stream"


def test_process_reader_thread_expected_session_id_fallback_when_no_capture() -> None:
    exc = _raise_like_process_reader(
        _LineReaderLike(expected_session_id="sess-expected"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected"


def test_pty_runner_thread_expected_session_id_fallback_when_no_capture() -> None:
    exc = _raise_like_pty_runner(
        _LineReaderLike(expected_session_id="sess-expected-pty"),
        timeout_seconds=30.0,
        reason=WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
    )
    assert exc.session_resume_safe is True
    assert exc.resumable_session_id == "sess-expected-pty"


@pytest.mark.parametrize("reason", sorted(_RESUMABLE_REASONS_EXPECTED, key=str))
def test_invoke_agent_subprocess_seam_emits_resume_safe_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: WatchdogFireReason,
) -> None:
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is True, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=True; got {exc.session_resume_safe}"
    )
    assert exc.resumable_session_id == "sess-runtime-seam", (
        f"reason={reason!r}: expected session id fallback; got {exc.resumable_session_id!r}"
    )


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
    exc = _drive_invoke_agent_with_reason(monkeypatch, tmp_path, reason)
    assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
    assert exc.session_resume_safe is False, (
        f"reason={reason!r}: full invoke_agent seam MUST emit"
        f" session_resume_safe=False; got {exc.session_resume_safe}"
    )


def test_session_ceiling_exceeded_with_prior_session_returns_fresh() -> None:
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=True)
    assert action == "fresh", (
        f"SESSION_CEILING_EXCEEDED with has_prior_session=True MUST"
        f" return 'fresh' (operator-set hard cap cannot be"
        f" resumed); got {action!r}"
    )


def test_session_ceiling_exceeded_without_prior_session_returns_fresh() -> None:
    action = recovery_action_for_failure_reason("SESSION_CEILING_EXCEEDED", has_prior_session=False)
    assert action == "fresh"


def test_session_ceiling_exceeded_does_not_resume_via_recovery_action() -> None:
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


def test_session_ceiling_is_in_non_resumable_set() -> None:
    hard_caps = ("SESSION_CEILING_EXCEEDED", "CHILDREN_PERSIST_TOO_LONG")
    for cap in hard_caps:
        action = recovery_action_for_failure_reason(cap, has_prior_session=True)
        assert action == "fresh", f"hard-cap reason={cap!r} MUST NOT resume; got {action!r}"


def test_invoke_options_carries_shared_registry_field() -> None:
    options = InvokeOptions()
    assert hasattr(options, "subagent_pid_registry")
    assert hasattr(options, "subagent_pid_source")
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


def test_invoke_options_round_trip_via_replace() -> None:
    options = InvokeOptions()
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    assert options.subagent_pid_registry is None


def test_parser_registered_pid_reaches_strategy_filter() -> None:
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    registry.register(12345, source="opencode", now=0.0)
    assert source.known_subagent_pids() == {12345}


def test_separate_registries_desynchronize_filter() -> None:
    orchestrator_registry, _ = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    strategy_registry, strategy_source = AgentRegistry().build_subagent_pid_registry(
        AgentTransport.OPENCODE
    )
    orchestrator_registry.register(99999, source="opencode", now=0.0)
    assert orchestrator_registry is not strategy_registry
    assert strategy_source.known_subagent_pids() == set()


def test_make_opencode_subagent_pid_source_shares_registry() -> None:
    registry = SubagentPidRegistry()
    source = make_opencode_subagent_pid_source(registry)
    registry.register(55555, source="opencode", now=0.0)
    assert source.known_subagent_pids() == {55555}


def test_invoke_options_with_shared_registry_preserves_unrelated_fields() -> None:
    options = InvokeOptions(
        model_flag="-m sonnet",
        session_id="sess-abc",
        workspace_path=None,
        show_progress=True,
        required_artifact=None,
    )
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    assert threaded.model_flag == "-m sonnet"
    assert threaded.session_id == "sess-abc"
    assert threaded.workspace_path is None
    assert threaded.show_progress is True


def test_partial_shared_registry_falls_back_to_internal_build() -> None:
    options = InvokeOptions()
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


def _assert_shared_registry_supported_for_transport(transport: AgentTransport) -> None:
    registry, source = AgentRegistry().build_subagent_pid_registry(transport)
    transport_name = transport.value
    source_label = transport_name
    pid = 70000 + (hash(transport_name) % 1000)
    registry.register(
        pid,
        source=source_label,
        now=0.0,
    )
    assert source.known_subagent_pids() == {pid}


def test_shared_registry_supported_for_every_transport() -> None:

    for transport in [
        AgentTransport.OPENCODE,
        AgentTransport.CLAUDE,
        AgentTransport.PI,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
    ]:
        _assert_shared_registry_supported_for_transport(transport)


@pytest.mark.parametrize("transport", [AgentTransport.AGY], ids=["agy"])
def test_shared_registry_supported_for_every_transport_agy(
    transport: AgentTransport,
) -> None:
    _assert_shared_registry_supported_for_transport(transport)


def test_single_mcp_tool_call_then_quiet_with_fresh_corroborator_does_not_fire() -> None:

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


def test_subagent_silence_with_stale_corroborator_fires() -> None:

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


def test_gate_fires_on_silent_subagent() -> None:
    watchdog, clock = _silent_subagent_fires_make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    assert watchdog.classify_stuck_now(now=now, idle_elapsed=181.0) == (StuckKind.SILENT_SUBAGENT)

    gate_verdict = watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        "Gate MUST FIRE on SILENT_SUBAGENT: the branch requires alive_by is None,"
        f" so there is no live child to protect. Got {gate_verdict}."
    )


def test_silent_subagent_fire_is_not_recorded_as_a_deferral() -> None:
    watchdog, clock = _silent_subagent_fires_make_watchdog()
    now = _wedge(watchdog, clock, silent_for=181.0)

    watchdog.evaluate_fire_gate(WatchdogFireReason.NO_OUTPUT_DEADLINE, now=now, idle_elapsed=181.0)
    assert watchdog.last_deferred_kind is None, (
        "A FIRE is not a deferral; last_deferred_kind must not be stamped."
        f" Got {watchdog.last_deferred_kind}."
    )
    assert watchdog.last_fire_reason != WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER


def test_silence_never_becomes_permanent_immunity() -> None:
    for silent_for in (61.0, 181.0, 600.0, 3600.0, 86_400.0):
        watchdog, clock = _silent_subagent_fires_make_watchdog()
        now = _wedge(watchdog, clock, silent_for=silent_for)

        gate_verdict = watchdog.evaluate_fire_gate(
            WatchdogFireReason.NO_OUTPUT_DEADLINE,
            now=now,
            idle_elapsed=silent_for,
        )
        assert gate_verdict == WatchdogVerdict.FIRE, (
            f"Silence of {silent_for}s must FIRE, not defer. A longer stall can"
            " never be MORE deserving of protection than a shorter one."
        )


def test_silent_subagent_seconds_is_threaded_into_runtime_classifier() -> None:
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    kind = watchdog.classify_stuck_now(now=now, idle_elapsed=181.0)
    assert kind == StuckKind.SILENT_SUBAGENT, (
        f"Expected SILENT_SUBAGENT when subagent_progress is stale and"
        f" classify_quiet is ACTIVE; got {kind}"
    )


def test_silent_subagent_disabled_when_silent_subagent_seconds_is_none() -> None:
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=None)
    watchdog.record_invocation_start()

    clock.advance(30.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(1000.0)
    now = clock.monotonic()

    kind = watchdog.classify_stuck_now(now=now, idle_elapsed=1000.0)
    assert kind != StuckKind.SILENT_SUBAGENT, (
        f"Expected the SILENT_SUBAGENT diagnostic to be DISABLED when"
        f" silent_subagent_seconds is None; got {kind}"
    )


def test_gate_fires_on_silent_subagent_and_does_not_record_a_deferral() -> None:
    watchdog, clock = _silent_subagent_runtime_make_watchdog(
        silent_subagent_seconds=180.0,
        activity_evidence_ttl_seconds=30.0,
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")

    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    gate_verdict = watchdog.evaluate_fire_gate(
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


def test_last_deferred_kind_is_none_when_no_fire_deferred() -> None:
    watchdog, _clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    assert watchdog.last_deferred_kind is None
    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None


def test_last_deferred_kind_resets_on_invocation_start() -> None:
    watchdog, clock = _silent_subagent_runtime_make_watchdog(silent_subagent_seconds=180.0)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    watchdog.record_subagent_work(description="tool_use:Bash")
    clock.advance(180.0 + 1.0)
    now = clock.monotonic()

    watchdog.set_is_waiting_state(True)
    watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=now,
        idle_elapsed=clock.monotonic(),
    )
    assert watchdog.last_deferred_kind == StuckKind.DUPLICATE_KILL

    watchdog.record_invocation_start()
    assert watchdog.last_deferred_kind is None


def test_dumb_kill_one_agent_reading_product_criteria() -> None:
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
    first = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD,
        activity_evidence_ttl_seconds=wd.config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING, (
        f"classifier should still return LOADING for the live subagent;"
        f" the R3 cumulative ceiling fires regardless of the classifier"
        f" verdict; got {kind!r}"
    )


def test_children_persist_deferred_while_classifier_returns_loading() -> None:
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

    first = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(300.0)

    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire unconditionally past the"
        f" effective ceiling (R3 hard enforcement); got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_absolute_ceiling_bypasses_gate_with_waiting_state() -> None:
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    wd.set_is_waiting_state(True)
    verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_no_duplicate_fire_across_many_evaluate_calls() -> None:
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=5.0,
            activity_ttl=30.0,
        ),
    )
    wd.record_activity()

    clock.advance(10.0)

    first_fire_count = 0
    for _ in range(1000):
        verdict = wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
        if verdict == WatchdogVerdict.FIRE:
            first_fire_count += 1
            break

    assert first_fire_count == 1

    wd2, clock2 = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(
            idle_timeout=1.0,
            max_session=None,
            activity_ttl=30.0,
        ),
    )
    wd2.record_activity()
    clock2.advance(10.0)

    verdict1 = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
    assert verdict1 == WatchdogVerdict.FIRE

    wd2.set_is_waiting_state(True)
    for _ in range(100):
        verdict = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE on duplicate evaluate call, got {verdict}"
        )

    wd2.set_is_waiting_state(False)
    verdict = wd2.evaluate(classify_quiet=_smart_verdict_dumb_kills_active)
    assert verdict == WatchdogVerdict.FIRE


def test_waiting_state_makes_fire_into_duplicate_kill() -> None:
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


def test_classifier_consulted_live_callable_returns_loading() -> None:
    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(activity_ttl=30.0)
    )
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_smart_verdict_dumb_kills_waiting)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_smart_verdict_dumb_kills_waiting,
        activity_evidence_ttl_seconds=wd.config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.LOADING


def test_classifier_consulted_live_callable_returns_transitioning() -> None:

    def _resumable() -> AgentExecutionState:
        return AgentExecutionState.RESUMABLE_CONTINUE

    wd, clock = _smart_verdict_dumb_kills_make_watchdog(
        _smart_verdict_dumb_kills_make_policy(activity_ttl=30.0)
    )
    wd.record_activity()
    clock.advance(2.0)
    wd.evaluate(classify_quiet=_resumable)
    summary = wd.last_evidence_summary(clock.monotonic())
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state=None,
        evidence_summary=summary,
        classify_quiet=_resumable,
        activity_evidence_ttl_seconds=wd.config.activity_evidence_ttl_seconds,
    )
    assert kind == StuckKind.TRANSITIONING


def test_stall_lifetime_regression_invocation_end_clears_active_stall() -> None:
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _stall_lifetime_watchdog(clock, events)
    watchdog.record_invocation_start()
    watchdog.set_stall_state(active=True, now=1.0, idle_elapsed=1.0)

    clock.advance(2.0)
    watchdog.record_invocation_end()

    assert [event.kind for event in events] == [
        WaitingStatusKind.STALLED,
        WaitingStatusKind.STALL_RESUMED,
    ]
    assert events[-1].stall_active is False


def test_stall_assessment_regression_fresh_watchdog_event_reports_not_stalled() -> None:
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _stall_lifetime_watchdog(clock, events)

    watchdog.emit_waiting_status(
        WaitingStatusKind.PROGRESS, current_run_seconds=0.0, idle_elapsed=0.0
    )

    assert events[-1].stall_active is False


def test_is_stalled_initially_false() -> None:
    watchdog, _clock = _stall_status_events_make_watchdog()
    assert _stall_state(watchdog) is False


def test_set_stall_emits_stalled_transition_only_once() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1
    assert _stall_state(watchdog) is True


def test_set_stall_repeated_active_emits_no_duplicates() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    for _ in range(10):
        watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"STALLED must be emitted only on transition; got {len(stalled_events)}"
    )


def test_set_stall_toggle_emits_stall_resumed_once() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
    for _ in range(10):
        watchdog.set_stall_state(active=False, now=200.0, idle_elapsed=0.0)
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


def test_set_stall_idempotent_false_emits_no_event() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.set_stall_state(active=False, now=100.0, idle_elapsed=0.0)
    assert _events(captured) == []


def test_suspected_frozen_emits_stalled_event() -> None:
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

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    assert _stall_state(watchdog) is False
    assert any(e.kind == WaitingStatusKind.ENTERED for e in _events(captured))

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

    watchdog.evaluate(classify_quiet=_waiting)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    suspect_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(stalled_events) == 1
    assert len(suspect_events) == 1


def test_fire_verdict_emits_stalled_event() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(
        listener=captured.append,
        drain_window_seconds=0.0,
    )
    _classifier_to_stuck_now(watchdog)

    _clock.advance(61.0)
    verdict = watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert _stall_state(watchdog) is True

    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED event on FIRE; got {len(stalled_events)}: "
        f"{[e.kind for e in _events(captured)]}"
    )

    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


def test_silent_subagent_emits_stalled_event() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)

    _attr = "_classify_stuck_now"

    def _silent_subagent_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.SILENT_SUBAGENT

    setattr(watchdog, _attr, _silent_subagent_now)

    _now = clock.monotonic() + 181.0
    gate_verdict = watchdog.evaluate_fire_gate(
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

    gate_verdict = watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=_now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated _gate_fire must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


def test_fire_session_ceiling_emits_stalled_event() -> None:
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

    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() on SESSION_CEILING_EXCEEDED MUST NOT emit duplicate "
        f"STALLED events; got {len(stalled_events)}"
    )


def test_activity_triggers_emit_stall_resumed() -> None:
    for trigger in ("record_activity", "record_invocation_start"):
        captured: list[WaitingStatusEvent] = []
        watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
        watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
        assert _stall_state(watchdog) is True
        captured.clear()
        if trigger == "record_activity":
            watchdog.record_activity()
        else:
            watchdog.record_invocation_start()
        resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
        assert len(resumed_events) == 1, f"{trigger} must emit exactly one STALL_RESUMED"
        assert _stall_state(watchdog) is False


def test_accumulate_waiting_run_emits_stall_resumed() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)
    clock.advance(61.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
    captured.clear()
    clock.advance(1.0)
    watchdog.record_error_activity("stalled while waiting on a child")
    exited_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.EXITED]
    assert len(exited_events) == 1
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


def test_no_stall_event_emitted_when_already_idle() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.record_activity()
    watchdog.record_activity()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 0


def test_stall_oscillation_emits_only_on_transitions() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    for i in range(5):
        watchdog.set_stall_state(active=True, now=float(i * 100), idle_elapsed=100.0)
        watchdog.set_stall_state(active=False, now=float(i * 100 + 50), idle_elapsed=0.0)
    for _ in range(100):
        watchdog.set_stall_state(active=True, now=10_000.0, idle_elapsed=100.0)
    for _ in range(100):
        watchdog.set_stall_state(active=False, now=10_500.0, idle_elapsed=0.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(stalled) == 6, (
        f"Expected exactly 6 STALLED events (5 transitions + 1 final), got {len(stalled)}"
    )
    assert len(resumed) == 6, (
        f"Expected exactly 6 STALL_RESUMED events (5 transitions + 1 final), got {len(resumed)}"
    )


def test_is_stalled_property_reflects_internal_state() -> None:
    watchdog, _clock = _stall_status_events_make_watchdog()
    assert watchdog.is_stalled is False
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=100.0)
    assert watchdog.is_stalled is True
    watchdog.set_stall_state(active=False, now=200.0, idle_elapsed=0.0)
    assert watchdog.is_stalled is False


def test_gate_deferral_clears_silent_subagent_stall_on_non_stuck_tick() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _stall_status_events_make_watchdog(listener=captured.append)

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

    first_now = clock.monotonic() + 181.0
    first_verdict = watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=181.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    stalled_after_first = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_after_first) == 1
    assert _stall_state(watchdog) is True

    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog.evaluate_fire_gate(
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

    third_now = clock.monotonic() + 1.0
    third_verdict = watchdog.evaluate_fire_gate(
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


def test_gate_deferral_does_not_clear_stuck_or_fire_stall() -> None:
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
    first_verdict = watchdog.evaluate_fire_gate(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=60.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    assert _stall_state(watchdog) is True

    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog.evaluate_fire_gate(
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


def test_set_stall_emitted_event_carries_idle_elapsed_seconds() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=42.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled) == 1
    assert stalled[0].idle_elapsed_seconds == 42.0
    assert stalled[0].current_run_seconds == 0.0


def test_set_stall_resumed_emitted_event_carries_idle_elapsed_seconds() -> None:
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _stall_status_events_make_watchdog(listener=captured.append)
    watchdog.set_stall_state(active=True, now=100.0, idle_elapsed=42.0)
    watchdog.set_stall_state(active=False, now=200.0, idle_elapsed=37.0)
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed) == 1
    assert resumed[0].idle_elapsed_seconds == 37.0
    assert resumed[0].current_run_seconds == 0.0


def test_strictly_stuck_enum_exists() -> None:
    assert hasattr(WatchdogFireReason, "STRICTLY_STUCK"), (
        "WatchdogFireReason.STRICTLY_STUCK missing; the new fire"
        " reason for stuck-but-alive jobs is required"
    )
    assert WatchdogFireReason.STRICTLY_STUCK.value == "strictly_stuck", (
        f"WatchdogFireReason.STRICTLY_STUCK.value must be"
        f" 'strictly_stuck'; got {WatchdogFireReason.STRICTLY_STUCK.value!r}"
    )


def test_strictly_stuck_fires_when_alive_by_pure_descendant_stale() -> None:
    wd, clock = _strictly_stuck_ceiling_make_watchdog(
        strictly_stuck_seconds=300.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    clock.advance(305.0)
    verdict = wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"STRICTLY_STUCK MUST fire at invocation elapsed = 305 s"
        f" with alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.STRICTLY_STUCK, (
        f"expected WatchdogFireReason.STRICTLY_STUCK; got {wd.last_fire_reason}"
    )


def test_strictly_stuck_does_not_fire_before_ceiling() -> None:
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


def test_strictly_stuck_disabled_when_none() -> None:
    wd, clock = _strictly_stuck_ceiling_make_watchdog(
        strictly_stuck_seconds=None,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(605.0)
    verdict = wd.evaluate(classify_quiet=_strictly_stuck_ceiling_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"STRICTLY_STUCK MUST be disabled when the field is None; got {verdict}"
    )


def test_is_waiting_state_true_returns_duplicate_kill() -> None:
    kind = classify_stuck(**_inputs(is_waiting_state=True))
    assert kind == StuckKind.DUPLICATE_KILL


def test_offline_connectivity_returns_waiting_on_connectivity() -> None:
    kind = classify_stuck(**_inputs(connectivity_state="offline"))
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


def test_fresh_subagent_output_returns_thinking() -> None:
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.THINKING


def test_fresh_subagent_liveness_without_first_party_returns_loading() -> None:
    summary = _multi_summary(
        subagent_liveness_at=_NOW - 5.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    kind = classify_stuck(**_inputs(evidence_summary=summary))
    assert kind == StuckKind.LOADING


def test_os_descendant_alive_no_fresh_channels_returns_loading() -> None:
    summary = _multi_summary(alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS)
    kind = classify_stuck(
        **_inputs(
            evidence_summary=summary,
            classify_quiet_state=AgentExecutionState.WAITING_ON_CHILD,
        )
    )
    assert kind == StuckKind.LOADING


def test_resumable_continue_returns_transitioning() -> None:
    kind = classify_stuck(**_inputs(classify_quiet_state=AgentExecutionState.RESUMABLE_CONTINUE))
    assert kind == StuckKind.TRANSITIONING


def test_no_channels_active_returns_stuck() -> None:
    kind = classify_stuck(**_inputs())
    assert kind == StuckKind.STUCK


def test_classify_stuck_is_pure() -> None:
    inputs = _inputs(connectivity_state="offline")
    kind1 = classify_stuck(**inputs)
    kind2 = classify_stuck(**inputs)
    assert kind1 == kind2
    assert kind1 == StuckKind.WAITING_ON_CONNECTIVITY


def test_priority_order_waiting_beats_offline() -> None:
    kind = classify_stuck(**_inputs(is_waiting_state=True, connectivity_state="offline"))
    assert kind == StuckKind.DUPLICATE_KILL


def test_priority_order_offline_beats_thinking() -> None:
    summary = _multi_summary(subagent_output_at=_NOW - 5.0)
    kind = classify_stuck(**_inputs(connectivity_state="offline", evidence_summary=summary))
    assert kind == StuckKind.WAITING_ON_CONNECTIVITY


def test_corroboration_is_plumbed_but_does_not_change_stuck_verdict() -> None:
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


def test_corroboration_does_not_change_thinking_verdict() -> None:
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


def test_corroboration_does_not_change_loading_verdict_via_subagent_liveness() -> None:
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


def test_corroboration_does_not_change_offline_verdict() -> None:
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


def test_corroboration_does_not_change_duplicate_kill_verdict() -> None:
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


def test_silent_subagent_when_progress_count_ge_1_and_stale() -> None:
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
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.SILENT_SUBAGENT


def test_no_silent_subagent_when_subagent_progress_is_fresh() -> None:
    summary = _multi_summary(
        subagent_output_at=_NOW - 5.0,
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


def test_no_silent_subagent_when_no_subagent_evidence() -> None:
    inputs = _inputs()
    kind = classify_stuck(
        **inputs,
        silent_subagent_seconds=180.0,
    )
    assert kind == StuckKind.STUCK


def test_silent_subagent_disabled_when_silent_subagent_seconds_is_none_via_classify_stuck() -> None:
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
    assert kind == StuckKind.STUCK


def test_silent_subagent_does_not_change_when_waiting() -> None:
    summary = _multi_summary(
        subagent_output_at=_NOW - 1000.0,
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
    assert kind == StuckKind.LOADING


def test_no_silent_subagent_when_subagent_liveness_alive_by_is_not_none() -> None:
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


def test_no_silent_subagent_when_only_liveness_alive_by_not_none() -> None:
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


def test_heartbeat_only_trip() -> None:
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


def test_heartbeat_ceiling_does_not_trip_before_threshold() -> None:
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


def test_fresh_progress_deferral_preserved() -> None:
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


def test_heartbeat_ceiling_disabled_when_none() -> None:
    wd, clock = _stuck_job_heartbeat_ceiling_make_watchdog(
        heartbeat_ceiling_seconds=None,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(100.0)
    verdict = wd.evaluate(classify_quiet=_stuck_job_heartbeat_ceiling_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST be disabled when the field is None; got {verdict}"
    )


def test_heartbeat_only_ceiling_fires_before_dumb_kill_ceiling() -> None:
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


def test_heartbeat_only_ceiling_respects_dumb_kill_floor() -> None:
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


def test_heartbeat_only_trip_when_no_progress_quiet_seconds_disabled() -> None:
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


def test_stuck_classifier_consulted_at_no_output_at_start_fire() -> None:
    config = _stuck_job_intelligence_make_policy(idle_timeout=300.0, no_output_at_start=30.0)
    clock = FakeClock(start=0.0)

    def _empty_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot()

    watchdog = _stuck_job_intelligence_make_watchdog(
        config, clock, corroborator=_empty_corroborator
    )
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_stuck_classifier_returns_loading_defers_no_output_at_start() -> None:
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


def test_stuck_classifier_returns_offline_defers_no_output_at_start() -> None:
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


def test_children_persist_too_long_uses_live_corroboration_alive_by() -> None:
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

    watchdog = _stuck_job_intelligence_make_watchdog(
        config,
        clock,
        corroborator=_live_corroborator,
    )
    watchdog.record_invocation_start()
    watchdog.record_activity()

    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

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
    assert call_count[0] >= 1, (
        f"expected corroborator to be invoked at least once during the"
        f" CHILDREN_PERSIST_TOO_LONG fire decision, got {call_count[0]}"
    )


def test_children_persist_too_long_stale_corroboration_does_not_defeat_ceiling() -> None:
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

    clock.advance(2.0)
    first = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(15.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected CHILDREN_PERSIST_TOO_LONG to FIRE (stale OS_DESCENDANT"
        f" signal triggers no_progress ceiling), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_no_progress_quiet_stuck_when_corroborator_says_dead() -> None:
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

    clock.advance(150.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE (corroborator says dead, no fresh channels), got verdict={verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET
    assert watchdog.last_alive_by is None, (
        f"expected last_alive_by=None (truly dead child), got {watchdog.last_alive_by}"
    )


def test_stuck_classifier_module_exposes_seven_kinds() -> None:
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


def test_stuck_job_sub_ceiling_fires_at_600s_when_alive_by_is_stale() -> None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        _stuck_job_sub_ceiling_make_policy(stuck_job_sub_ceiling_seconds=600.0),
        clock,
        corroborator=_make_stuck_corroborator(),
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
        f"stuck_job_sub_ceiling MUST fire at 600s with stale alive_by; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"expected CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


def test_stuck_job_sub_ceiling_fires_for_every_stale_alive_by() -> None:

    for alive_by in (
        AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
        AliveBy.STALE_LABEL_ONLY,
    ):
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


def test_stuck_job_sub_ceiling_does_not_fire_when_alive_by_is_fresh() -> None:

    for alive_by in (AliveBy.FRESH_PROGRESS, AliveBy.FRESH_HEARTBEAT_ONLY):
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


def test_stuck_job_sub_ceiling_disabled_when_none() -> None:
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

    clock.advance(499.0)
    verdict = watchdog.evaluate(classify_quiet=_stuck_job_sub_ceiling_waiting_on_child)

    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"with sub-ceiling disabled, the gate MUST NOT fire at 700s; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None


def test_timeout_policy_default_stuck_job_sub_ceiling_matches_constant() -> None:
    policy = TimeoutPolicy(idle_timeout_seconds=200.0)

    assert policy.stuck_job_sub_ceiling_seconds == STUCK_JOB_SUB_CEILING_SECONDS
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


def test_stuck_job_sub_ceiling_validated_positive_and_bounded() -> None:
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=-1.0,
        )
    with pytest.raises(ValueError, match="stuck_job_sub_ceiling_seconds must be positive"):
        TimeoutPolicy(
            idle_timeout_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
            stuck_job_sub_ceiling_seconds=0.0,
        )
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
    policy = TimeoutPolicy(
        idle_timeout_seconds=200.0,
        stuck_job_sub_ceiling_seconds=600.0,
    )
    assert policy.stuck_job_sub_ceiling_seconds == 600.0


def test_stuck_job_sub_ceiling_fires_when_scoped_child_inactive() -> None:

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

    assert verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"sub-ceiling MUST NOT fire when scoped_child_active=False; got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None


def test_subagent_capture_cache_is_hard_bounded_by_cap() -> None:
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog.subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at exactly cap={cap}, "
        f"got {len(watchdog.subagent_output_captures)}"
    )
    assert len(watchdog.evicted_worker_tombstones) == _OVERFLOW_DELTA, (
        f"the {_OVERFLOW_DELTA} evicted workers MUST be tombstoned, "
        f"got {len(watchdog.evicted_worker_tombstones)}"
    )

    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog.subagent_output_captures) == cap
    assert len(watchdog.evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries for workers no longer in "
        "discovery (the eviction cooldown ended because the worker "
        "actually died)"
    )
    expected_survivors = {f"w-{i}" for i in range(_OVERFLOW_DELTA, first_count)}
    assert expected_survivors == set(watchdog.subagent_output_captures.keys()), (
        f"every cap survivor MUST be retained in the cache; "
        f"missing={expected_survivors - set(watchdog.subagent_output_captures.keys())}, "
        f"extra={set(watchdog.subagent_output_captures.keys()) - expected_survivors}"
    )


def test_subagent_capture_cache_does_not_evict_when_under_cap() -> None:
    cap = _probe_cache_cap()
    under_cap_count = cap // 2
    captures = {f"keep-{i}": _StaticCaptureEmpty() for i in range(under_cap_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)

    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog.subagent_output_captures) == under_cap_count
    assert len(watchdog.evicted_worker_tombstones) == 0
    for index in range(under_cap_count):
        assert f"keep-{index}" in watchdog.subagent_output_captures


def test_subagent_capture_cache_eviction_skips_existing_workers() -> None:
    cap = _probe_cache_cap()

    primed = {f"primed-{i}": _StaticCaptureEmpty() for i in range(cap)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(primed)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog.subagent_output_captures) == cap
    assert len(watchdog.evicted_worker_tombstones) == 0

    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog.subagent_output_captures) == cap
    assert len(watchdog.evicted_worker_tombstones) == 0


def test_subagent_capture_cache_polls_all_workers_then_enforces_cap() -> None:
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    captures = {f"w-{i}": _StaticCapture() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)

    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())

    assert fresh == first_count
    assert watchdog.subagent_output_count == first_count
    assert len(watchdog.subagent_output_captures) == cap, (
        f"hard cap MUST enforce cache at cap={cap}, got {len(watchdog.subagent_output_captures)}"
    )
    assert len(watchdog.evicted_worker_tombstones) == _OVERFLOW_DELTA


def test_subagent_capture_tombstone_prevents_duplicate_output_after_eviction() -> None:
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    line_count_per_worker = 3
    captures: dict[str, _StatefulCapture] = {}
    for i in range(first_count):
        lines = [f"line-{j}-worker-{i}" for j in range(line_count_per_worker)]
        captures[f"w-{i}"] = _StatefulCapture(lines)

    monitor = _FakeProcessMonitorSubagentCaptureEviction(captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)

    first_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    expected_first = first_count * line_count_per_worker
    assert first_poll == expected_first, (
        f"first poll MUST report every line from every worker, got {first_poll} "
        f"vs expected {expected_first}"
    )
    assert len(watchdog.subagent_output_captures) == cap
    assert len(watchdog.evicted_worker_tombstones) == _OVERFLOW_DELTA

    clock.advance(0.01)
    second_poll = watchdog.poll_subagent_output(now=clock.monotonic())
    assert second_poll == 0, (
        f"second poll on the same live workers MUST report 0 new lines "
        f"(stateful capture read positions must be honored AND "
        f"tombstoned workers must be skipped); got {second_poll}"
    )
    assert len(watchdog.subagent_output_captures) == cap
    assert len(watchdog.evicted_worker_tombstones) == _OVERFLOW_DELTA


def test_subagent_capture_tombstone_cycles_out_when_worker_dies() -> None:
    cap = _probe_cache_cap()
    first_count = cap + _OVERFLOW_DELTA

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog.evicted_worker_tombstones) == _OVERFLOW_DELTA

    surviving = {f"w-{i}": _StaticCaptureEmpty() for i in range(_OVERFLOW_DELTA, first_count)}
    monitor.replace_captures(surviving)
    clock.advance(0.01)
    watchdog.poll_subagent_output(now=clock.monotonic())
    assert len(watchdog.evicted_worker_tombstones) == 0, (
        "tombstone MUST release entries when the worker actually exits (no longer in discovery)"
    )


def test_subagent_capture_tombstone_is_itself_bounded() -> None:
    cap = _probe_cache_cap()
    tombstone_cap = _probe_tombstone_cap()
    first_count = cap + cap + 1

    first_captures = {f"w-{i}": _StaticCaptureEmpty() for i in range(first_count)}
    monitor = _FakeProcessMonitorSubagentCaptureEviction(first_captures)
    watchdog, clock = _subagent_capture_eviction_make_watchdog(monitor)
    watchdog.poll_subagent_output(now=clock.monotonic())

    assert len(watchdog.evicted_worker_tombstones) == tombstone_cap
    assert len(watchdog.subagent_output_captures) == cap
    num_evicted = first_count - cap
    tombstoned = list(watchdog.evicted_worker_tombstones.keys())
    assert tombstoned == [f"w-{i}" for i in range(num_evicted - tombstone_cap, num_evicted)]


def test_helper_processes_alone_yield_zero_filtered_count() -> None:
    monitor = _FilteredCountMonitor(
        filtered_count=0,
        descendant_snapshot_count=10,
    )
    assert monitor.spawned_subagent_count() == 0
    assert monitor.live_subagent_count() == 0
    assert monitor.descendant_snapshot()[0] == 10


def test_spawned_subagent_count_equals_live_subagent_count() -> None:
    monitor = _FilteredCountMonitor(filtered_count=3)
    assert monitor.spawned_subagent_count() == 3
    assert monitor.live_subagent_count() == 3
    assert monitor.spawned_subagent_count() == monitor.live_subagent_count()


def test_subagent_pid_registry_bounded_at_max_entries() -> None:
    assert MAX_SUBAGENT_REGISTRY_ENTRIES == 1024
    registry = SubagentPidRegistry()
    for pid in range(1_000_001, 1_002_001):
        registry.register(pid, source="opencode", now=float(pid))
    snapshot = registry.snapshot()
    assert len(snapshot) == MAX_SUBAGENT_REGISTRY_ENTRIES == 1024
    surviving_pids = [identity.pid for identity in snapshot]
    assert surviving_pids == list(range(1_000_977, 1_002_001))
    assert 1_000_001 not in registry.known_pids()
    assert 1_000_976 not in registry.known_pids()
    assert 1_000_977 in registry.known_pids()
    assert 1_002_000 in registry.known_pids()


def test_register_is_idempotent() -> None:
    registry = SubagentPidRegistry()
    first = registry.register(1234, source="opencode", now=10.0)
    second = registry.register(1234, source="opencode", now=999.0)
    third = registry.register(1234, source="claude", now=42.0)
    assert first.registered_at_monotonic == 10.0
    assert second.registered_at_monotonic == 10.0
    assert third.registered_at_monotonic == 10.0
    assert len(registry.snapshot()) == 1
    assert registry.snapshot()[0].source == "opencode"


def test_unregister_removes_pid() -> None:
    registry = SubagentPidRegistry()
    registry.register(1234, source="opencode", now=0.0)
    assert 1234 in registry.known_pids()
    assert len(registry.snapshot()) == 1
    registry.unregister(1234)
    assert 1234 not in registry.known_pids()
    assert len(registry.snapshot()) == 0
    registry.unregister(9999)
    assert 9999 not in registry.known_pids()


def test_spawned_subagent_count_filters_out_unregistered_descendants() -> None:
    registry = SubagentPidRegistry()
    registry.register(7001, source="opencode", now=0.0)
    source = _RegistryPidSource(registry, source_label="opencode")
    assert source.known_subagent_pids() == {7001}
    assert len(source.known_subagent_pids()) == 1
    broader_count = 1 + 3
    assert len(source.known_subagent_pids()) < broader_count


def test_subagent_identity_rejects_invalid_source() -> None:
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


def test_process_monitor_protocol_includes_spawned_subagent_count() -> None:
    assert hasattr(ProcessMonitor, "spawned_subagent_count")
    assert hasattr(ProcessMonitor, "live_subagent_count")


def test_filtered_count_seam_is_isolated_per_transport() -> None:
    registry = SubagentPidRegistry()
    registry.register(8001, source="claude", now=0.0)
    registry.register(8002, source="opencode", now=0.0)
    opencode_source = make_opencode_subagent_pid_source(registry)
    assert opencode_source.known_subagent_pids() == {8002}
    claude_source = make_claude_subagent_pid_source(registry)
    assert claude_source.known_subagent_pids() == {8001}


def test_last_subagent_progress_description_property_returns_recorded_value() -> None:
    watchdog, _clock = _subagent_progress_surface_make_watchdog()
    watchdog.record_invocation_start()

    assert watchdog.last_subagent_progress_description is None

    watchdog.record_subagent_work(description="agent is reading foo.py")
    assert watchdog.last_subagent_progress_description == "agent is reading foo.py"

    watchdog.record_subagent_work(description="agent is writing bar.py")
    assert watchdog.last_subagent_progress_description == "agent is writing bar.py"

    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_description is None


def test_register_default_subagent_activity_listener_invokes_listener_with_payload() -> None:
    watchdog, _clock = _subagent_progress_surface_make_watchdog()
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="agent is reading foo.py")
    watchdog.emit_waiting_status(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )

    assert len(captured) == 1
    assert captured[0].subagent_activity == "agent is reading foo.py"
    assert captured[0].kind == WaitingStatusKind.ENTERED

    watchdog.emit_waiting_status(
        WaitingStatusKind.ENTERED,
        current_run_seconds=1.0,
        idle_elapsed=1.0,
        ceiling_seconds=60.0,
    )
    assert len(captured) == 2
    assert captured[1].subagent_activity == "agent is reading foo.py"

    watchdog.record_invocation_start()
    captured.clear()
    watchdog.emit_waiting_status(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )
    assert captured == []


def test_default_no_progress_quiet_minimum_invocation_seconds_matches_constant() -> None:
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    assert policy.no_progress_quiet_minimum_invocation_seconds == (
        NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS
    )
    assert policy.no_progress_quiet_minimum_invocation_seconds == 120.0


def test_constructor_accepts_none_as_disabled() -> None:
    floor_disabled = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=None,
    )
    assert floor_disabled.no_progress_quiet_minimum_invocation_seconds is None

    heartbeat_disabled = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
    )
    assert heartbeat_disabled.no_progress_quiet_heartbeat_ceiling_seconds is None


def test_constructor_rejects_non_positive_floor() -> None:
    for invalid in (0.0, -1.0):
        with pytest.raises(ValueError, match="must be positive when set"):
            TimeoutPolicy(
                idle_timeout_seconds=300.0,
                no_progress_quiet_minimum_invocation_seconds=invalid,
            )


def test_constructor_rejects_non_positive_heartbeat_ceiling() -> None:
    for invalid in (0.0, -1.0):
        with pytest.raises(ValueError, match="must be positive when set"):
            TimeoutPolicy(
                idle_timeout_seconds=300.0,
                no_progress_quiet_heartbeat_ceiling_seconds=invalid,
            )


def test_constructor_floor_ceiling_relationships() -> None:
    positive_floor = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert positive_floor.no_progress_quiet_minimum_invocation_seconds == 60.0

    with pytest.raises(ValueError, match="must be <= no_progress_quiet_seconds"):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_minimum_invocation_seconds=200.0,
        )

    equal_to_ceiling = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=120.0,
        no_progress_quiet_heartbeat_ceiling_seconds=120.0,
    )
    assert equal_to_ceiling.no_progress_quiet_minimum_invocation_seconds == 120.0
    assert equal_to_ceiling.no_progress_quiet_seconds == 120.0

    with_none_ceiling = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=60.0,
    )
    assert with_none_ceiling.no_progress_quiet_seconds is None
    assert with_none_ceiling.no_progress_quiet_minimum_invocation_seconds == 60.0


def test_default_no_progress_quiet_heartbeat_ceiling_seconds_matches_constant() -> None:
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    assert policy.no_progress_quiet_heartbeat_ceiling_seconds == 240.0


def test_constructor_heartbeat_ceiling_relationships() -> None:
    with pytest.raises(
        ValueError,
        match="no_progress_quiet_heartbeat_ceiling_seconds must be <=",
    ):
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_progress_quiet_seconds=120.0,
            no_progress_quiet_heartbeat_ceiling_seconds=240.0,
        )

    equal_to_ceiling = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert equal_to_ceiling.no_progress_quiet_heartbeat_ceiling_seconds == 240.0
    assert equal_to_ceiling.no_progress_quiet_seconds == 240.0

    below_ceiling = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=240.0,
        no_progress_quiet_heartbeat_ceiling_seconds=180.0,
    )
    assert below_ceiling.no_progress_quiet_heartbeat_ceiling_seconds == 180.0
    assert below_ceiling.no_progress_quiet_seconds == 240.0

    with_none_ceiling = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=240.0,
    )
    assert with_none_ceiling.no_progress_quiet_seconds is None
    assert with_none_ceiling.no_progress_quiet_heartbeat_ceiling_seconds == 240.0


def test_known_tool_call_verbs_set_is_twelve_members() -> None:
    assert isinstance(KNOWN_TOOL_CALL_VERBS, frozenset), (
        f"KNOWN_TOOL_CALL_VERBS MUST be a frozenset for immutability;"
        f" got {type(KNOWN_TOOL_CALL_VERBS).__name__}"
    )
    assert len(KNOWN_TOOL_CALL_VERBS) == 12, (
        f"KNOWN_TOOL_CALL_VERBS MUST contain exactly 12 verbs (R5);"
        f" got {len(KNOWN_TOOL_CALL_VERBS)}: {sorted(KNOWN_TOOL_CALL_VERBS)}"
    )
    assert KNOWN_TOOL_CALL_VERBS == _EXPECTED_CANONICAL_VERBS, (
        f"KNOWN_TOOL_CALL_VERBS mismatch; expected="
        f"{sorted(_EXPECTED_CANONICAL_VERBS)}, got="
        f"{sorted(KNOWN_TOOL_CALL_VERBS)}"
    )


def test_parse_returns_canonical_verb_for_known_prefix() -> None:

    for description, expected in [
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
    ]:
        result = parse_tool_call_from_description(description)
        assert result == expected, (
            f"description={description!r}: expected {expected!r}, got {result!r}"
        )


def test_parse_returns_none_for_edge_cases() -> None:

    for description, expected in [
        (None, None),
        ("", None),
        ("no_colon", None),
        ("tool_use:", "tool_use"),
        ("unknown_verb:Read", None),
        ("tool_use:Read:something", "tool_use"),
        ("tool_use:<redacted>", "tool_use"),
        ("tool_use:/etc/<redacted>", "tool_use"),
        ("[subagent] progress: phase=1", None),
        ('{"type": "child_progress"}', None),
        ('"subagent_activity": "tool_use:Read"', None),
    ]:
        result = parse_tool_call_from_description(description)
        assert result == expected, (
            f"description={description!r}: expected {expected!r}, got {result!r}"
        )


def test_parse_does_not_mutate_known_verb_set() -> None:
    snapshot_before = set(KNOWN_TOOL_CALL_VERBS)
    for _ in range(100):
        parse_tool_call_from_description("tool_use:Read")
        parse_tool_call_from_description(None)
        parse_tool_call_from_description("")
        parse_tool_call_from_description("unknown_verb:Read")
    snapshot_after = set(KNOWN_TOOL_CALL_VERBS)
    assert snapshot_before == snapshot_after, (
        f"KNOWN_TOOL_CALL_VERBS mutated across calls; before="
        f"{snapshot_before}, after={snapshot_after}"
    )
    assert len(snapshot_after) == 12, (
        f"KNOWN_TOOL_CALL_VERBS size changed across calls; got {len(snapshot_after)} members"
    )


def test_tool_result_does_not_wipe_the_tool_call_streak() -> None:
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


def test_tool_result_is_attributed_to_the_post_tool_result_stall() -> None:
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


def test_tool_result_counts_as_meaningful_output() -> None:
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


def test_subagent_progress_event_kind_exists() -> None:
    assert hasattr(WaitingStatusKind, "SUBAGENT_PROGRESS"), (
        "WaitingStatusKind.SUBAGENT_PROGRESS missing; the watchdog's"
        " waiting-status stream cannot surface per-subagent progress"
        " without this enum value"
    )
    assert WaitingStatusKind.SUBAGENT_PROGRESS.value == "subagent_progress"


def test_subagent_progress_emits_once_when_monitor_has_live_subagents() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=2,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)

    verdict = watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert verdict.value == "waiting_on_child", (
        f"evaluate MUST return WAITING_ON_CHILD when classify_quiet"
        f" returns WAITING_ON_CHILD and idle_timeout elapsed; got {verdict}"
    )
    captured.clear()

    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS emitted before the throttle window"
        f" elapsed; got {len(subagent_emits)} events"
    )

    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"expected exactly 1 SUBAGENT_PROGRESS event after the throttle"
        f" window elapsed; got {len(subagent_emits)}"
    )
    assert subagent_emits[0].diagnostic.get("live_subagent_count") == 2, (
        f"SUBAGENT_PROGRESS diagnostic.live_subagent_count MUST be 2;"
        f" got {subagent_emits[0].diagnostic.get('live_subagent_count')}"
    )


def test_subagent_progress_emits_with_recorded_description() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(description="reading source.py")
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()
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


def test_subagent_progress_does_not_emit_without_evidence() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS MUST NOT emit without evidence; got {len(subagent_emits)} events"
    )


def test_subagent_progress_rate_limit_respected() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    captured.clear()

    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"expected 1 SUBAGENT_PROGRESS emit after first throttle window; got {len(subagent_emits)}"
    )

    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 1, (
        f"second tick within throttle window MUST NOT re-emit; got {len(subagent_emits)}"
    )

    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    subagent_emits = [e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS]
    assert len(subagent_emits) == 2, (
        f"expected 2 SUBAGENT_PROGRESS emits after second throttle"
        f" window; got {len(subagent_emits)}"
    )


def test_subagent_progress_resets_on_record_invocation_start() -> None:
    watchdog, clock, captured = _waiting_subagent_progress_make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured), (
        "first invocation SUBAGENT_PROGRESS missing"
    )

    captured.clear()
    watchdog.record_invocation_start()
    clock.advance(6.0)
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting_subagent_progress_waiting)
    assert any(e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured), (
        "second invocation's first SUBAGENT_PROGRESS emit was"
        " suppressed by stale throttle state from the prior invocation"
    )


def test_wait_until_returns_immediately_when_predicate_true() -> None:
    clock = FakeClock(start=0.0)
    tick_calls: list[int | None] = []

    def _predicate() -> int | None:
        return 42

    class _Watchdog(WatchLoopBase):
        def check(self) -> int | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=30.0,
                poll_interval_s=0.5,
                on_tick=tick_calls.append,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == 42
    assert clock.monotonic() == 0.0
    assert tick_calls == []


def test_wait_until_returns_value_when_predicate_becomes_true_after_ticks() -> None:
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


def test_wait_until_returns_none_on_timeout() -> None:
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


def test_wait_until_calls_on_tick_each_cycle() -> None:
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


def test_signal_activity_wakes_wait_until_in_threaded_context() -> None:
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


def test_wait_until_respects_non_divisible_timeout_boundary() -> None:
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


@pytest.mark.timeout_seconds(5)
def test_no_sys_exit_in_idle_watchdog_or_process_reader() -> None:
    targets = [PROCESS_READER, *IDLE_WATCHDOG_DIR.glob("*.py")]
    for path in targets:
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                if isinstance(exc, ast.Call):
                    func = exc.func
                    if isinstance(func, ast.Name) and func.id == "SystemExit":
                        msg = (
                            f"raise SystemExit at {path}:{node.lineno} -- "
                            "watchdog/process reader must NEVER raise"
                            " SystemExit"
                        )
                        raise AssertionError(msg)
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
                    msg = (
                        f"bare exit() call at {path}:{node.lineno} -- "
                        "watchdog/process reader must NEVER call exit"
                    )
                    raise AssertionError(msg)


def test_teardown_subtree_calls_are_verdict_guarded() -> None:
    tree = _parse(PROCESS_READER)

    def _raises_startup_failure(node: ast.Raise) -> bool:
        exc = node.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        return isinstance(exc, ast.Name) and exc.id == "AgentInvocationError"

    def _has_verdict_check(func: ast.FunctionDef) -> bool:
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
            if isinstance(node, ast.Attribute) and node.attr in {
                "_completion_is_terminal",
                "BROKEN_AGENT_OUTPUT_GRACE_SECONDS",
            }:
                return True
            if isinstance(node, ast.Name) and node.id == "BROKEN_AGENT_OUTPUT_GRACE_SECONDS":
                return True
            if isinstance(node, ast.Raise) and _raises_startup_failure(node):
                return True
        return False

    def _is_handle_terminate_call(node: ast.Call) -> bool:
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
                for arg in []
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


@pytest.mark.timeout_seconds(5)
def test_watchdog_fire_reason_created_only_in_canonical_owners() -> None:
    canonical_owners = {
        IDLE_WATCHDOG_DIR / "idle_watchdog.py",
        POST_EXIT_WATCHDOG,
    }
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


@pytest.mark.timeout_seconds(5)
def test_unavailability_tracker_is_sole_cooldown_clock_owner() -> None:
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


def test_idle_watchdog_module_imports_clean() -> None:
    assert "DEFERRED_BY_STUCK_CLASSIFIER" in WatchdogFireReason.__members__
    assert "REPEATED_IDENTICAL_TOOL_CALL" in WatchdogFireReason.__members__
    assert IdleWatchdog is not None
    assert StuckKind is not None


def test_expected_fire_reasons_includes_repeated_identical_tool_call(
    tmp_path: Path,
) -> None:
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
        "EXPECTED_FIRE_REASONS frozenset literal MUST be present in idle_watchdog.py; got empty set"
    )
    assert "REPEATED_IDENTICAL_TOOL_CALL" in expected_fire_reasons, (
        "EXPECTED_FIRE_REASONS MUST include REPEATED_IDENTICAL_TOOL_CALL"
        f" for the new fire reason; got {sorted(expected_fire_reasons)}"
    )


def test_expected_fire_reasons_drift_guard_raises_runtime_error(
    tmp_path: Path,
) -> None:
    _ = tmp_path
    source = (IDLE_WATCHDOG_DIR / "idle_watchdog.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IDLE_WATCHDOG_DIR / "idle_watchdog.py"))

    guard = _find_drift_guard(tree)
    assert guard is not None, (
        "The drift guard `if _actual != EXPECTED_FIRE_REASONS` MUST be"
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


@dataclass
class FakeProcessMonitor(ProcessMonitor):
    stdin = None

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


@dataclass
class FakeCapture(SubagentOutputCapture):
    lines: list[list[str]] = field(default_factory=list)
    call_count: int = 0

    def read_lines(self, worker_id: str) -> list[str]:
        result = self.lines[self.call_count] if self.call_count < len(self.lines) else []
        self.call_count += 1
        return result


@dataclass
class _NoProcessMonitor:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _RealSubagentMonitor(ProcessMonitor):
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


@dataclass
class _FakeProcessMonitor:
    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _LiveOnlyProcessMonitor(ProcessMonitor):
    live_count: int = 1

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


@dataclass
class _FakeDiscovery(DiscoveryStrategy):
    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


@dataclass
class _FakeProcessMonitorE2eActivityAware(ProcessMonitor):
    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
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


@dataclass
class _HelpersOnlyMonitorLogSpamThrottlePublicSurface(ProcessMonitor):
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


@dataclass
class _LiveSubagentMonitor(_HelpersOnlyMonitor):
    def live_subagent_count(self) -> int:
        return 1


class _RecordingWatchdog:
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

    def record_any_output(self, byte_size: int = 0) -> None:
        del byte_size

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


class _ToolUseStrategy:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


class _JunkToolUseStrategy:
    def __init__(self) -> None:
        self._raw = "not-json-{{{"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


@dataclass
class _NoProcessMonitorNoOutputAtStart(ProcessMonitor):
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


@dataclass
class _NoProcessMonitorNoOutputAtStartLoading:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _StubCorroborator:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


@dataclass
class _NoProcessMonitorNonResumableEndToEnd:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _FakeManagedProcess:
    stdin = None

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        self.terminate_calls.append(grace_period_s)


class _FakeCheckFireSupervisor:
    def act(self, _observation: object, *, teardown: object) -> TerminationOutcome:
        del teardown
        return TerminationOutcome(issuer=None, returncode=0)


@dataclass
class _FakeCheckFireSelf:
    stdin = None

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(default_factory=lambda: [None])
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)
    _captured_session_id: str | None = None
    termination_recorder: TerminationRecorder = field(default_factory=TerminationRecorder)
    _process_teardown: None = None
    _completion_is_terminal: Callable[[], bool] | None = None

    def _attempt_supervisor(self) -> _FakeCheckFireSupervisor:
        return _FakeCheckFireSupervisor()


@dataclass
class _ProductionSubagentFakeHandle:
    has_descendants: bool = False
    returncode: int = 0


@dataclass
class _NoProcessMonitorPureStallWedge:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _NoProcessMonitorResumeAfterKillContract:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _FakeManagedProcessResumeAfterKillContract:
    stdin = None

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        self.terminate_calls.append(grace_period_s)


@dataclass
class _FakeCheckFireSelfResumeAfterKillContract:
    stdin = None

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(default_factory=lambda: [None])
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)
    _captured_session_id: str | None = None
    termination_recorder: TerminationRecorder = field(default_factory=TerminationRecorder)
    _process_teardown: None = None
    _completion_is_terminal: Callable[[], bool] | None = None

    def _attempt_supervisor(self) -> _FakeCheckFireSupervisor:
        return _FakeCheckFireSupervisor()


class _NoOpStrategy:
    def observe_line(self, _line: str) -> None:
        pass


@dataclass
class _NoProcessMonitorResumeContractInvariant:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _LineReaderLike:
    agent_command_name: str = "test-agent"
    parsed_output: list[str] | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    expected_session_id: str | None = None


class _FakeProcess:
    stdin = None

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
        self._gate.clear()
        from ralph.process.manager import get_process_manager

        pm = get_process_manager()
        while not self._gate.wait(timeout=0.005):
            rec = pm.get_record(self.pid)
            if rec is not None and rec.status.name in ("TERMINATED", "KILLED", "EXITED"):
                break
            if self.terminated:
                break
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

    def terminate(self, **_kwargs: object) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self, **_kwargs: object) -> None:
        self.terminated = True
        self.returncode = -9


class _BaseFakeWatchdog:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def record_invocation_start(self) -> None:
        pass

    def record_session_id_capture(self, session_id: str) -> None:
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


class _FakeFiringWatchdog(_BaseFakeWatchdog):
    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.FIRE


class _FakeNoFireWatchdog(_BaseFakeWatchdog):
    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        return WatchdogVerdict.CONTINUE


class _FakeFiringPostExitWatchdog:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def wait_for_process_exit(
        self,
        predicate_exit_observed: object,
    ) -> PostExitVerdict:
        return PostExitVerdict.FIRE_PROCESS_EXIT_HANG


@dataclass
class _NoProcessMonitorSilentAfterToolCallWedge:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _NoProcessMonitorSilentSubagentFires:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _NoProcessMonitorSilentSubagentRuntime:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


@dataclass
class _LiveOnlyProcessMonitorSmartVerdictDumbKills(ProcessMonitor):
    live_count: int = 1

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


@dataclass
class _NoProcessMonitorStrictlyStuckCeiling:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _StubCorroboratorStrictlyStuckCeiling:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


@dataclass
class _ClassifyQuietStub:
    state: AgentExecutionState = AgentExecutionState.ACTIVE

    def __call__(self) -> AgentExecutionState:
        return self.state


@dataclass
class _NoProcessMonitorStuckJobHeartbeatCeiling:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _StubCorroboratorStuckJobHeartbeatCeiling:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


@dataclass
class _FakeProcessMonitorStuckJobIntelligence(ProcessMonitor):
    live_count: int = 0

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


class _StaticCapture:
    def __init__(self) -> None:
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        self.read_count += 1
        return [f"line-for-{worker_id}"]


class _StaticCaptureEmpty:
    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        return []


class _FakeProcessMonitorSubagentCaptureEviction:
    def __init__(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
        self._captures = dict(captures)
        self.discover_calls = 0

    def replace_captures(self, captures: Mapping[str, SubagentOutputCapture]) -> None:
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


class _StatefulCapture:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._read_position = 0
        self.read_count = 0

    def read_lines(self, worker_id: str) -> list[str]:
        del worker_id
        self.read_count += 1
        if self._read_position >= len(self._lines):
            return []
        slice_ = self._lines[self._read_position :]
        self._read_position = len(self._lines)
        return list(slice_)


@dataclass
class _FilteredCountMonitor:
    filtered_count: int = 0
    descendant_snapshot_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def descendant_snapshot(self) -> tuple[int, float]:
        return self.descendant_snapshot_count, 0.0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


@dataclass
class _RegistryPidSource:
    registry: SubagentPidRegistry
    source_label: str

    def known_subagent_pids(self) -> set[int]:
        return {
            identity.pid
            for identity in self.registry.snapshot()
            if identity.source == self.source_label
        }


@dataclass
class _NoProcessMonitorSubagentProgressSurface:
    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


class _ResultThenCallStrategy:
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


@dataclass
class _FakeProcessMonitorWaitingSubagentProgress:
    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}
