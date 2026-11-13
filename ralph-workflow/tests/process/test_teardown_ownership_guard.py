from __future__ import annotations

import os
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psutil

from ralph.process.manager import ProcessRecord, ProcessStatus
from ralph.process.teardown import (
    DefaultProcessTeardown,
    register_child_session,
    teardown_subtree,
    verified_child_session_pgid,
)

if TYPE_CHECKING:
    import pytest


class _FakeProcess:
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

    def terminate(self, **_kwargs: object) -> None:
        self._kills.append(self.pid)

    def kill(self, **_kwargs: object) -> None:
        self._kills.append(self.pid)

    def is_running(self) -> bool:
        return False


def _no_such_process(pid: int) -> _FakeProcess:
    raise psutil.NoSuchProcess(pid)


def _refuse_to_enumerate(pid: int) -> _FakeProcess:
    msg = f"teardown enumerated PID {pid}: it must be refused before enumeration"
    raise AssertionError(msg)


def _capture_group_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
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
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _refuse_to_enumerate)

    teardown_subtree(1, issuer="invoke:test")

    assert group_signals == []


def test_pid_zero_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _refuse_to_enumerate)

    teardown_subtree(0, issuer="invoke:test")

    assert group_signals == []


def test_live_process_we_did_not_spawn_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    group_signals = _capture_group_signals(monkeypatch)
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    teardown_subtree(4242, issuer="invoke:test")

    assert kills == [], f"signalled a foreign process tree: {kills}"
    assert group_signals == []


def test_own_descendant_is_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_group_signals(monkeypatch)
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242, issuer="invoke:test")

    assert kills == [4242]


def test_dead_host_without_verified_pgid_does_not_signal_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(999_999, issuer="invoke:test")

    assert group_signals == [], (
        "blind killpg on a dead PID — this SIGKILLs whichever unrelated "
        "process group inherited that number"
    )


def test_dead_host_with_verified_pgid_still_reaps_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(
        999_999, issuer="invoke:test", pgid=999_999
    )

    assert (999_999, signal.SIGTERM) in group_signals


def test_own_process_group_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    own_pgid = os.getpgid(0)
    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(
        own_pgid, issuer="invoke:test", pgid=own_pgid
    )

    assert group_signals == []


def test_verified_pgid_rejects_processes_we_did_not_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    def leader_of_itself(pid: int) -> int:
        return pid

    monkeypatch.setattr(os, "getpgid", leader_of_itself)

    assert verified_child_session_pgid(4242) is None


def test_registered_session_is_reaped_after_its_leader_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)
    assert register_child_session(4242) == 4242

    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)

    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242, issuer="invoke:test")

    assert (4242, signal.SIGTERM) in group_signals


def test_registration_is_dropped_once_the_subtree_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)
    register_child_session(4242)

    monkeypatch.setattr(psutil, "Process", _no_such_process)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242, issuer="invoke:test")

    group_signals = _capture_group_signals(monkeypatch)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242, issuer="invoke:test")

    assert group_signals == []


def test_registration_refuses_a_pid_we_do_not_own(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=1)

    def leader_of_itself(pid: int) -> int:
        return pid

    monkeypatch.setattr(os, "getpgid", leader_of_itself)

    assert register_child_session(4242) is None

    group_signals = _capture_group_signals(monkeypatch)
    monkeypatch.setattr(psutil, "Process", _no_such_process)
    DefaultProcessTeardown(kill_escalation_ms=0.0).teardown_subtree(4242, issuer="invoke:test")

    assert group_signals == []


def _record(label: str) -> ProcessRecord:
    return ProcessRecord(
        pid=4242,
        pgid=4242,
        command=("agent",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
        label=label,
    )


def test_matching_issuer_family_reaps_labeled_process(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    _capture_group_signals(monkeypatch)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    DefaultProcessTeardown(
        kill_escalation_ms=0.0,
        record_lookup=lambda _pid: _record("invoke:plan:pi"),
    ).teardown_subtree(4242, issuer="invoke:completion:pi")

    assert kills == [4242]


def test_cross_family_issuer_refuses_labeled_process(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    group_signals = _capture_group_signals(monkeypatch)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    DefaultProcessTeardown(
        kill_escalation_ms=0.0,
        record_lookup=lambda _pid: _record("executor:run-process"),
    ).teardown_subtree(4242, issuer="invoke:completion:pi")

    assert kills == []
    assert group_signals == []


def test_verified_pgid_rejects_pid_one() -> None:
    assert verified_child_session_pgid(1) is None


def test_verified_pgid_rejects_non_session_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def shared_group(pid: int) -> int:
        del pid
        return 7

    monkeypatch.setattr(os, "getpgid", shared_group)

    assert verified_child_session_pgid(4242) is None


def test_verified_pgid_accepts_our_own_session_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    own_pgid = os.getpgid(0)
    _install_tree(monkeypatch, kills, host_pid=4242, parent_pid=os.getpid())

    def child_leads_own_session(pid: int) -> int:
        return 4242 if pid == 4242 else own_pgid

    monkeypatch.setattr(os, "getpgid", child_leads_own_session)

    assert verified_child_session_pgid(4242) == 4242
