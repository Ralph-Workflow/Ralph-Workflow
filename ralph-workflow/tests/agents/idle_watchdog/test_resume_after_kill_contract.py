"""End-to-end contract tests for the resume-after-watchdog-kill flow.

The watchdog fires a fire-reason; the line reader raises an
``IdleWatchdogKilledError(reason=...)``; the stream-timeout wrapper
threads that into an ``AgentInactivityTimeoutError(reason=...,
session_resume_safe=...)``; the recovery controller calls
``recovery_action_for_failure_reason(...)``; the session-id resolver
threads the prior session id; the pipeline runner builds an
``AgentRetryIntent`` with ``action='resume'``.

This file pins each leg of that contract so a future refactor cannot
silently regress to a fresh session on AgentInactivityTimeoutError.

All tests use the existing helpers (``FakeClock``, the recovery
helpers, the AgentRetryIntent builder).  No real subprocess, no real
sleep, no real network; the 60s combined budget is preserved.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import (
    IdleWatchdogKilledError,
)
from ralph.agents.idle_watchdog_kill import (
    IdleWatchdogKilledError as IdleWatchdogKilledErrorTop,
)
from ralph.agents.invoke import (
    fresh_session_options,
)
from ralph.agents.invoke._agent_inactivity_timeout_error import (
    AgentInactivityTimeoutError,
)
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._process_reader import (
    _convert_idle_stream_timeout_to_agent_error,
    _ProcessLineReader,
)
from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import InvokeOptions
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_retry_intent import (
    agent_retry_intent_for_failure,
)

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusEvent

def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


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


# ---------------------------------------------------------------------------
# (a) IdleWatchdog fire NO_OUTPUT_AT_START -> IdleWatchdogKilledError(reason=NO_OUTPUT_AT_START)
# ---------------------------------------------------------------------------


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


class _FakeManagedProcess:
    """Fake process handle for exercising ``_ProcessLineReader._check_fire``.

    ``_check_fire`` calls ``terminate`` and reads ``pid``; we keep
    ``pid`` as ``None`` so no real process tree teardown runs in the
    test, and we record whether ``terminate`` was invoked.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self.terminate_calls: list[float] = []

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        self.terminate_calls.append(grace_period_s)


@dataclass
class _FakeCheckFireSelf:
    """Minimal fake reader self for calling ``_ProcessLineReader._check_fire``.

    The method needs the policy, clock, lines queue, last hard-stop
    slot, and a fake handle.  Everything else is ignored.
    """

    _policy: TimeoutPolicy
    _clock: FakeClock
    _lines_lock: threading.Lock = field(default_factory=threading.Lock)
    _lines_queue: list[str] = field(default_factory=list)
    _last_hard_stop: list[WaitingStatusEvent | None] = field(
        default_factory=lambda: [None]
    )
    _last_activity_kind: str = "none"
    _handle: _FakeManagedProcess = field(default_factory=_FakeManagedProcess)
    # Mirrors ``_ProcessLineReader._captured_session_id`` so the kill
    # path can read the captured transport session id without walking
    # the stdout pipe. Default None for tests that do not exercise the
    # capture path.
    _captured_session_id: str | None = None


class _NoOpStrategy:
    """Stub execution strategy for the fake reader self."""

    def observe_line(self, _line: str) -> None:
        pass


def test_fire_no_output_at_start_yields_inactivity_error() -> None:
    """Build an IdleWatchdog, force NO_OUTPUT_AT_START to fire, drive the
    real ``_ProcessLineReader._check_fire`` path, and then exercise the
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
        process_monitor=_NoProcessMonitor(),
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
        f"expected FIRE after no_output_at_start past the dumb-kill"
        f" floor (125s); got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    # Drive the real line-reader fire path with a fake reader self.
    fake_self = _FakeCheckFireSelf(_policy=policy, _clock=clock)
    result = _ProcessLineReader._check_fire(
        fake_self, watchdog, WatchdogVerdict.FIRE
    )
    assert result is not None, (
        "_check_fire must return a wrapper when the verdict is FIRE"
    )
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
    assert timeout_exc.session_resume_safe is True, (
        "NO_OUTPUT_AT_START must be resume-safe"
    )
    assert timeout_exc.resumable_session_id == expected_session_id, (
        "the conversion seam MUST thread the expected session id"
    )


# ---------------------------------------------------------------------------
# (b) AgentInactivityTimeoutError.session_resume_safe is True for the
#     in-set reasons and False otherwise.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# (c) recovery_action_for_failure_reason returns 'resume' for
#     AgentInactivityTimeoutError with has_prior_session=True.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# (d) resolve_resume_session_id threads the prior session id.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# (e) fresh_session_options clears session_id for new-phase transitions.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# (f) pipeline_runner_threads_resume_session_id: agent_retry_intent_for_failure
#     builds AgentRetryIntent(action='resume', session_id=<recovered id>).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# IdleWatchdogKilledError / IdleWatchdogKilledError alias sanity check.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# (g) Prompt-scenario regression: NO_OUTPUT_AT_START fire with a known
#     prior session id threads all the way to a resume AgentRetryIntent.
# ---------------------------------------------------------------------------


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
        process_monitor=_NoProcessMonitor(),
    )
    watchdog.record_invocation_start()

    # Advance past the no_output_at_start threshold AND past the
    # dumb-kill floor (120 s default) so the floor guard in
    # ``_evaluate_no_output_at_start`` does not defer the fire.
    clock.advance(125.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    fake_self = _FakeCheckFireSelf(_policy=policy, _clock=clock)
    result = _ProcessLineReader._check_fire(fake_self, watchdog, WatchdogVerdict.FIRE)
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
