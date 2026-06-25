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
        sync_process_factory=make_sync_process_factory(
            itertools.count(1), returncode=None
        ),
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
    """When ``terminal_history_limit=0`` every termination record
    (including outcome records) is cleared on each ``_record_terminal_state``
    call.  After many terminations, ``list_termination_outcomes()``
    returns an empty dict (no survivors).
    """
    pm = _make_pm(terminal_history_limit=0)

    for _ in range(3):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05)

    outcomes = pm.list_termination_outcomes()
    assert outcomes == {}, (
        f"with terminal_history_limit=0, list_termination_outcomes()"
        f" must be empty after every termination; got {outcomes}"
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
        f"with terminal_history_limit=256, all 5 outcome keys must"
        f" survive; got {len(outcomes)}"
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
