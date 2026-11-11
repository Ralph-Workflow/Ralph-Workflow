from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._idle_stream_timeout_error import _IdleStreamTimeoutError
from ralph.agents.invoke._process_reader import (
    _convert_idle_stream_timeout_to_agent_error,
)
from ralph.agents.invoke._session_resume import (
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import InvokeOptions
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


@dataclass
class _NoProcessMonitor(ProcessMonitor):
    live_count: int = 0
    classified: tuple = ()

    def live_subagent_count(self) -> int:
        return self.live_count

    def spawned_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return {}


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog_with_capture(
    *,
    captured_session_id: str | None,
) -> tuple[IdleWatchdog, FakeClock, str | None]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=0.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())
    watchdog.record_invocation_start()
    return watchdog, clock, captured_session_id


def test_no_output_at_start_kill_threads_session_id() -> None:
    watchdog, clock, captured_session_id = _make_watchdog_with_capture(
        captured_session_id="sess-abc-123"
    )
    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    typed_exc = IdleWatchdogKilledError(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START.value,
        signal=15,
        evidence_summary="no_output_at_start fired",
        child_alive=False,
        resumable_session_id="sess-abc-123",
    )
    wrapper = _IdleStreamTimeoutError(
        timeout_seconds=31.0,
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"idle_elapsed": 31.0},
    )
    wrapper.__cause__ = typed_exc
    converted = _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=("line 1", "line 2"),
        explicit_completion_seen=False,
        captured_session_id=captured_session_id,
    )
    assert converted.resumable_session_id == captured_session_id
    assert converted.resumable_session_id == "sess-abc-123"
    assert converted.session_resume_safe is True
    assert converted.reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_failure_classifier_marks_no_output_at_start_as_resumable() -> None:
    watchdog, clock, captured_session_id = _make_watchdog_with_capture(
        captured_session_id="sess-abc-123"
    )
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_active)
    typed_exc = IdleWatchdogKilledError(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START.value,
        signal=15,
        evidence_summary="no_output_at_start fired",
        child_alive=False,
        resumable_session_id="sess-abc-123",
    )
    wrapper = _IdleStreamTimeoutError(
        timeout_seconds=31.0,
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"idle_elapsed": 31.0},
    )
    wrapper.__cause__ = typed_exc
    converted = _convert_idle_stream_timeout_to_agent_error(
        agent_name="test-agent",
        exc=wrapper,
        parsed_output=(),
        captured_session_id=captured_session_id,
    )
    classifier = FailureClassifier()
    failure = classifier.classify(
        converted,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert failure.category == FailureCategory.AGENT
    assert failure.resumable_session_id == "sess-abc-123"
    assert failure.is_unavailable is False


def test_recovery_action_returns_resume_for_agent_inactivity() -> None:
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_recovery_action_returns_fresh_when_no_prior_session() -> None:
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=False,
    )
    assert action == "fresh"
    opts = InvokeOptions(session_id="prior-session-id")
    fresh = fresh_session_options(opts)
    assert fresh.session_id is None


def test_recovery_action_returns_resume_for_opencode_resumable_exit_error() -> None:
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_fresh_session_options_returns_none_session_id() -> None:
    opts = InvokeOptions(session_id="prior-session-id")
    fresh = fresh_session_options(opts, prior_session_id="another-prior-id")
    assert fresh.session_id is None
    assert opts.session_id == "prior-session-id"


def test_resolve_resume_session_id_threads_prior_session_id() -> None:
    resolved = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sess-xyz",
        recovery_action="resume",
    )
    assert resolved == "sess-xyz"


def test_resolve_resume_session_id_returns_none_when_fresh() -> None:
    resolved = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sess-xyz",
        recovery_action="fresh",
    )
    assert resolved is None
