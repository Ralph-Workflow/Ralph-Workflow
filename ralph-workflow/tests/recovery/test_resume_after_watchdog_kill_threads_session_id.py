"""Black-box tests for the R4 resume-after-kill session-id threading contract.

R4 (Trustworthy Idle Watchdog product spec):

    Watchdog-driven kills resume the existing session; new sessions
    occur only on deliberate phase transitions.

The contract is end-to-end:

  1. The watchdog kills the agent via ``IdleWatchdogKilledError`` with
     ``resumable_session_id`` populated.
  2. The line-reader wraps the typed exception in ``_IdleStreamTimeoutError``
     and converts via ``_convert_idle_stream_timeout_to_agent_error``
     to ``AgentInactivityTimeoutError``.
  3. The failure classifier applies the ``resumable_kill`` carve-out
     (watchdog_reason == "no_output_at_start" AND resumable_session_id)
     so ``is_unavailable=False``.
  4. ``recovery_action_for_failure_reason('AgentInactivityTimeoutError',
     has_prior_session=True)`` returns ``'resume'``.

These tests are pure black-box: no real subprocess, no real time, no
real filesystem. ``IdleWatchdog`` is driven with ``FakeClock`` and a
fake ``ProcessMonitor`` so the test is deterministic and fast.
"""

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
    """Fake monitor: no live subagents, no captures (canonical test fixture)."""

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
    """Build an IdleWatchdog and return the captured session id alongside it.

    The watchdog itself does not own a public ``captured_session_id``
    property: the line-reader layer extracts the session id from the
    agent's ``--session`` flag and threads it through the public
    ``_convert_idle_stream_timeout_to_agent_error(..., captured_session_id=...)``
    seam. This test returns the captured id as a plain tuple element so
    the assertions below can pass it back to the public conversion
    seam -- no private watchdog attribute is set or read.

    The captured session id parameter is accepted for symmetry with the
    R4 contract: the kill error AND the converted error AND the
    classifier all thread the SAME id end-to-end. The watchdog itself
    does not need to know the id (it fires on activity signal, not on
    session identity).
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        # The headline fire reason for the R4 contract.
        no_output_at_start_seconds=30.0,
        # Disable the no-progress quiet ceiling to avoid ambiguity.
        no_progress_quiet_seconds=None,
        # Stale activity evidence so the watchdog does not defer.
        activity_evidence_ttl_seconds=0.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())
    watchdog.record_invocation_start()
    return watchdog, clock, captured_session_id


def test_no_output_at_start_kill_threads_session_id() -> None:
    """R4 step 1: watchdog kill threads ``resumable_session_id`` end-to-end.

    Build an IdleWatchdog with ``captured_session_id="sess-abc-123"``.
    Advance past the NO_OUTPUT_AT_START ceiling; the watchdog MUST
    fire. Build the ``IdleWatchdogKilledError`` with the captured id;
    convert to ``AgentInactivityTimeoutError``; assert the typed
    error carries the captured id and ``session_resume_safe=True``.
    """
    watchdog, clock, captured_session_id = _make_watchdog_with_capture(
        captured_session_id="sess-abc-123"
    )
    clock.advance(31.0)  # past no_output_at_start_seconds=30.0
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    # Build the typed watchdog kill error with the captured session id.
    typed_exc = IdleWatchdogKilledError(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START.value,
        signal=15,  # SIGTERM
        evidence_summary="no_output_at_start fired",
        child_alive=False,
        resumable_session_id="sess-abc-123",
    )
    # Build the wrapper _IdleStreamTimeoutError the reader produces.
    wrapper = _IdleStreamTimeoutError(
        timeout_seconds=31.0,
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"idle_elapsed": 31.0},
    )
    wrapper.__cause__ = typed_exc
    # Convert to AgentInactivityTimeoutError. The captured_session_id
    # threads through the InactivityTimeoutOpts.
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
    """R4 step 2: classifier applies the resumable_kill carve-out.

    The ``resumable_kill`` carve-out (``watchdog_reason ==
    "no_output_at_start"`` AND ``resumable_session_id`` present) sets
    ``is_unavailable=False``. The classifier reads the watchdog reason
    from the typed exception's ``reason`` attribute.
    """
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
    # The resumable_kill carve-out sets is_unavailable=False so the
    # recovery controller emits a resume intent instead of advancing
    # the agent chain.
    assert failure.is_unavailable is False


def test_recovery_action_returns_resume_for_agent_inactivity() -> None:
    """R4 step 3: ``recovery_action_for_failure_reason`` returns 'resume'.

    The recovery controller calls this helper to decide whether the
    next attempt should ``resume`` the prior session. For
    ``AgentInactivityTimeoutError`` with ``has_prior_session=True``,
    the helper MUST return ``'resume'``.
    """
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_recovery_action_returns_fresh_when_no_prior_session() -> None:
    """R4 step 4: no prior session -> 'fresh' even for watchdog-kill reason.

    Without a prior session to resume, the helper MUST return
    ``'fresh'`` regardless of the watchdog-kill reason. The fresh
    path goes through ``fresh_session_options`` (function-separate
    from the resume path).
    """
    action = recovery_action_for_failure_reason(
        "AgentInactivityTimeoutError",
        has_prior_session=False,
    )
    assert action == "fresh"
    # The fresh path goes through ``fresh_session_options`` which
    # explicitly clears the session id.
    opts = InvokeOptions(session_id="prior-session-id")
    fresh = fresh_session_options(opts)
    assert fresh.session_id is None


def test_recovery_action_returns_resume_for_opencode_resumable_exit_error() -> None:
    """R4 step 5: ``OpenCodeResumableExitError`` also resumes the prior session.

    The OpenCode resumable exit error is the deterministic
    classification for a clean rc=0 exit with no completion evidence.
    The recovery action MUST resume the prior session so the agent
    can continue where it left off.
    """
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_fresh_session_options_returns_none_session_id() -> None:
    """R4 step 6: fresh_session_options clears the session id explicitly.

    The fresh and resume paths are FUNCTION-SEPARATE. The fresh
    path (deliberate phase transitions) MUST NOT accidentally carry
    a prior session id forward.
    """
    opts = InvokeOptions(session_id="prior-session-id")
    fresh = fresh_session_options(opts, prior_session_id="another-prior-id")
    # Even when ``prior_session_id`` is provided, fresh never
    # writes it back -- the deliberate-phase-transition path always
    # starts a fresh session.
    assert fresh.session_id is None
    assert opts.session_id == "prior-session-id"  # original is unchanged


def test_resolve_resume_session_id_threads_prior_session_id() -> None:
    """R4 step 7: ``resolve_resume_session_id`` threads the prior id.

    The single decision point for session continuation: when
    ``has_prior_session=True`` and ``recovery_action='resume'``, the
    helper MUST return the prior session id (NOT None).
    """
    resolved = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sess-xyz",
        recovery_action="resume",
    )
    assert resolved == "sess-xyz"


def test_resolve_resume_session_id_returns_none_when_fresh() -> None:
    """R4: 'fresh' action MUST return None even with a prior session.

    When the recovery controller decides 'fresh', the helper MUST
    return None regardless of the prior session id. The fresh and
    resume paths are FUNCTION-SEPARATE.
    """
    resolved = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sess-xyz",
        recovery_action="fresh",
    )
    assert resolved is None
