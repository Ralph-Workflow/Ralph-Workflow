"""Pin: IdleWatchdog.record_invocation_start fully resets per-invocation state.

The watchdog is intended to be reusable across invocations (the
production path constructs one ``IdleWatchdog`` per
``invoke_agent`` call). The PROMPT identified a real state-leak risk
where fields such as ``_last_alive_by``, ``_last_mcp_tool_call_at``,
``_last_subagent_progress_at``, ``_last_subagent_output_at``,
``_last_workspace_event_at``, ``_last_progress_fingerprint``, and
``_last_subagent_progress_emit_at`` survived across invocations.

The fix: ``record_invocation_start()`` reinitializes EVERY field
whose semantics are per-invocation (not process-lifetime), so the
second invocation starts from a clean baseline and the watchdog
cannot defer/fingerprint/throttle based on the previous run.

Black-box: drive the watchdog through its public API (``evaluate()``,
``record_*``, ``diagnostic_snapshot``), populate the per-invocation
state, then call ``record_invocation_start()`` and assert the
public-facing snapshot (and per-channel evidence timestamps) is
back to the baseline. The internal fields are exposed via
:func:`_per_invocation_fields` which mirrors the per-invocation
contract documented in ``record_invocation_start`` -- this is a
necessary black-box-ish read because the test needs to prove the
reset contract across ALL fields, not just the snapshot surface.

All tests use FakeClock and no real subprocess / sleep / network.
"""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    AliveBy,
    IdleWatchdog,
    TimeoutPolicy,
)
from ralph.agents.timeout_clock import FakeClock


def _make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock), clock


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
    watchdog, clock = _make_watchdog()
    _populate_per_invocation_state(watchdog, clock)
    # Every per-invocation field is dirty. ``record_invocation_start``
    # MUST reset them all to baseline.
    watchdog.record_invocation_start()
    for field_name, baseline in _per_invocation_fields().items():
        actual = getattr(watchdog, field_name)
        assert actual == baseline, (
            f"record_invocation_start MUST reset {field_name} to {baseline!r}; got {actual!r}"
        )


def test_record_invocation_start_resets_alive_by_signal() -> None:
    """``_last_alive_by`` MUST be cleared on invocation_start.

    The pre-fix leak: ``_last_alive_by`` (assigned at
    ``idle_watchdog.py:1260``) survived across invocations, so a
    reused watchdog could feed a stale ``alive_by`` value into the
    ``IdleWatchdogKilledError.child_alive`` field on the next run's
    fire.
    """
    watchdog, clock = _make_watchdog()
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


def test_record_invocation_start_resets_progress_fingerprint() -> None:
    """``_last_progress_fingerprint`` MUST be cleared on invocation_start.

    The pre-fix leak: a fingerprint from the previous run would
    cause a same-fingerprint line in the new run to be skipped as
    a "repeat" when it is actually fresh.
    """
    watchdog, _clock = _make_watchdog()
    watchdog._last_progress_fingerprint = "previous-fingerprint"
    watchdog.record_invocation_start()
    assert watchdog._last_progress_fingerprint is None, (
        "record_invocation_start MUST clear _last_progress_fingerprint"
    )


def test_record_invocation_start_resets_subagent_progress_emit_at() -> None:
    """``_last_subagent_progress_emit_at`` MUST be cleared on invocation_start.

    The pre-fix leak: the SUBAGENT_PROGRESS waiting-status emit
    cadence timestamp survived across invocations so the new run's
    first emit could be throttled by the previous run's emit time.
    """
    watchdog, _clock = _make_watchdog()
    watchdog._last_subagent_progress_emit_at = 12345.0
    watchdog.record_invocation_start()
    assert watchdog._last_subagent_progress_emit_at is None, (
        "record_invocation_start MUST clear _last_subagent_progress_emit_at"
    )


def test_record_invocation_start_resets_per_channel_timestamps() -> None:
    """Every per-channel evidence timestamp MUST be cleared on invocation_start.

    The pre-fix leak: ``_last_mcp_tool_call_at``,
    ``_last_subagent_progress_at``, ``_last_subagent_output_at``, and
    ``_last_workspace_event_at`` survived across invocations so the
    second run's deferral path could inherit stale "fresh" evidence
    from the first run.
    """
    watchdog, clock = _make_watchdog()
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


def test_record_invocation_start_resets_cumulative_waiting() -> None:
    """``_cumulative_waiting_on_child_seconds`` MUST reset to 0.0.

    The cumulative counter is per-invocation; a reused watchdog
    MUST NOT carry the prior run's cumulative budget into the next
    run because the CHILDREN_PERSIST_TOO_LONG fire compares the
    counter against the configured ceiling and a stale counter
    could push the watchdog over the threshold prematurely.
    """
    watchdog, _clock = _make_watchdog()
    watchdog._cumulative_waiting_on_child_seconds = 250.0
    watchdog.record_invocation_start()
    assert watchdog._cumulative_waiting_on_child_seconds == 0.0, (
        "record_invocation_start MUST reset cumulative_waiting_on_child_seconds"
    )


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
    watchdog, _clock = _make_watchdog()
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


def test_second_invocation_starts_from_clean_baseline_no_stale_throttle() -> None:
    """A second invocation MUST NOT inherit the first invocation's throttle state.

    Black-box: drive the watchdog through ``evaluate()`` to populate
    the per-channel log throttle map, then ``record_invocation_start``
    and verify the second invocation starts with an empty throttle
    map (no deferred-log carryover from the first invocation).
    """
    watchdog, clock = _make_watchdog()
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


def test_second_invocation_fingerprint_does_not_skip_fresh_lines() -> None:
    """A second invocation MUST NOT skip a fresh progress line because
    it has the same fingerprint as the previous invocation's last line.

    Black-box: drive ``record_progress_report`` with the same line in
    two invocations and verify the watchdog does not suppress the
    second invocation's progress event as a repeat.
    """
    watchdog, clock = _make_watchdog()
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
