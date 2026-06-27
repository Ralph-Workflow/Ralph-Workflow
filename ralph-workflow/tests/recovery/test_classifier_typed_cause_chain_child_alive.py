"""Regression test for the typed watchdog-cause chain reaching ``child_alive``.

R1+R3+R5 (Trustworthy Idle Watchdog spec): the failure classifier must read
the watchdog's typed ``child_alive`` field end-to-end even when the typed
``IdleWatchdogKilledError`` is buried two hops deep in the exception chain.

Production exception chain (the real path, NOT a synthetic test fake):

    AgentInactivityTimeoutError
        \u2191 __cause__  (set by `raise X from exc` in
          `_process_reader._run_subprocess_and_read_lines`)
        _IdleStreamTimeoutError
            \u2191 __cause__  (set by `wrapper.__cause__ = typed_exc`
              in `_check_fire`)
            IdleWatchdogKilledError (typed, has .reason, .signal,
              .child_alive, .resumable_session_id)

The pre-fix failure classifier walked ``exc_obj.__cause__`` directly (only one
hop) and lost the typed cause when the chain was two hops deep. The live-child
signal collapsed back to the conservative ``child_alive=None`` path, which
falsely routes a live-child ``NO_PROGRESS_QUIET`` to Rule 2 (exponential
backoff) instead of the intended Rule 1 (same-agent retry) defense-in-depth
path.

The fix reuses ``FailureClassifier._find_typed_watchdog_cause`` (which
already walks the full chain) so the typed cause is reachable regardless of
chain depth. This test builds the REAL production chain using the real
``AgentInactivityTimeoutError`` and the real ``_IdleStreamTimeoutError``
classes and asserts the classifier reads ``child_alive`` correctly.

These tests are pure black-box: no real subprocess, no real time, no real
filesystem. The classifier is exercised with synthetic exceptions that
mirror the production chain exactly.
"""

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
    """Build the real production exception chain:
    AgentInactivityTimeoutError <- _IdleStreamTimeoutError <- IdleWatchdogKilledError.

    Mirrors the exact construction path the watchdog fires in production:
      1. `_check_fire` constructs `IdleWatchdogKilledError` and sets
         `wrapper.__cause__ = typed_exc`.
      2. `_run_subprocess_and_read_lines` catches `_IdleStreamTimeoutError`
         and re-raises `_convert_idle_stream_timeout_to_agent_error(...)`
         `from exc` (so the AgentInactivityTimeoutError has __cause__ set
         to the wrapper).
    """
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
    """Production chain: child_alive=True must reach the classifier.

    The classifier must read ``IdleWatchdogKilledError.child_alive=True``
    from the typed cause at the BOTTOM of the production chain
    (AgentInactivityTimeoutError <- _IdleStreamTimeoutError <- IdleWatchdogKilledError).

    Pre-fix bug: the classifier walked ``__cause__`` directly (one hop),
    hit `_IdleStreamTimeoutError` (not IdleWatchdogKilledError), and fell
    back to the conservative ``child_alive=None`` path. The conditional
    ``child_alive in (False, None)`` for ``no_progress_quiet`` then routed
    a live-child NO_PROGRESS_QUIET to Rule 2 (exponential backoff)
    instead of the intended Rule 1 (defense-in-depth; same-agent retry).
    """
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
    # The headline assertion: live-child NO_PROGRESS_QUIET routes to
    # Rule 1 (defense-in-depth; same-agent retry), NOT Rule 2
    # (exponential backoff). The conditional in the classifier reads
    # ``child_alive=True`` and routes to ``is_unavailable=False``.
    assert classified.is_unavailable is False, (
        "live-child NO_PROGRESS_QUIET (child_alive=True) must route to"
        " Rule 1 (is_unavailable=False), not Rule 2 (exponential backoff)."
        " The pre-fix bug lost the typed cause at one-hop __cause__ walk"
        " and collapsed to the conservative child_alive=None path."
    )


def test_child_alive_false_reaches_classifier_through_production_chain() -> None:
    """Production chain: child_alive=False must reach the classifier.

    The conservative policy: ``child_alive=False`` (truly dead child)
    routes to Rule 2 (exponential backoff, STALE_CHILD_QUIET). The
    classifier must read the typed cause's ``child_alive=False`` field
    through the two-hop production chain.
    """
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
    # Rule 2 path: dead-child NO_PROGRESS_QUIET marks the agent unavailable.
    assert classified.is_unavailable is True, (
        "dead-child NO_PROGRESS_QUIET (child_alive=False) must route to"
        " Rule 2 (is_unavailable=True), not Rule 1 (defense-in-depth)."
    )


def test_resumable_session_id_reaches_classifier_through_production_chain() -> None:
    """Production chain: resumable_session_id must reach the classifier.

    The classifier's resumable_kill carve-out requires the captured
    session id to be lifted from the typed cause so the recovery
    controller's resume intent can thread the id forward. Pre-fix: the
    one-hop __cause__ walk missed the typed cause at the bottom of the
    chain so the captured id fell through to None.
    """
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
    # The headline assertion: the captured session id from the typed
    # cause reaches the classifier through the production chain.
    assert classified.resumable_session_id == "sess-captured-threaded", (
        "resumable_session_id must propagate from the typed"
        " IdleWatchdogKilledError at the bottom of the production chain"
        " through the AgentInactivityTimeoutError and"
        " _IdleStreamTimeoutError wrappers. Pre-fix: the one-hop"
        " __cause__ walk missed the typed cause and the captured id was"
        " lost."
    )
    # The resumable_kill carve-out sets is_unavailable=False so the
    # recovery controller emits a resume intent with the captured id.
    assert classified.is_unavailable is False, (
        "no_output_at_start + captured resumable_session_id must trigger"
        " the resumable_kill carve-out (is_unavailable=False) so the"
        " recovery controller resumes the existing session instead of"
        " advancing the chain."
    )


def test_no_progress_quiet_chain_no_signal_does_not_reach_child_alive() -> None:
    """Regression guard: child_alive=None must still be safe.

    When the typed cause is NOT in the chain (legacy construction sites
    that did not set child_alive), the classifier must still classify
    correctly. The conservative policy routes child_alive=None to Rule 2
    (STALE_CHILD_QUIET) for backward compat with existing tests that do
    not set child_alive.
    """
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
    # No typed watchdog cause in the chain -> child_alive defaults to
    # None (conservative policy: STALE_CHILD_QUIET / Rule 2).
    assert classified.category == FailureCategory.AGENT
    assert classified.is_unavailable is True
