"""Per-test budget: main-thread CPU seconds plus a wall-clock hang cap.

Registered by ``tests/conftest.py`` (``pytest_plugins``). Every phase of
every test -- setup, call and teardown, each with its own budget -- is
charged:

* **CPU** -- the CPU seconds the *main thread* spends in that phase, which
  is the work the test controls. A process-wide counter (``ITIMER_PROF``)
  also charged threads that an earlier test leaked and that were still
  spinning, so an idle test failed for its predecessor's work.
* **Wall clock** -- a hang cap of :func:`wall_hang_cap` (5x the budget, at
  most 60 s above it). A test that waits rather than works (a sleep, a
  blocked read, a hang) uses no CPU, and work it hands to threads it starts
  is bounded here too.

The budget is the ``timeout_seconds`` marker, else
``RALPH_PYTEST_TEST_TIMEOUT_SECONDS``, else 1 s. Zero, negative and
non-finite values are rejected: they would disarm the budget.

Mechanics: a POSIX process cannot read another thread's CPU clock on
macOS, so the main thread measures itself. A watchdog thread sends it
``SIGPROF`` (``signal.pthread_kill``) only when the budget could first
have run out -- never earlier than ``budget - CPU used so far`` seconds of
wall clock, because one thread cannot spend CPU faster than real time --
and at the wall deadline. The handler reads ``time.thread_time()`` and
raises the uncatchable :class:`TestExecutionTimeoutError`; a test that
finishes inside its budget is never signalled. An overrun that code under
test swallows is re-raised every :data:`_RETRY_SECONDS` and again when the
phase ends, and the phase's final CPU and wall clock are checked even if
no signal landed.

The handler stays installed for the whole session, so a signal that lands
after its phase finished finds no armed phase and returns instead of
reaching ``SIG_DFL`` (``SIGPROF``'s default action kills the worker -- a
late ``ITIMER_PROF`` delivery once took down a whole shard). A test that
replaces that handler or blocks ``SIGPROF`` and leaves it that way has
disarmed the budget; the phase fails and the handler is reinstalled.
"""

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
    """A test phase ran out of its per-test budget.

    A ``BaseException``, like ``KeyboardInterrupt``, so code under test
    cannot swallow it. As a ``TimeoutError`` it was an ``OSError``: an
    ``except OSError: continue`` around a file read turned an overrun into
    a silently skipped file (a fresh audit scan then reported phantom
    baseline drift), and ``except Exception`` blocks hid overruns entirely.
    """


#: The wall-clock HANG cap, as a multiple of the per-test budget (and at
#: most ``_WALL_HANG_MAX_EXTRA_SECONDS`` above it).
_WALL_HANG_FACTOR = 5.0
_WALL_HANG_MAX_EXTRA_SECONDS = 60.0
#: Shortest gap between two checks, so a phase near its budget is not
#: signalled in a tight loop.
_MIN_CHECK_SECONDS = 0.01
#: Gap between re-raises of an overrun the handler could not deliver
#: (swallowed, or the main thread was not running Python code).
_RETRY_SECONDS = 0.25
#: How long the watchdog sleeps between phases before re-checking; ``arm``
#: wakes it at once, so this only bounds a wait that nothing ends.
_IDLE_WAKE_SECONDS = 60.0
_SIGNAL = signal.SIGPROF
# Bound now: tests monkeypatch ``signal``/``time`` attributes during setup,
# and the budget must keep reading the real ones.
_install_handler = signal.signal
_current_handler = signal.getsignal
_send_signal = signal.pthread_kill
_signal_mask = signal.pthread_sigmask


def wall_hang_cap(budget_seconds: float) -> float:
    """Return the wall-clock hang cap for a phase with ``budget_seconds`` of CPU."""
    return min(budget_seconds * _WALL_HANG_FACTOR, budget_seconds + _WALL_HANG_MAX_EXTRA_SECONDS)


def validated_budget(value: object, *, source: str) -> float:
    """Return ``value`` as a budget in seconds, or raise if it would disarm the budget.

    Raises:
        ValueError: ``value`` is not a positive, finite number.
    """
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
    """Return the per-phase budget for ``item``: its marker, else the environment, else 1 s."""
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
        """Measure the phase from the main thread; return the overrun, if any."""
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
    """Signals the main thread when its armed phase may be over budget."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._phases: list[_Phase] = []  # bounded-accumulator-ok: one entry per nested phase
        self._thread: threading.Thread | None = None
        self._main_ident = threading.main_thread().ident
        # One bound-method object, so ``getsignal`` can be compared by identity.
        self._handler = self._on_signal
        # When the watchdog thread will next wake on its own. Phases are armed
        # and disarmed several times per test; waking the thread for each one
        # makes it contend for the GIL and stalls the main thread (measured
        # ~13 ms per test), so it is only nudged when a new phase's first check
        # is due before this.
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
        """End ``phase``; return why it failed (overrun or tampering), if it did."""
        with self._condition:
            phase.finished = True
            self._phases.remove(phase)
            # No wake-up: the thread re-reads the phase list when its own
            # wait ends, and an early wake would only cost the main thread.
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
                # Signal under the lock: ``disarm`` cannot finish the phase
                # between this check and the send.
                phase.next_check = _monotonic() + _RETRY_SECONDS
                if self._main_ident is not None:
                    _send_signal(self._main_ident, _SIGNAL)


_WATCHDOG = _Watchdog()


@contextmanager
def phase_budget(
    label: str, budget: float, *, wall_cap: float | None = None
) -> Generator[None, None, None]:
    """Charge the enclosed block, run on the main thread, against ``budget``.

    ``wall_cap`` overrides the :func:`wall_hang_cap` default; tests of the CPU
    path raise it so a loaded host (where the main thread gets well under a
    fifth of a core) cannot trip the hang cap first.

    Raises:
        TestExecutionTimeoutError: the block used more than ``budget`` CPU
            seconds of the main thread, exceeded :func:`wall_hang_cap`, or
            disarmed the budget by tampering with ``SIGPROF``.
    """
    phase = _WATCHDOG.arm(label, budget, wall_cap)
    try:
        yield
    except TestExecutionTimeoutError:
        _WATCHDOG.disarm(phase)
        raise
    except BaseException as exc:
        # An overrun that code under test swallowed outranks whatever the
        # block raised afterwards; the original stays attached as the cause.
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
        # Setup already failed on this budget; still run fixture teardown,
        # under the default budget, so the next test starts clean.
        budget = DEFAULT_TEST_TIMEOUT_SECONDS
    with phase_budget(f"{when} of {item.nodeid}", budget):
        yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    """Charge fixture setup against its own per-test budget."""
    with _charged(item, "setup"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Charge the test body against its own per-test budget."""
    with _charged(item, "call"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Charge fixture teardown against its own per-test budget."""
    del nextitem
    with _charged(item, "teardown"):
        return (yield)
