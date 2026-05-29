"""Liveness verification tests for verify_process_liveness helper.

Tests cover all four LivenessResult states (ALIVE, GONE, ZOMBIE, UNKNOWN)
across POSIX, Windows, and psutil-based detection paths with monkeypatched
platform detection and injected FakePsutil. No real OS processes spawned.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from ralph.process.manager import LivenessResult, verify_process_liveness
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess


# ────────────────────────────────────────────────────────────────────────────
# POSIX path: os.kill(pid, 0) based liveness checks
# ────────────────────────────────────────────────────────────────────────────


def test_liveness_check_alive_process() -> None:
    """os.kill(pid, 0) succeeds → returns ALIVE."""
    result = verify_process_liveness(os.getpid(), psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_gone_process() -> None:
    """os.kill(pid, 0) raises ProcessLookupError → returns GONE."""

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.GONE


def test_liveness_check_zombie_process() -> None:
    """psutil status returns 'zombie' → returns ZOMBIE."""
    zombie = FakePsutilProcess(pid=1, _running=True, _status="zombie")
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: zombie}

    result = verify_process_liveness(1, psutil_mod=fake_psutil)
    assert result == LivenessResult.ZOMBIE


def test_liveness_check_no_permission() -> None:
    """os.kill(pid, 0) raises PermissionError → returns ALIVE (process exists)."""

    def _kill_raises_permission(pid: int, sig: int) -> None:
        raise PermissionError(pid, sig)

    with patch("os.kill", _kill_raises_permission):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_other_os_error() -> None:
    """os.kill raises OSError (not ProcessLookupError or PermissionError) → UNKNOWN."""

    def _kill_raises_other(pid: int, sig: int) -> None:
        raise OSError("some other error")

    with patch("os.kill", _kill_raises_other):
        result = verify_process_liveness(99999, psutil_mod=None)
    assert result == LivenessResult.UNKNOWN


def test_liveness_check_no_psutil_fallback() -> None:
    """psutil=None falls back to os.kill only."""
    result = verify_process_liveness(os.getpid(), psutil_mod=None)
    assert result == LivenessResult.ALIVE


def test_liveness_check_windows_no_kill() -> None:
    """hasattr(os, 'kill') is False → uses psutil fallback."""
    # Simulate Windows: os.kill not available
    with (
        patch("os.kill", side_effect=AttributeError("no kill on Windows")),
        patch("os.__dict__", {}),
    ):
        # Force hasattr check to return False for 'kill'
        import builtins
        _orig_hasattr = builtins.hasattr

        def _fake_hasattr(obj: object, name: str) -> bool:
            if obj is os and name == "kill":
                return False
            return _orig_hasattr(obj, name)

        with patch("builtins.hasattr", _fake_hasattr):
            # Without psutil, fallback to UNKNOWN
            result = verify_process_liveness(1, psutil_mod=None)
            assert result == LivenessResult.UNKNOWN

    # With psutil that has pid_exists
    class _PsutilWithPidExists(FakePsutil):
        def pid_exists(self, pid: int) -> bool:
            return True

    fake_psutil = _PsutilWithPidExists()

    import builtins
    _orig_hasattr = builtins.hasattr

    def _fake_hasattr2(obj: object, name: str) -> bool:
        if obj is os and name == "kill":
            return False
        return _orig_hasattr(obj, name)

    with patch("builtins.hasattr", _fake_hasattr2):
        result = verify_process_liveness(1, psutil_mod=fake_psutil)
        assert result == LivenessResult.ALIVE


def test_liveness_check_zombie_takes_priority_over_posix() -> None:
    """Zombie detection via psutil takes priority over os.kill result."""
    zombie = FakePsutilProcess(pid=1, _running=True, _status="zombie")
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: zombie}

    # Even though os.kill(1, 0) would succeed (process technically exists),
    # zombie status should be returned
    with patch("os.kill", return_value=None):
        result = verify_process_liveness(1, psutil_mod=fake_psutil)
    assert result == LivenessResult.ZOMBIE
