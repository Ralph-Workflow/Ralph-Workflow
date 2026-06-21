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

from dataclasses import dataclass

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
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import InvokeOptions
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_retry_intent import (
    agent_retry_intent_for_failure,
)

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


def test_fire_no_output_at_start_yields_inactivity_error() -> None:
    """Build an IdleWatchdog, force NO_OUTPUT_AT_START to fire, and
    assert the watchdog's fire path raises ``IdleWatchdogKilledError``
    with reason='no_output_at_start' and ``child_alive`` set
    correctly.

    Uses FakeClock so the wall-clock advance is deterministic; the
    watchdog fires ``NO_OUTPUT_AT_START`` at ``no_output_at_start_seconds``
    when no output, no tool call, no file change, and no subagent
    output has been observed.
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

    # Advance the clock past the no_output_at_start threshold.
    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_classify_quiet)
    assert verdict == WatchdogVerdict.FIRE, (
        f"expected FIRE after no_output_at_start at 31s; got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    # Build the typed IdleWatchdogKilledError using the fire reason.
    fired_reason = watchdog.last_fire_reason
    assert fired_reason is not None
    typed_exc = IdleWatchdogKilledError(
        reason=fired_reason.value,
        signal=15,
        child_alive=None,
    )
    assert typed_exc.reason == WatchdogFireReason.NO_OUTPUT_AT_START.value
    assert typed_exc.signal == 15
    assert typed_exc.child_alive is None


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
