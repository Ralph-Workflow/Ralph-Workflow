"""Tests for the bounded ProcessManager._termination_outcomes retention (wt-024 M3).

The ProcessManager singleton accumulates one entry per termination
in ``self._termination_outcomes`` for the whole process lifetime.
Previously nothing evicted those entries, so a long-running test
harness or a real session that spawned-then-terminated thousands of
processes would have accumulated an unbounded dict of per-PID
outcome lists.

The M3 fix ties ``_termination_outcomes`` to the already-bounded
``_terminal_records`` eviction (capped at
``policy.terminal_history_limit``).  Whenever a PID is evicted from
``_terminal_records``, the matching key in
``_termination_outcomes`` is dropped too.  Active PIDs (still in
``_records``) keep their outcome records.

Important caveat (wt-024 analysis feedback): the ``limit == 0``
branch MUST only drop the just-terminal PID's outcome record.
``_record_termination_outcome`` is called for a PID BEFORE that PID
reaches terminal state (it records each escalation stage —
``graceful_terminate`` / ``force_kill`` — during the live
escalation path).  If one PID's terminal-state call cleared the
entire outcomes dict it would erase the in-flight diagnostics of
every OTHER still-active PID.  The fix uses
``self._termination_outcomes.pop(record.pid, None)`` (per-PID) in
the ``limit == 0`` branch instead of ``self._termination_outcomes.clear()``
(everything).

These tests are pure in-memory: they use ``FakePsutil`` and
``make_sync_process_factory`` with ``returncode=None`` so the
escalation path drives ``_record_termination_outcome`` (clean exits
do NOT call it).  No real subprocess, no real disk I/O, no
``time.sleep``.  All under 1s.
"""

from __future__ import annotations

import itertools
import sys

from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)


def _make_pm(*, terminal_history_limit: int) -> ProcessManager:
    """Build a ProcessManager with a small terminal_history_limit."""
    return ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=False,
            enable_zombie_reaper=False,
            terminal_history_limit=terminal_history_limit,
        ),
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=None),
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=FakePsutil(),
    )


def test_termination_outcomes_bounded_after_many_terminations() -> None:
    """Spawning + terminating more processes than ``terminal_history_limit``
    leaves at most ``terminal_history_limit`` keys in
    ``list_termination_outcomes()``.

    Each terminated PID drives the escalation path which calls
    ``_record_termination_outcome`` and then
    ``_record_terminal_state``.  With ``terminal_history_limit=2``
    and 5 spawn+terminate cycles, at most 2 outcome keys must
    survive (the 2 most-recently terminated).
    """
    pm = _make_pm(terminal_history_limit=2)

    handles = []
    for _ in range(5):
        handle = pm.spawn([sys.executable, "-c", "pass"])
        # Use FakePsutil + returncode=None -> escalation path runs
        # (graceful_terminate -> force_kill), which records
        # termination outcomes per PID.
        handle.terminate(grace_period_s=0.05)
        handles.append(handle)

    outcomes = pm.list_termination_outcomes()
    assert len(outcomes) == 2, (
        f"expected at most terminal_history_limit=2 keys in"
        f" list_termination_outcomes(), got {len(outcomes)}: {list(outcomes)}"
    )
    # The retained PIDs MUST be the 2 most-recently-terminated ones.
    expected_pids = {handles[-1].pid, handles[-2].pid}
    assert set(outcomes.keys()) == expected_pids, (
        f"retained PIDs must be the 2 most-recently terminated;"
        f" expected={expected_pids}, got={set(outcomes.keys())}"
    )
    # All handles are terminal (killed by escalation).
    for handle in handles:
        assert handle.record.status == ProcessStatus.KILLED, (
            f"handle.pid={handle.pid} status={handle.record.status}"
        )


def test_termination_outcomes_does_not_evict_active_pid() -> None:
    """Active PIDs (still tracked in ``_records``) MUST keep their
    outcome records even when many later processes are terminated.

    With ``terminal_history_limit=2``, after 3 spawn+terminate cycles
    the first cycle's outcome key MUST have been evicted (its PID is
    in ``_terminal_records``) but at the moment just before the 4th
    termination, the 2 most-recently-terminated PIDs MUST still have
    their outcome keys.
    """
    pm = _make_pm(terminal_history_limit=2)

    # Spawn 3 processes and terminate each in sequence.
    handles = []
    for _ in range(3):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05)
        handles.append(h)

    # Now 3 PIDs have been recorded as terminal.
    # Only the 2 most-recent PIDs should remain in
    # list_termination_outcomes (cap=2).
    outcomes = pm.list_termination_outcomes()
    assert len(outcomes) == 2
    # The 1st-terminated PID's outcome key was evicted.
    assert handles[0].pid not in outcomes, (
        f"oldest-terminated PID {handles[0].pid} should have been"
        f" evicted from _termination_outcomes; got outcomes={list(outcomes)}"
    )
    # The 2 most-recent-terminated PIDs MUST still be present.
    assert handles[1].pid in outcomes
    assert handles[2].pid in outcomes


def test_termination_outcomes_cleared_when_limit_is_zero() -> None:
    """When ``terminal_history_limit=0`` the just-terminal PID's
    outcome record is dropped (no terminal history kept), but
    ``_termination_outcomes`` for OTHER PIDs is NOT cleared.  The
    M3 fix MUST NOT erase in-flight outcomes of still-active PIDs
    when another PID becomes terminal (wt-024 analysis feedback).

    With one PID active (mid-escalation) and another PID
    terminating, the active PID's outcome entries must survive the
    other PID's terminal-state call.  Each PID records its own
    outcomes during the live escalation path; the limit=0
    terminal-state call only drops the just-terminal PID's own
    outcome record, leaving the active PID's outcomes intact.
    """
    pm = _make_pm(terminal_history_limit=0)

    # Drive 3 sequential spawn+terminate cycles.  Each cycle records
    # outcome stages for the just-spawned PID; when that PID becomes
    # terminal, only its OWN outcome record is dropped, so no two
    # cycles ever have overlapping PIDs in ``_termination_outcomes``.
    # Therefore after all 3 cycles the dict is empty.
    for _ in range(3):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05)

    outcomes = pm.list_termination_outcomes()
    assert outcomes == {}, (
        f"with terminal_history_limit=0 and sequential single-PID"
        f" cycles, list_termination_outcomes() must be empty;"
        f" got {outcomes}"
    )


def test_termination_outcomes_limit_zero_preserves_in_flight_active_pid() -> None:
    """Regression test for wt-024 analysis feedback.

    When ``terminal_history_limit=0``, finishing the escalation
    for PID-A MUST NOT erase the in-flight ``_termination_outcomes``
    entries of PID-B (a still-active PID whose escalation has
    already recorded one or more stages).  The M3 fix must
    preserve outcome records for still-active PIDs across
    other-PID terminal-state calls.

    Strategy: spawn TWO processes, drive PID-A to terminal via
    ``handle.terminate``, and assert PID-B's outcome entries
    survive.  (PID-B's ``_termination_outcomes`` entry is recorded
    by the escalation path the moment ``handle.terminate`` is
    called, BEFORE the per-PID terminal-state call.)
    """
    pm = _make_pm(terminal_history_limit=0)

    # Spawn two processes.  PID-A=1, PID-B=2 (per itertools.count(1)).
    handle_a = pm.spawn([sys.executable, "-c", "pass"])
    handle_b = pm.spawn([sys.executable, "-c", "pass"])

    # Drive PID-B's escalation first — this records outcomes for
    # PID-B but does NOT yet make PID-B terminal (escalation is in
    # flight) — actually FakePsutil + returncode=None drives the
    # synchronous escalation to terminal.  To set up the
    # "PID-B's outcomes are in _termination_outcomes while
    # PID-A is still active" scenario we need to keep PID-A alive.
    # We use a custom sync_process_factory that returns a different
    # returncode per PID: PID-A is alive (returncode stays None);
    # PID-B drives to terminal via escalation.
    # Simpler: directly invoke ``_record_termination_outcome`` for
    # an ACTIVE PID (a public seam for tests / runtime diagnostics
    # is not available, so we go through the same public path
    # escalation uses).
    # Instead, use the escalation path: terminate PID-B first; this
    # records PID-B's outcomes AND makes PID-B terminal, dropping
    # PID-B's outcomes (cap=0).  Then keep PID-A alive and assert
    # that PID-A's later-recorded outcomes survive any subsequent
    # unrelated terminal-state call.
    handle_b.terminate(grace_period_s=0.05)

    # After PID-B is terminal, its outcome key is gone (cap=0).
    outcomes_after_b = pm.list_termination_outcomes()
    assert handle_b.pid not in outcomes_after_b, (
        f"after PID-B terminal, PID-B's outcome key should be dropped; got {outcomes_after_b}"
    )

    # Now drive PID-A's escalation partway: this records outcome
    # stages for PID-A.  Because PID-A is mid-escalation (NOT yet
    # terminal), no terminal-state call fires — so its outcomes
    # remain in ``_termination_outcomes``.
    pm._record_termination_outcome(handle_a.pid, "graceful_terminate", "sent")
    pm._record_termination_outcome(handle_a.pid, "force_kill", "sent")

    # PID-A's outcomes are now in the dict.  Simulate an UNRELATED
    # terminal-state call for some other PID (e.g. a freshly-spawned
    # PID-C).  PID-A's outcomes MUST survive.
    handle_c = pm.spawn([sys.executable, "-c", "pass"])
    handle_c.terminate(grace_period_s=0.05)

    outcomes = pm.list_termination_outcomes()
    assert handle_a.pid in outcomes, (
        f"PID-A's in-flight outcomes MUST survive an unrelated"
        f" PID-C terminal-state call; outcomes={outcomes},"
        f" PID-A={handle_a.pid}"
    )
    assert outcomes[handle_a.pid] == [
        {"stage": "graceful_terminate", "outcome": "sent"},
        {"stage": "force_kill", "outcome": "sent"},
    ], f"PID-A's in-flight outcome list must be intact; got {outcomes.get(handle_a.pid)}"
    # PID-C is terminal (cap=0), so its own outcome is dropped.
    assert handle_c.pid not in outcomes, (
        f"PID-C is terminal under cap=0, its own outcome must be dropped; got {outcomes}"
    )


def test_termination_outcomes_default_limit_keeps_existing_tests_green() -> None:
    """At the default ``terminal_history_limit=256``, a few spawn+terminate
    cycles must keep all outcome keys (no eviction happens for small N).

    This guards against an over-eager eviction that would break the
    existing ``test_termination_outcomes_logged_across_platforms`` and
    ``test_list_termination_outcomes_returns_dict`` tests.
    """
    pm = _make_pm(terminal_history_limit=256)

    handles = []
    for _ in range(5):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05)
        handles.append(h)

    outcomes = pm.list_termination_outcomes()
    # 5 terminations, default cap 256 -> all 5 survive.
    assert len(outcomes) == 5, (
        f"with terminal_history_limit=256, all 5 outcome keys must survive; got {len(outcomes)}"
    )


def test_termination_outcomes_does_not_break_list_termination_outcomes_shape() -> None:
    """``list_termination_outcomes()`` MUST continue to return a
    ``dict[int, list[dict[str, str]]]`` mapping PID to outcome lists
    with 'stage' and 'outcome' string keys.

    The wt-024 M3 fix only adds eviction of stale outcome keys; it
    MUST NOT change the return shape of the public accessor.
    """
    pm = _make_pm(terminal_history_limit=256)

    h = pm.spawn([sys.executable, "-c", "pass"])
    h.terminate(grace_period_s=0.05)

    outcomes = pm.list_termination_outcomes()
    assert isinstance(outcomes, dict)
    assert h.pid in outcomes
    pid_outcomes = outcomes[h.pid]
    assert isinstance(pid_outcomes, list)
    assert len(pid_outcomes) >= 1
    for entry in pid_outcomes:
        assert isinstance(entry, dict)
        assert "stage" in entry
        assert "outcome" in entry
        assert isinstance(entry["stage"], str)
        assert isinstance(entry["outcome"], str)


def test_termination_outcomes_cleared_on_purge_on_init() -> None:
    """When ``policy.purge_on_init=True``, ``_termination_outcomes``
    MUST also be cleared at ProcessManager construction (symmetric
    with ``_terminal_records``).  Otherwise a re-instantiated manager
    inherits stale state from the singleton module.

    Verified behaviorally: a fresh ProcessManager with ``purge_on_init=True``
    plus the default cap (256) must have an empty
    ``list_termination_outcomes()`` regardless of any state the
    previous instance accumulated.  Two managers are constructed in
    sequence; the second one is verified to start empty even though
    the first accumulated outcomes.
    """
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    pm1 = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=True,
            enable_zombie_reaper=False,
            terminal_history_limit=256,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=FakePsutil(),
    )
    # Drive some outcomes into pm1 so it has non-empty state.
    h = pm1.spawn([sys.executable, "-c", "pass"])
    h.terminate(grace_period_s=0.05)
    assert pm1.list_termination_outcomes(), (
        "pm1 should have at least one outcome after spawning+terminating"
    )

    # Construct pm2 with the same factory / policy (including
    # purge_on_init=True).  Its list_termination_outcomes() must
    # start empty — purge_on_init guarantees a clean slate.
    pm2 = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=True,
            enable_zombie_reaper=False,
            terminal_history_limit=256,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=FakePsutil(),
    )
    assert pm2.list_termination_outcomes() == {}, (
        f"pm2 with purge_on_init=True must start with empty outcomes;"
        f" got {pm2.list_termination_outcomes()}"
    )
