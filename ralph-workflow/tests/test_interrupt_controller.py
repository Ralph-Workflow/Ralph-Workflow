from __future__ import annotations

from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE,
    InterruptController,
)

_PID_ALPHA = 101
_PID_BETA = 202
_EXPECTED_KILL_COUNT = 2


def test_force_interrupt_records_shutdown_and_optional_cleanup() -> None:
    events: list[tuple[str, object]] = []

    def _shutdown_all(grace_period_s: float) -> None:
        events.append(("shutdown", grace_period_s))

    def _record_interrupt() -> None:
        events.append(("record", None))

    def _stop_connectivity() -> None:
        events.append(("stop", None))

    def _kill_process_group(pid: int, sig: int) -> None:
        events.append(("kill", (pid, sig)))

    def _hard_exit(code: int) -> None:
        events.append(("exit", code))

    controller = InterruptController(
        shutdown_all=_shutdown_all,
        record_interrupt=_record_interrupt,
        stop_connectivity=_stop_connectivity,
        kill_process_group=_kill_process_group,
        hard_exit=_hard_exit,
    )

    controller.force_interrupt(bridge_pids=[_PID_ALPHA, _PID_BETA])

    assert events[0] == ("record", None)
    assert ("stop", None) in events
    assert ("shutdown", 0) in events
    kill_events = [event for event in events if event[0] == "kill"]
    assert len(kill_events) == _EXPECTED_KILL_COUNT
    assert ("exit", INTERRUPT_EXIT_CODE) not in events


def test_force_interrupt_suppresses_cleanup_failures() -> None:
    shutdown_calls: list[float] = []

    def _bad_stop() -> None:
        raise RuntimeError("stop failed")

    def _bad_kill(pid: int, sig: int) -> None:
        del sig
        if pid == _PID_BETA:
            raise ProcessLookupError(pid)
        raise PermissionError(pid)

    def _shutdown_all(grace_period_s: float) -> None:
        shutdown_calls.append(grace_period_s)

    controller = InterruptController(
        shutdown_all=_shutdown_all,
        record_interrupt=lambda: None,
        stop_connectivity=_bad_stop,
        kill_process_group=_bad_kill,
        hard_exit=lambda code: None,
    )

    controller.force_interrupt(bridge_pids=[_PID_ALPHA, _PID_BETA])

    assert shutdown_calls == [0]


def test_force_exit_uses_interrupt_exit_code() -> None:
    exit_codes: list[int] = []

    def _shutdown_all(grace_period_s: float) -> None:
        del grace_period_s

    controller = InterruptController(
        shutdown_all=_shutdown_all,
        record_interrupt=lambda: None,
        hard_exit=exit_codes.append,
    )

    controller.force_exit()

    assert exit_codes == [INTERRUPT_EXIT_CODE]
