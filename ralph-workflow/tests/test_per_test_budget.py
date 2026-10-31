"""The per-test budget charges each phase's own work and cannot be disarmed.

``tests._per_test_budget`` charges every test phase the CPU its main
thread spends, plus a wall-clock hang cap. These tests run the real
mechanism -- watchdog thread, ``SIGPROF``, ``time.thread_time`` -- with
budgets of a few hundredths of a second, so each overrun costs at most
that much CPU. The nested phases they arm sit on top of the phase the
suite itself armed for the test, which resumes when they end.
"""

from __future__ import annotations

import math
import re
import signal
import textwrap
import threading
import time

import pytest

import tests._per_test_budget as budget_module

pytest_plugins = ("pytester",)


def _burn_main_thread_cpu(seconds: float) -> None:
    start = time.thread_time()
    while time.thread_time() - start < seconds:
        pass


def test_a_thread_leaked_by_an_earlier_test_is_not_charged_to_the_next_one() -> None:
    stop = threading.Event()

    def spin() -> None:
        start = time.thread_time()
        while not stop.is_set() and time.thread_time() - start < 0.5:
            pass

    leaked = threading.Thread(target=spin, daemon=True)
    leaked.start()
    try:
        # The phase itself only waits; the leaked thread burns CPU meanwhile.
        with budget_module.phase_budget("idle waiter", 0.1):
            threading.Event().wait(0.2)
    finally:
        stop.set()
        leaked.join(timeout=2.0)


#: Tests of the CPU path raise the wall hang cap far above the 5x default, so a
#: loaded host -- where the main thread gets well under a fifth of a core --
#: cannot trip the hang cap before the 0.05 CPU-second budget.
_CPU_PATH_WALL_CAP = 30.0


def test_main_thread_cpu_over_budget_raises() -> None:
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="used more than 0.05 CPU"),
        budget_module.phase_budget("busy", 0.05, wall_cap=_CPU_PATH_WALL_CAP),
    ):
        _burn_main_thread_cpu(1.0)


def test_a_phase_that_only_waits_hits_the_wall_hang_cap() -> None:
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="wall-clock hang cap"),
        budget_module.phase_budget("hang", 0.02),
    ):
        threading.Event().wait(2.0)


def test_arming_and_cancelling_an_own_alarm_does_not_disarm_the_hang_cap() -> None:
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="wall-clock hang cap"),
        budget_module.phase_budget("own alarm", 0.02),
    ):
        previous = signal.signal(signal.SIGALRM, lambda _signum, _frame: None)
        signal.setitimer(signal.ITIMER_REAL, 0.01)
        threading.Event().wait(0.02)
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        threading.Event().wait(2.0)


def test_an_overrun_swallowed_by_code_under_test_still_fails_the_phase() -> None:
    swallowed: list[str] = []
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="used more than 0.05 CPU"),
        budget_module.phase_budget("swallower", 0.05, wall_cap=_CPU_PATH_WALL_CAP),
    ):
        try:
            _burn_main_thread_cpu(1.0)
        except BaseException as exc:  # the production anti-pattern being guarded
            swallowed.append(type(exc).__name__)
    assert swallowed == ["TestExecutionTimeoutError"]


def test_replacing_the_budget_signal_handler_fails_the_phase_and_is_repaired() -> None:
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="replaced the per-test"),
        budget_module.phase_budget("tamperer", 1.0),
    ):
        signal.signal(signal.SIGPROF, signal.SIG_IGN)
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="used more than 0.05 CPU"),
        budget_module.phase_budget("after repair", 0.05, wall_cap=_CPU_PATH_WALL_CAP),
    ):
        _burn_main_thread_cpu(1.0)


def test_leaving_the_budget_signal_blocked_fails_the_phase_and_is_repaired() -> None:
    with (
        pytest.raises(budget_module.TestExecutionTimeoutError, match="left SIGPROF blocked"),
        budget_module.phase_budget("blocker", 1.0),
    ):
        signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGPROF,))
    assert signal.SIGPROF not in signal.pthread_sigmask(signal.SIG_BLOCK, ())


@pytest.mark.parametrize("value", [0, 0.0, -1, math.nan, math.inf, True, "1"])
def test_budgets_that_would_disarm_the_timers_are_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        budget_module.validated_budget(value, source="timeout_seconds")


def test_positive_budgets_are_accepted_and_get_a_five_fold_hang_cap() -> None:
    assert budget_module.validated_budget(2, source="marker") == 2.0
    assert budget_module.wall_hang_cap(1.0) == 5.0
    assert budget_module.wall_hang_cap(30.0) == 90.0


def test_fixture_setup_and_teardown_are_each_charged_and_a_zero_marker_errors(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_phases=textwrap.dedent(
            """
            import time

            import pytest


            def _burn(seconds):
                start = time.thread_time()
                while time.thread_time() - start < seconds:
                    pass


            @pytest.fixture
            def heavy_setup():
                _burn(1.0)
                yield


            @pytest.fixture
            def heavy_teardown():
                yield
                _burn(1.0)


            @pytest.mark.timeout_seconds(0.05)
            def test_setup_overrun(heavy_setup):
                pass


            @pytest.mark.timeout_seconds(0.05)
            def test_teardown_overrun(heavy_teardown):
                pass


            @pytest.mark.timeout_seconds(0)
            def test_zero_budget():
                pass
            """
        )
    )

    result = pytester.runpytest_inprocess("-p", "tests._per_test_budget", "-p", "no:randomly")

    result.assert_outcomes(passed=1, errors=3)
    output = result.stdout.str()
    # Each phase is charged on its own budget. Which cap trips -- the CPU budget
    # or its 5x wall hang cap -- depends on how much CPU a loaded host gives the
    # main thread; either one, naming the right phase, proves the charging.
    for phase in (
        "setup of test_phases.py::test_setup_overrun",
        "teardown of test_phases.py::test_teardown_overrun",
    ):
        assert re.search(
            re.escape(phase) + r" (used more than 0\.05 CPU|exceeded the .* wall-clock hang cap)",
            output,
        ), output
    assert "must be a positive, finite number of seconds (got 0)" in output
