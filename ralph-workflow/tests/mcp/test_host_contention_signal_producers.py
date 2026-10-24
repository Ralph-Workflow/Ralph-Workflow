"""Tests for real host-contention signal producers."""

from __future__ import annotations

import pathlib
from collections import Counter
from datetime import timedelta
from unittest import mock

import pytest

from ralph.mcp.server.lifecycle import pop_recent_host_contention_signals

#: Wide enough window to include every recorded contention signal,
#: including the ones stamped through the producers' injected clock.
_ALL_SIGNALS_WINDOW_SECONDS = 1e12


def _recorded_reasons() -> list[str]:
    """Return the reasons currently recorded in the public signal registry."""
    return [
        reason
        for _timestamp, reason in pop_recent_host_contention_signals(_ALL_SIGNALS_WINDOW_SECONDS)
    ]


class _FakeProcess:
    """Minimal fake psutil.Process stand-in."""

    def __init__(self, status: str) -> None:
        self._status = status

    def status(self) -> str:
        return self._status


# ---------------------------------------------------------------------------
# record_d_state
# ---------------------------------------------------------------------------


def test_record_d_state_emits_on_disk_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """When psutil reports disk-sleep, a d_state signal is emitted."""
    import ralph.mcp.server._host_contention_signal as hcs

    fake_psutil = mock.MagicMock()
    fake_psutil.Process.return_value = _FakeProcess("disk-sleep")
    monkeypatch.setattr(hcs, "psutil", fake_psutil)

    before = _recorded_reasons()
    hcs.record_d_state(12345)
    after = _recorded_reasons()
    assert Counter(after) - Counter(before) == Counter({"d_state:disk-sleep": 1})


def test_record_d_state_missing_process_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the process does not exist, no exception propagates."""
    import ralph.mcp.server._host_contention_signal as hcs

    fake_psutil = mock.MagicMock()
    fake_psutil.Process.side_effect = FileNotFoundError("No such process")
    monkeypatch.setattr(hcs, "psutil", fake_psutil)

    # Must not raise
    hcs.record_d_state(99999)


# ---------------------------------------------------------------------------
# record_sigkill_survivor
# ---------------------------------------------------------------------------


def test_record_sigkill_survivor_emits_on_zombie(monkeypatch: pytest.MonkeyPatch) -> None:
    """When psutil reports zombie, a sigkill_survivor signal is emitted."""
    import ralph.mcp.server._host_contention_signal as hcs

    fake_psutil = mock.MagicMock()
    fake_psutil.Process.return_value = _FakeProcess("zombie")
    monkeypatch.setattr(hcs, "psutil", fake_psutil)

    before = _recorded_reasons()
    hcs.record_sigkill_survivor(12345)
    after = _recorded_reasons()
    assert Counter(after) - Counter(before) == Counter({"sigkill_survivor:zombie": 1})


def test_record_sigkill_survivor_missing_process_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the process does not exist, no exception propagates."""
    import ralph.mcp.server._host_contention_signal as hcs

    fake_psutil = mock.MagicMock()
    fake_psutil.Process.side_effect = PermissionError("access denied")
    monkeypatch.setattr(hcs, "psutil", fake_psutil)

    # Must not raise
    hcs.record_sigkill_survivor(99999)


# ---------------------------------------------------------------------------
# record_psi_pressure
# ---------------------------------------------------------------------------


def test_record_psi_pressure_cpu_avg10_80_emits(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When /proc/pressure/cpu some avg10 >= 50, a psi_pressure signal fires."""
    import ralph.mcp.server._host_contention_signal as hcs

    psi_dir = tmp_path / "proc" / "pressure"
    psi_dir.mkdir(parents=True)
    cpu_file = psi_dir / "cpu"
    cpu_file.write_text(
        "some avg10=80.0 avg60=30.0 avg300=20.0\nfull avg10=10.0 avg60=5.0 avg300=2.0\n"
    )

    monkeypatch.setattr(hcs, "psutil", None)

    class _FakePath:
        def __init__(self, target: object) -> None:
            self._target = target

        def open(self, *args: object, **kwargs: object) -> object:
            return cpu_file.open(*args, **kwargs)

    def fake_path_ctor(arg: object = None, *args: object, **kwargs: object) -> object:
        if str(arg) == "/proc/pressure/cpu":
            return _FakePath(cpu_file)
        cpu_file_str = str(cpu_file)
        if isinstance(arg, str) and arg == "/proc/pressure/cpu":
            return _FakePath(cpu_file_str)
        # arg is narrowed to str by the two prior str() / isinstance
        # checks above; the fall-through path constructs a real
        # pathlib.Path only when ``arg`` is a str-compatible value
        # (the test always supplies a str), so the str() conversion
        # here is the production-grade narrowing step.
        narrowed_arg: str = str(arg) if arg is not None else ""
        return pathlib.Path(narrowed_arg, *args, **kwargs)

    monkeypatch.setattr(hcs, "Path", fake_path_ctor)

    before = _recorded_reasons()
    hcs.record_psi_pressure()
    after = _recorded_reasons()
    new_reasons = list((Counter(after) - Counter(before)).elements())
    assert any("psi_pressure:cpu:80.0" in reason for reason in new_reasons)


def test_record_psi_pressure_no_proc_pressure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When /proc/pressure does not exist, no exception propagates."""
    import ralph.mcp.server._host_contention_signal as hcs

    monkeypatch.setattr(hcs, "psutil", None)

    # Make Path construction fail for /proc/pressure paths
    def bad_path(arg: object = None, *args: object, **kwargs: object) -> object:
        raise FileNotFoundError("/proc/pressure")

    monkeypatch.setattr(hcs, "Path", bad_path)

    # Must not raise — the function catches all exceptions
    hcs.record_psi_pressure()


# ---------------------------------------------------------------------------
# record_supervisor_tick_lateness
# ---------------------------------------------------------------------------


def test_record_supervisor_tick_lateness_emits_when_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When now - expected_at > check_interval * 2, a lateness signal fires."""
    import ralph.mcp.server._host_contention_signal as hcs

    # expected_at=0, _now returns 30, check_interval=5s → 30 > 10 → emit
    before = _recorded_reasons()
    hcs.record_supervisor_tick_lateness(
        0,
        check_interval=timedelta(seconds=5),
        _now=lambda: 30.0,
    )
    after = _recorded_reasons()
    assert Counter(after) - Counter(before) == Counter({"supervisor_tick_lateness": 1})


def test_record_supervisor_tick_lateness_does_not_emit_when_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When now - expected_at <= check_interval * 2, no signal is emitted."""
    import ralph.mcp.server._host_contention_signal as hcs

    # expected_at=0, _now returns 8, check_interval=5s → 8 <= 10 → no emit
    before = _recorded_reasons()
    hcs.record_supervisor_tick_lateness(
        0,
        check_interval=timedelta(seconds=5),
        _now=lambda: 8.0,
    )
    after = _recorded_reasons()
    assert Counter(after) - Counter(before) == Counter()
