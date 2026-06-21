"""Pin: resumable session_id plumbed through the watchdog kill -> recovery flow.

The PROMPT trace shows that after the watchdog fires
``NO_OUTPUT_AT_START`` the recovery controller starts a FRESH agent
session instead of resuming the killed one. The cause: ``state.last_agent_session_id``
is NEVER set from the watchdog's captured session id even though
``_process_reader._run_subprocess_and_read_lines`` already captures
``captured_session_id`` per-line and threads it into
``AgentInactivityTimeoutError.opts.resumable_session_id`` via
``_convert_idle_stream_timeout_to_agent_error``.

The fix: after ``FailureClassifier.classify`` returns a
``ClassifiedFailure`` carrying ``watchdog_reason``, the controller
sets ``new_state.last_agent_session_id`` from
``failure.resumable_session_id`` so the downstream
``_apply_chain_retry`` consumer (which already does
``resume_agent_retry_intent(state.last_agent_session_id)``) emits
a resume intent with the captured id.

This test:

1. Wraps ``IdleWatchdogKilledError(resumable_session_id='sess-abc123')``
   as an ``AgentInactivityTimeoutError`` with the same id via the
   canonical conversion seam.
2. Drives the wrapper through ``FailureClassifier.classify`` and
   asserts the returned ``ClassifiedFailure`` carries
   ``watchdog_reason == 'no_output_at_start'`` AND
   ``resumable_session_id == 'sess-abc123'``.
3. Drives the wrapper through ``RecoveryController.handle`` with a
   state having a chain and asserts:
     (a) ``state.last_agent_session_id == 'sess-abc123'`` after handle() returns
     (b) the returned state's ``agent_retry_intent`` is a resume intent
         with ``session_id == 'sess-abc123'`` (i.e.
         ``_apply_chain_retry`` consumed the populated
         ``last_agent_session_id``).

Pre-fix ``state.last_agent_session_id`` stays None and the
``agent_retry_intent`` is a cleared intent.

All tests use the project's existing helpers (FakeClock, the
recovery helpers, the AgentRetryIntent builder). No real
subprocess, no real sleep, no real network; the 60s combined
budget is preserved.
"""

from __future__ import annotations

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import (
    AgentInactivityTimeoutError,
)
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._process_reader import (
    _convert_idle_stream_timeout_to_agent_error,
    _is_resumable_fire_reason,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import PipelineState


def _active_classify_quiet():
    from ralph.agents.execution_state import AgentExecutionState

    return AgentExecutionState.ACTIVE


def _make_pipeline_state(
    *,
    chain_agents: tuple[str, ...] = ("agent-a",),
    retries: int = 0,
) -> PipelineState:
    """Construct a minimal ``PipelineState`` with a chain for the phase.

    Returns a state with ``phase='development'``, an AgentChainState
    that has ``chain_agents`` and ``current_index=0``, and
    ``last_agent_session_id=None`` (the empty pre-fire state).
    """
    from ralph.pipeline.agent_chain_state import AgentChainState

    chain = AgentChainState(agents=list(chain_agents), current_index=0, retries=retries)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain},
    )
    return state


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


def test_failure_classifier_carries_resumable_session_id() -> None:
    """``FailureClassifier.classify`` MUST surface ``resumable_session_id``
    from ``AgentInactivityTimeoutError.opts.resumable_session_id`` so
    the recovery controller can read it without re-walking the
    exception chain.

    Pre-fix the field is missing on ``ClassifiedFailure``.
    """
    from ralph.recovery.classifier import FailureClassifier

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
    """``RecoveryController.handle`` MUST set
    ``state.last_agent_session_id`` from the captured session id when
    the failure is a resumable watchdog fire and ``retry_in_session``
    is True.

    Pre-fix the controller never reads the watchdog's captured id so
    ``state.last_agent_session_id`` stays None and ``_apply_chain_retry``
    emits a cleared retry intent.
    """
    from ralph.recovery.classifier import FailureContext
    from ralph.recovery.controller import RecoveryController

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
    """``_apply_chain_retry`` MUST emit a resume intent with the captured
    session id when ``state.last_agent_session_id`` is populated.

    Pre-fix ``state.last_agent_session_id`` stays None so the chain
    retry emits a cleared intent and the next attempt starts a fresh
    session.
    """
    from ralph.recovery.classifier import FailureContext
    from ralph.recovery.controller import RecoveryController

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
        f"agent_retry_intent.session_id MUST thread the captured id;"
        f" got {intent.session_id!r}"
    )


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


def test_is_resumable_fire_reason_for_no_output_at_start() -> None:
    """``_is_resumable_fire_reason(NO_OUTPUT_AT_START)`` MUST return True."""
    assert _is_resumable_fire_reason(WatchdogFireReason.NO_OUTPUT_AT_START) is True, (
        "NO_OUTPUT_AT_START MUST be resumable"
    )


def test_pipeline_state_copy_with_accepts_last_agent_session_id() -> None:
    """``PipelineState.copy_with`` MUST accept ``last_agent_session_id``."""
    state = _make_pipeline_state()
    updated = state.copy_with(last_agent_session_id="sid-1")
    assert updated.last_agent_session_id == "sid-1"