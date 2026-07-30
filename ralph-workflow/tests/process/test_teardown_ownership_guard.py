"""Regression tests: teardown must never signal a process it does not own.

Observed 2026-07-25. ``make verify`` shut the developer's Mac down. Two paths
in ``ralph.process.teardown`` treated a bare PID as authority to kill:

1. ``teardown_subtree(1)`` — the fake process managers in the suite hand out
   ``itertools.count(1)`` PIDs, and ``run_process`` reaps ``handle.pid`` on
   every exit path. PID 1 is ``launchd``: the reaper enumerated its recursive
   descendants (every process on the host) and SIGTERM'd them all.
2. ``os.killpg(dead_pid, SIGKILL)`` — on the success path ``communicate()``
   has already reaped the child, so teardown always took the "host already
   exited" branch and signalled whatever group had inherited that number.

Both are pinned here. The guard is ownership: a live descendant of THIS
process, or a process group verified while its leader was alive.
"""

from __future__ import annotations

import os
import signal
from typing import TYPE_CHECKING

import psutil

from ralph.process.teardown import (
    DefaultProcessTeardown,
    register_child_session,
    teardown_subtree,
    verified_child_session_pgid,
)

if TYPE_CHECKING:
    import pytest


class _FakeProcess:
    """Stand-in for ``psutil.Process`` that records every signal it receives."""

    def __init__(self, pid: int, parent: _FakeProcess | None, kills: list[int]) -> None:
        self.pid = pid
        self._parent = parent
        self._kills = kills
        self._children: list[_FakeProcess] = []

    def parent(self) -> _FakeProcess | None:
        return self._parent

    def children(self, recursive: bool = False) -> list[_FakeProcess]:
        del recursive
        return list(self._children)

    def terminate(self) -> None:
        self._kills.append(self.pid)

    def kill(self) -> None:
        self._kills.append(self.pid)

    def is_running(self) -> bool:
        return False


def _no_such_process(pid: int) -> _FakeProcess:
    """``psutil.Process`` replacement for a PID that does not exist."""
    raise psutil.NoSuchProcess(pid)


def _refuse_to_enumerate(pid: int) -> _FakeProcess:
    """``psutil.Process`` replacement that fails the test if it is consulted."""
    msg = f"teardown enumerated PID {pid}: it must be refused before enumeration"
    raise AssertionError(msg)


def _capture_group_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Redirect ``os.killpg`` into a recorder and return it."""
    recorded: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        recorded.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg)
    return recorded


def _install_tree(
    monkeypatch: pytest.MonkeyPatch,
    kills: list[int],
    *,
    host_pid: int,
    parent_pid: int,
) -> None:
    """Make ``psutil.Process(host_pid)`` resolve to a host parented by ``parent_pid``."""
    parent = _FakeProcess(parent_pid, None, kills)
    host = _FakeProcess(host_pid, parent, kills)

    def fake_process(pid: int) -> _FakeProcess:
        if pid == host_pid:
            return host
        if pid == parent_pid:
            return parent
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", fake_process)


def test_pid_one_is_never_enumerated_or_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """teardown_subtree(1) must not touch init/launchd — its tree is the whole host."""
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _refuse_to_enumerate)

    teardown_subtree(1)

    assert group_signals == []


def test_pid_zero_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """PID 0 means "my own process group" to killpg — a self-kill."""
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _refuse_to_enumerate)

    teardown_subtree(0)

    assert group_signals == []


def test_live_process_we_did_not_spawn_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PID that is not our descendant belongs to someone else."""
    group_signals = _capture_group_signals(monkeypatch)
    kills: list[int] = []
    # The host's parent chain terminates at PID 1, never reaching os.getpid().
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    teardown_subtree(4242)

    assert kills == [], f"signalled a foreign process tree: {kills}"
    assert group_signals == []


def test_own_descendant_is_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not break the actual job: our own child still dies."""
    _capture_group_signals(monkeypatch)
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242)

    assert kills == [4242]


def test_dead_host_without_verified_pgid_does_not_signal_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exited PID carries no authority: its number may have been reused."""
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(999_999)

    assert group_signals == [], (
        "blind killpg on a dead PID — this SIGKILLs whichever unrelated "
        "process group inherited that number"
    )


def test_dead_host_with_verified_pgid_still_reaps_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group verified while its leader lived is safe to sweep after it exits."""
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(999_999, pgid=999_999)

    assert (999_999, signal.SIGTERM) in group_signals


def test_own_process_group_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signalling our own group takes down the caller and its siblings."""
    own_pgid = os.getpgid(0)
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(own_pgid, pgid=own_pgid)

    assert group_signals == []


def test_verified_pgid_rejects_processes_we_did_not_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PGID capture is the ownership proof — it must reject foreign PIDs."""
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    def leader_of_itself(pid: int) -> int:
        return pid

    monkeypatch.setattr(os, "getpgid", leader_of_itself)

    assert verified_child_session_pgid(4242) is None


def test_registered_session_is_reaped_after_its_leader_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn-time registration is what keeps orphan reaping working.

    Without it the ownership guard would silently narrow teardown: the leader
    is normally already reaped by ``communicate()`` when teardown runs, and a
    dead PID alone may never be signalled. Orphaned workers would then survive
    every call and accumulate until the host cannot start a thread — the
    original stall.
    """
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)
    assert register_child_session(4242) == 4242

    # The leader now exits: psutil can no longer enumerate it.
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242)

    assert (4242, signal.SIGTERM) in group_signals


def test_registration_is_dropped_once_the_subtree_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale entry would let a recycled PID inherit a stranger's authority."""
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)
    register_child_session(4242)

    monkeypatch.setattr(psutil, "Process", _no_such_process)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242)

    # Second teardown of the same PID: the entry is gone, so nothing is sent.
    group_signals = _capture_group_signals(monkeypatch)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242)

    assert group_signals == []


def test_registration_refuses_a_pid_we_do_not_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake process managers hand out fabricated PIDs; none may be banked."""
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    def leader_of_itself(pid: int) -> int:
        return pid

    monkeypatch.setattr(os, "getpgid", leader_of_itself)

    assert register_child_session(4242) is None

    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242)

    assert group_signals == []


def test_verified_pgid_rejects_pid_one() -> None:
    """PID 1 is a group leader on macOS; only the explicit guard rejects it."""
    assert verified_child_session_pgid(1) is None


def test_verified_pgid_rejects_non_session_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child sharing another group has no group of its own to signal."""
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def shared_group(pid: int) -> int:
        del pid
        return 7

    monkeypatch.setattr(os, "getpgid", shared_group)

    assert verified_child_session_pgid(4242) is None


def test_verified_pgid_accepts_our_own_session_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """A start_new_session child of ours is exactly what may be group-signalled."""
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)

    assert verified_child_session_pgid(4242) == 4242
