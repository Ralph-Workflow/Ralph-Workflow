from __future__ import annotations

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._idle_stream_timeout_error import _IdleStreamTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def _build_production_chain(
    *,
    reason: WatchdogFireReason,
    child_alive: bool | None,
    resumable_session_id: str | None,
) -> AgentInactivityTimeoutError:
    typed_exc = IdleWatchdogKilledError(
        reason=reason.value,
        signal=15,
        evidence_summary="watchdog fired",
        child_alive=child_alive,
        resumable_session_id=resumable_session_id,
    )
    wrapper = _IdleStreamTimeoutError(
        timeout_seconds=31.0,
        reason=reason,
        diagnostic={"idle_elapsed": 31.0},
    )
    wrapper.__cause__ = typed_exc
    converted = AgentInactivityTimeoutError(
        "test-agent",
        31.0,
        opts=InactivityTimeoutOpts(
            reason=reason,
            session_resume_safe=True,
            resumable_session_id=resumable_session_id,
            diagnostic={"idle_elapsed": 31.0},
        ),
    )
    converted.__cause__ = wrapper
    return converted


def test_child_alive_true_reaches_classifier_through_production_chain() -> None:
    exc = _build_production_chain(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        child_alive=True,
        resumable_session_id="sess-live-1",
    )
    classified = FailureClassifier().classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert classified.category == FailureCategory.AGENT
    assert classified.is_unavailable is False, (
        "live-child NO_PROGRESS_QUIET (child_alive=True) must route to"
        " Rule 1 (is_unavailable=False), not Rule 2 (exponential backoff)."
        " The pre-fix bug lost the typed cause at one-hop __cause__ walk"
        " and collapsed to the conservative child_alive=None path."
    )


def test_child_alive_false_reaches_classifier_through_production_chain() -> None:
    exc = _build_production_chain(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        child_alive=False,
        resumable_session_id="sess-dead-1",
    )
    classified = FailureClassifier().classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert classified.category == FailureCategory.AGENT
    assert classified.is_unavailable is True, (
        "dead-child NO_PROGRESS_QUIET (child_alive=False) must route to"
        " Rule 2 (is_unavailable=True), not Rule 1 (defense-in-depth)."
    )


def test_resumable_session_id_reaches_classifier_through_production_chain() -> None:
    exc = _build_production_chain(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        child_alive=False,
        resumable_session_id="sess-captured-threaded",
    )
    classified = FailureClassifier().classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert classified.category == FailureCategory.AGENT
    assert classified.resumable_session_id == "sess-captured-threaded", (
        "resumable_session_id must propagate from the typed"
        " IdleWatchdogKilledError at the bottom of the production chain"
        " through the AgentInactivityTimeoutError and"
        " _IdleStreamTimeoutError wrappers. Pre-fix: the one-hop"
        " __cause__ walk missed the typed cause and the captured id was"
        " lost."
    )
    assert classified.is_unavailable is False, (
        "no_output_at_start + captured resumable_session_id must trigger"
        " the resumable_kill carve-out (is_unavailable=False) so the"
        " recovery controller resumes the existing session instead of"
        " advancing the chain."
    )


def test_no_progress_quiet_chain_no_signal_does_not_reach_child_alive() -> None:
    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"cumulative": 0.0},
    )
    exc = AgentInactivityTimeoutError("claude", 15.0, opts=opts)
    classified = FailureClassifier().classify(
        exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified.category == FailureCategory.AGENT
    assert classified.is_unavailable is True
