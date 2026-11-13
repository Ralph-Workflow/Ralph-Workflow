from __future__ import annotations

import itertools
import sys

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessTerminationIssuer
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)


def _make_pm(*, terminal_history_limit: int) -> ProcessManager:
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
    pm = _make_pm(terminal_history_limit=2)

    handles = []
    for _ in range(5):
        handle = pm.spawn([sys.executable, "-c", "pass"])
        handle.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)
        handles.append(handle)

    outcomes = pm.list_termination_outcomes()
    assert len(outcomes) == 2, (
        f"expected at most terminal_history_limit=2 keys in"
        f" list_termination_outcomes(), got {len(outcomes)}: {list(outcomes)}"
    )
    expected_pids = {handles[-1].pid, handles[-2].pid}
    assert set(outcomes.keys()) == expected_pids, (
        f"retained PIDs must be the 2 most-recently terminated;"
        f" expected={expected_pids}, got={set(outcomes.keys())}"
    )
    for handle in handles:
        assert handle.record.status == ProcessStatus.KILLED, (
            f"handle.pid={handle.pid} status={handle.record.status}"
        )


def test_termination_outcomes_does_not_evict_active_pid() -> None:
    pm = _make_pm(terminal_history_limit=2)

    handles = []
    for _ in range(3):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)
        handles.append(h)

    outcomes = pm.list_termination_outcomes()
    assert len(outcomes) == 2
    assert handles[0].pid not in outcomes, (
        f"oldest-terminated PID {handles[0].pid} should have been"
        f" evicted from _termination_outcomes; got outcomes={list(outcomes)}"
    )
    assert handles[1].pid in outcomes
    assert handles[2].pid in outcomes


def test_termination_outcomes_cleared_when_limit_is_zero() -> None:
    pm = _make_pm(terminal_history_limit=0)

    for _ in range(3):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)

    outcomes = pm.list_termination_outcomes()
    assert outcomes == {}, (
        f"with terminal_history_limit=0 and sequential single-PID"
        f" cycles, list_termination_outcomes() must be empty;"
        f" got {outcomes}"
    )


def test_termination_outcomes_limit_zero_preserves_in_flight_active_pid() -> None:
    pm = _make_pm(terminal_history_limit=0)

    handle_a = pm.spawn([sys.executable, "-c", "pass"])
    handle_b = pm.spawn([sys.executable, "-c", "pass"])

    handle_b.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)

    outcomes_after_b = pm.list_termination_outcomes()
    assert handle_b.pid not in outcomes_after_b, (
        f"after PID-B terminal, PID-B's outcome key should be dropped; got {outcomes_after_b}"
    )

    pm._record_termination_outcome(handle_a.pid, "graceful_terminate", "sent")
    pm._record_termination_outcome(handle_a.pid, "force_kill", "sent")

    handle_c = pm.spawn([sys.executable, "-c", "pass"])
    handle_c.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)

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
    assert handle_c.pid not in outcomes, (
        f"PID-C is terminal under cap=0, its own outcome must be dropped; got {outcomes}"
    )


def test_termination_outcomes_default_limit_keeps_existing_tests_green() -> None:
    pm = _make_pm(terminal_history_limit=256)

    handles = []
    for _ in range(5):
        h = pm.spawn([sys.executable, "-c", "pass"])
        h.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)
        handles.append(h)

    outcomes = pm.list_termination_outcomes()
    assert len(outcomes) == 5, (
        f"with terminal_history_limit=256, all 5 outcome keys must survive; got {len(outcomes)}"
    )


def test_termination_outcomes_does_not_break_list_termination_outcomes_shape() -> None:
    pm = _make_pm(terminal_history_limit=256)

    h = pm.spawn([sys.executable, "-c", "pass"])
    h.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)

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
    h = pm1.spawn([sys.executable, "-c", "pass"])
    h.terminate(grace_period_s=0.05, issuer=ProcessTerminationIssuer.OPERATOR)
    assert pm1.list_termination_outcomes(), (
        "pm1 should have at least one outcome after spawning+terminating"
    )

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
