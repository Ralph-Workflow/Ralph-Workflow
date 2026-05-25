"""Tests for ManagedProcess.communicate_and_cleanup."""

from __future__ import annotations

import itertools
import subprocess
import sys

import pytest

from ralph.process.manager import ManagedProcess, ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.testing.fake_process import make_sync_process_factory

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
)


def _make_handle(returncode: int = 0) -> ManagedProcess:
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=returncode),
    )
    return pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="test:managed-process"))


class TestManagedProcessCommunicateAndCleanup:
    def test_returns_output_from_communicate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"out", b"err")
        )

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"out"
        assert stderr == b"err"

    def test_no_descendants_skips_cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"", b"")
        )
        monkeypatch.setattr(handle, "has_live_descendants", lambda: False)
        calls: list[str] = []
        monkeypatch.setattr(
            handle, "terminate", lambda grace_period_s=None: calls.append("terminate")
        )
        monkeypatch.setattr(handle, "kill", lambda: calls.append("kill"))

        handle.communicate_and_cleanup()

        assert calls == []

    def test_live_descendants_trigger_terminate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"", b"")
        )
        state = iter([True, False, False])
        calls: list[float | str | None] = []
        monkeypatch.setattr(handle, "has_live_descendants", lambda: next(state))
        monkeypatch.setattr(
            handle,
            "terminate",
            lambda grace_period_s=None: calls.append(grace_period_s),
        )
        monkeypatch.setattr(handle, "kill", lambda: calls.append("kill"))

        handle.communicate_and_cleanup(cleanup_grace_period_s=0.25)

        assert calls == [0.25]

    def test_persistent_descendants_trigger_kill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"", b"")
        )
        state = iter([True, True, False])
        calls: list[str] = []
        monkeypatch.setattr(handle, "has_live_descendants", lambda: next(state))
        monkeypatch.setattr(
            handle, "terminate", lambda grace_period_s=None: calls.append("terminate")
        )
        monkeypatch.setattr(handle, "kill", lambda: calls.append("kill"))

        handle.communicate_and_cleanup()

        assert calls == ["terminate", "kill"]

    def test_cleanup_uses_grace_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"", b"")
        )
        state = iter([True, False, False])
        seen: list[float | None] = []
        monkeypatch.setattr(handle, "has_live_descendants", lambda: next(state))
        monkeypatch.setattr(
            handle, "terminate", lambda grace_period_s=None: seen.append(grace_period_s)
        )
        monkeypatch.setattr(handle, "kill", lambda: None)

        handle.communicate_and_cleanup(cleanup_grace_period_s=0.75)

        assert seen == [0.75]

    def test_timeout_triggers_terminate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle,
            "communicate",
            lambda input=None, timeout=None: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["python"], timeout=0.1)
            ),
        )
        calls: list[str] = []
        monkeypatch.setattr(
            handle, "terminate", lambda grace_period_s=None: calls.append("terminate")
        )

        with pytest.raises(subprocess.TimeoutExpired):
            handle.communicate_and_cleanup()

        assert calls == ["terminate"]

    def test_timeout_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle,
            "communicate",
            lambda input=None, timeout=None: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["python"], timeout=0.1)
            ),
        )
        monkeypatch.setattr(handle, "terminate", lambda grace_period_s=None: None)

        with pytest.raises(subprocess.TimeoutExpired):
            handle.communicate_and_cleanup()

    def test_descendants_are_rechecked_before_kill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = _make_handle()
        monkeypatch.setattr(
            handle._proc, "communicate", lambda input=None, timeout=None: (b"", b"")
        )
        state = iter([True, True, True, False])
        checks: list[bool] = []
        monkeypatch.setattr(
            handle, "has_live_descendants", lambda: checks.append(next(state)) or checks[-1]
        )
        monkeypatch.setattr(handle, "terminate", lambda grace_period_s=None: None)
        monkeypatch.setattr(handle, "kill", lambda: None)

        handle.communicate_and_cleanup()

        assert checks[:3] == [True, True, True]
