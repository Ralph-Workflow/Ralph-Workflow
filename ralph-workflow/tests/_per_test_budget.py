from __future__ import annotations

import math
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic as _monotonic
from time import thread_time as _thread_time
from typing import TYPE_CHECKING

import pytest

from ralph.runtime import DEFAULT_TEST_TIMEOUT_SECONDS, TEST_TIMEOUT_ENV, timeout_seconds_from_env

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType


class TestExecutionTimeoutError(BaseException):
    pass


_WALL_HANG_FACTOR = 5.0
_WALL_HANG_MAX_EXTRA_SECONDS = 60.0
_MIN_CHECK_SECONDS = 0.01
_RETRY_SECONDS = 0.25
_IDLE_WAKE_SECONDS = 60.0
_SIGNAL = signal.SIGPROF
_install_handler = signal.signal
_current_handler = signal.getsignal
_send_signal = signal.pthread_kill
_signal_mask = signal.pthread_sigmask


def wall_hang_cap(budget_seconds: float) -> float:
    return min(budget_seconds * _WALL_HANG_FACTOR, budget_seconds + _WALL_HANG_MAX_EXTRA_SECONDS)


def validated_budget(value: object, *, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{source} must be a number of seconds (got {value!r})")
    seconds = float(value)
    if not (math.isfinite(seconds) and seconds > 0):
        raise ValueError(
            f"{source} must be a positive, finite number of seconds (got {value!r}): "
            "zero, negative or infinite values would disable the per-test budget"
        )
    return seconds


def budget_for(item: pytest.Item) -> float:
    marker = item.get_closest_marker("timeout_seconds")
    if marker is not None and marker.args:
        return validated_budget(
            marker.args[0], source=f"@pytest.mark.timeout_seconds on {item.nodeid}"
        )
    return validated_budget(
        timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS),
        source=TEST_TIMEOUT_ENV,
    )


@dataclass
class _Phase:
    label: str
    budget: float
    wall_cap: float
    cpu_start: float
    wall_start: float
    next_check: float
    overrun: str | None = None
    finished: bool = False

    def check(self) -> str | None:
        now = _monotonic()
        cpu_used = _thread_time() - self.cpu_start
        if self.overrun is None:
            if cpu_used > self.budget:
                self.overrun = f"test {self.label} used more than {self.budget} CPU seconds"
            elif now - self.wall_start > self.wall_cap:
                self.overrun = (
                    f"test {self.label} exceeded the {self.wall_cap} s wall-clock hang cap "
                    f"({self.budget} s budget)"
                )
        if self.overrun is not None:
            self.next_check = now + _RETRY_SECONDS
        else:
            cpu_left = self.budget - cpu_used
            wall_left = self.wall_start + self.wall_cap - now
            self.next_check = now + max(_MIN_CHECK_SECONDS, min(cpu_left, wall_left))
        return self.overrun


class _Watchdog:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._phases: list[_Phase] = []
        self._thread: threading.Thread | None = None
        self._main_ident = threading.main_thread().ident
        self._handler = self._on_signal
        self._wake_at = math.inf

    def arm(self, label: str, budget: float, wall_cap: float | None = None) -> _Phase:
        now = _monotonic()
        cap = wall_hang_cap(budget) if wall_cap is None else wall_cap
        phase = _Phase(
            label=label,
            budget=budget,
            wall_cap=cap,
            cpu_start=_thread_time(),
            wall_start=now,
            next_check=now + min(budget, cap),
        )
        if _current_handler(_SIGNAL) is not self._handler:
            _install_handler(_SIGNAL, self._handler)
        with self._condition:
            self._phases.append(phase)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="ralph-per-test-budget", daemon=True
                )
                self._thread.start()
            elif phase.next_check < self._wake_at:
                self._condition.notify()
        return phase

    def disarm(self, phase: _Phase) -> str | None:
        with self._condition:
            phase.finished = True
            self._phases.remove(phase)
        tampered = self._repair_tampering()
        overrun = phase.check()
        return overrun if overrun is not None else tampered

    def _repair_tampering(self) -> str | None:
        problems: list[str] = []
        if _current_handler(_SIGNAL) is not self._handler:
            _install_handler(_SIGNAL, self._handler)
            problems.append("replaced the per-test budget's SIGPROF handler")
        if _SIGNAL in _signal_mask(signal.SIG_BLOCK, ()):
            _signal_mask(signal.SIG_UNBLOCK, (_SIGNAL,))
            problems.append("left SIGPROF blocked")
        if not problems:
            return None
        return (
            f"test {' and '.join(problems)}, which disarms the per-test budget; "
            "restore signal state before the test ends"
        )

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        with self._condition:
            phase = self._phases[-1] if self._phases else None
            if phase is None or phase.finished:
                return
            overrun = phase.check()
            self._condition.notify()
        if overrun is not None:
            raise TestExecutionTimeoutError(overrun)

    def _run(self) -> None:
        with self._condition:
            while True:
                phase = self._phases[-1] if self._phases else None
                if phase is None:
                    self._wake_at = _monotonic() + _IDLE_WAKE_SECONDS
                    self._condition.wait(_IDLE_WAKE_SECONDS)
                    continue
                delay = phase.next_check - _monotonic()
                if delay > 0:
                    self._wake_at = phase.next_check
                    self._condition.wait(delay)
                    continue
                phase.next_check = _monotonic() + _RETRY_SECONDS
                if self._main_ident is not None:
                    _send_signal(self._main_ident, _SIGNAL)


_WATCHDOG = _Watchdog()


@contextmanager
def phase_budget(
    label: str, budget: float, *, wall_cap: float | None = None
) -> Generator[None, None, None]:
    phase = _WATCHDOG.arm(label, budget, wall_cap)
    try:
        yield
    except TestExecutionTimeoutError:
        _WATCHDOG.disarm(phase)
        raise
    except BaseException as exc:
        failure = _WATCHDOG.disarm(phase)
        if failure is not None and phase.overrun is not None:
            raise TestExecutionTimeoutError(failure) from exc
        raise
    failure = _WATCHDOG.disarm(phase)
    if failure is not None:
        raise TestExecutionTimeoutError(failure)


@contextmanager
def _charged(item: pytest.Item, when: str) -> Generator[None, None, None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    try:
        budget = budget_for(item)
    except ValueError:
        if when != "teardown":
            raise
        budget = DEFAULT_TEST_TIMEOUT_SECONDS
    with phase_budget(f"{when} of {item.nodeid}", budget):
        yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    with _charged(item, "setup"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    with _charged(item, "call"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    del nextitem
    with _charged(item, "teardown"):
        return (yield)
