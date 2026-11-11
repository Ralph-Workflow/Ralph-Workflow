from __future__ import annotations

import builtins
import os
from unittest.mock import patch

from ralph.process.manager import LivenessResult, verify_process_liveness
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess


def test_liveness_check_alive_process() -> None:
    result = verify_process_liveness(os.getpid(), psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_gone_process() -> None:

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.GONE


def test_liveness_check_zombie_process() -> None:
    zombie = FakePsutilProcess(pid=1, _running=True, _status="zombie")
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: zombie}

    result = verify_process_liveness(1, psutil_mod=fake_psutil)
    assert result == LivenessResult.ZOMBIE


def test_liveness_check_no_permission() -> None:

    def _kill_raises_permission(pid: int, sig: int) -> None:
        raise PermissionError(pid, sig)

    with patch("os.kill", _kill_raises_permission):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_other_os_error() -> None:

    def _kill_raises_other(pid: int, sig: int) -> None:
        raise OSError("some other error")

    with patch("os.kill", _kill_raises_other):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.UNKNOWN


def test_liveness_check_no_psutil_fallback() -> None:
    result = verify_process_liveness(os.getpid(), psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_windows_no_kill() -> None:
    with patch("os.kill", side_effect=AttributeError("no kill on Windows")):
        _orig_hasattr = builtins.hasattr

        def _fake_hasattr(obj: object, name: str) -> bool:
            if obj is os and name == "kill":
                return False
            return _orig_hasattr(obj, name)

        with patch("builtins.hasattr", _fake_hasattr):
            result = verify_process_liveness(1, psutil_mod=None)
            assert result == LivenessResult.UNKNOWN

    class _PsutilWithPidExists(FakePsutil):
        def pid_exists(self, pid: int) -> bool:
            return True

    fake_psutil = _PsutilWithPidExists()
    _orig_hasattr2 = builtins.hasattr

    def _fake_hasattr2(obj: object, name: str) -> bool:
        if obj is os and name == "kill":
            return False
        return _orig_hasattr2(obj, name)

    with patch("builtins.hasattr", _fake_hasattr2):
        result = verify_process_liveness(1, psutil_mod=fake_psutil)
        assert result == LivenessResult.ALIVE


def test_liveness_check_zombie_takes_priority_over_posix() -> None:
    zombie = FakePsutilProcess(pid=1, _running=True, _status="zombie")
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: zombie}

    with patch("os.kill", return_value=None):
        result = verify_process_liveness(1, psutil_mod=fake_psutil)
    assert result == LivenessResult.ZOMBIE
