from __future__ import annotations

from ralph.interrupt.controller import InterruptController


INTERRUPT_EXIT_CODE = 130


def test_begin_interrupt_records_shutdown_and_optional_cleanup() -> None:
    events: list[tuple[str, object]] = []

    controller = InterruptController(
        shutdown_all=lambda grace_period_s: events.append(("shutdown", grace_period_s)),
        record_interrupt=lambda: events.append(("record", None)),
        stop_connectivity=lambda: events.append(("stop", None)),
        kill_process_group=lambda pid, sig: events.append(("kill", (pid, sig))),
        hard_exit=lambda code: events.append(("exit", code)),
    )

    controller.begin_interrupt(bridge_pids=[101, 202])

    assert events[0] == ("record", None)
    assert ("stop", None) in events
    assert ("shutdown", 0) in events
    kill_events = [event for event in events if event[0] == "kill"]
    assert len(kill_events) == 2
    assert ("exit", INTERRUPT_EXIT_CODE) not in events


def test_begin_interrupt_suppresses_cleanup_failures() -> None:
    shutdown_calls: list[int] = []

    def bad_stop() -> None:
        raise RuntimeError("stop failed")

    def bad_kill(pid: int, sig: int) -> None:
        del sig
        if pid == 202:
            raise ProcessLookupError(pid)
        raise PermissionError(pid)

    controller = InterruptController(
        shutdown_all=lambda grace_period_s: shutdown_calls.append(grace_period_s),
        record_interrupt=lambda: None,
        stop_connectivity=bad_stop,
        kill_process_group=bad_kill,
        hard_exit=lambda code: None,
    )

    controller.begin_interrupt(bridge_pids=[101, 202])

    assert shutdown_calls == [0]


def test_force_exit_uses_interrupt_exit_code() -> None:
    exit_codes: list[int] = []

    controller = InterruptController(
        shutdown_all=lambda grace_period_s: None,
        record_interrupt=lambda: None,
        hard_exit=exit_codes.append,
    )

    controller.force_exit()

    assert exit_codes == [INTERRUPT_EXIT_CODE]
