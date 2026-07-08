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

from ralph.agents.idle_watchdog import WatchdogFireReason
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
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.classifier import FailureClassifier, FailureContext
from ralph.recovery.controller import RecoveryController


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
