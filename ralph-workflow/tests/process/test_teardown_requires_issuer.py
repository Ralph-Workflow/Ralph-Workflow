from __future__ import annotations

import os
import signal
from datetime import UTC, datetime
from typing import Any

import psutil
import pytest

from ralph.process.manager import ProcessRecord, ProcessStatus
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.process.teardown import DefaultProcessTeardown, teardown_subtree


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


def _install_own_tree(monkeypatch: pytest.MonkeyPatch, kills: list[int]) -> None:
    host = _FakeProcess(4242, _FakeProcess(os.getpid(), None, kills), kills)

    def fake_process(pid: int) -> _FakeProcess:
        if pid == 4242:
            return host
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", fake_process)


def _raise_no_such(pid: int) -> None:
    raise psutil.NoSuchProcess(pid)


def _untyped_fixture(value: str) -> Any:
    return value


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


def test_module_teardown_subtree_rejects_plain_str_issuer() -> None:
    with pytest.raises(TypeError, match="ProcessTerminationIssuer"):
        teardown_subtree(4242, issuer=_untyped_fixture("invoke:process"))


def test_default_teardown_rejects_plain_str_issuer() -> None:
    teardown = DefaultProcessTeardown(kill_escalation_ms=0.0)
    with pytest.raises(TypeError, match="ProcessTerminationIssuer"):
        teardown.teardown_subtree(4242, issuer=_untyped_fixture("invoke:pty"))


def test_default_teardown_rejects_arbitrary_str_subclass_issuer() -> None:
    teardown = DefaultProcessTeardown(kill_escalation_ms=0.0)
    with pytest.raises(TypeError, match="ProcessTerminationIssuer"):
        teardown.teardown_subtree(4242, issuer=_untyped_fixture("operator"))


def test_owner_label_family_mismatch_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    group_signals: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        group_signals.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg)
    _install_own_tree(monkeypatch, kills)

    DefaultProcessTeardown(
        kill_escalation_ms=0.0,
        record_lookup=lambda _pid: _record("executor:run-process"),
    ).teardown_subtree(4242, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)

    assert kills == []
    assert group_signals == []


def test_owner_label_family_match_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    kills: list[int] = []
    _install_own_tree(monkeypatch, kills)

    DefaultProcessTeardown(
        kill_escalation_ms=0.0,
        record_lookup=lambda _pid: _record("executor:run-process"),
    ).teardown_subtree(
        4242,
        issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
        owner_label="executor:run-process",
    )

    assert kills == [4242]


def test_owner_label_match_signals_stale_session_group(monkeypatch: pytest.MonkeyPatch) -> None:
    group_signals: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        group_signals.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setattr(psutil, "Process", lambda pid: _untyped_fixture(_raise_no_such(pid)))
    DefaultProcessTeardown(
        kill_escalation_ms=0.0,
        record_lookup=lambda _pid: _record("executor:run-process"),
    ).teardown_subtree(
        999_999,
        issuer=ProcessTerminationIssuer.EXECUTOR_CANCEL,
        owner_label="executor:run-process",
        pgid=999_999,
    )

    assert (999_999, signal.SIGTERM) in group_signals
