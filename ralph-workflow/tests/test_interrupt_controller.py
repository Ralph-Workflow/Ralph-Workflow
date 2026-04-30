from __future__ import annotations

from ralph.interrupt.controller import InterruptController, install_force_kill_handler

INTERRUPT_EXIT_CODE = 130
_DEAD_PID = 202
_FORCE_KILL_PID = 101


def test_begin_interrupt_records_graceful_shutdown_and_optional_cleanup() -> None:
    events: list[tuple[str, object]] = []

    controller = InterruptController(
        shutdown_all=lambda grace_period_s: events.append(("shutdown", grace_period_s)),
        record_interrupt=lambda: events.append(("record", None)),
        stop_connectivity=lambda: events.append(("stop", None)),
        kill_process_group=lambda pid, sig: events.append(("kill", (pid, sig))),
        hard_exit=lambda code: events.append(("exit", code)),
    )

    controller.begin_interrupt(grace_period_s=2.5)

    assert events == [("record", None), ("stop", None), ("shutdown", 2.5)]


def test_force_interrupt_suppresses_cleanup_failures() -> None:
    shutdown_calls: list[int] = []

    def bad_stop() -> None:
        raise RuntimeError("stop failed")

    def bad_kill(pid: int, sig: int) -> None:
        del sig
        if pid == _DEAD_PID:
            raise ProcessLookupError(pid)
        raise PermissionError(pid)

    controller = InterruptController(
        shutdown_all=shutdown_calls.append,
        record_interrupt=lambda: None,
        stop_connectivity=bad_stop,
        kill_process_group=bad_kill,
        hard_exit=lambda code: None,
    )

    controller.force_interrupt(bridge_pids=[101, 202])

    assert shutdown_calls == [0]


def test_force_exit_uses_interrupt_exit_code_and_force_kill() -> None:
    events: list[tuple[str, object]] = []

    controller = InterruptController(
        shutdown_all=lambda grace_period_s: events.append(("shutdown", grace_period_s)),
        record_interrupt=lambda: events.append(("record", None)),
        kill_process_group=lambda pid, sig: events.append(("kill", (pid, sig))),
        hard_exit=lambda code: events.append(("exit", code)),
    )

    controller.force_exit(bridge_pids=[_FORCE_KILL_PID])

    assert ("shutdown", 0) in events
    assert any(event[0] == "kill" and event[1][0] == _FORCE_KILL_PID for event in events)
    assert events[-1] == ("exit", INTERRUPT_EXIT_CODE)


def test_install_force_kill_handler_restores_previous_handler() -> None:
    calls: list[tuple[str, object]] = []
    previous = object()

    def fake_getsignal(signum: int) -> object:
        calls.append(("get", signum))
        return previous

    def fake_signal(signum: int, handler: object) -> object:
        calls.append(("set", (signum, handler)))
        return handler

    restore = install_force_kill_handler(
        lambda: calls.append(("force", None)),
        signal_getter=fake_getsignal,
        signal_setter=fake_signal,
    )
    handler = calls[1][1][1]
    handler(2, None)
    restore()

    assert calls[0] == ("get", 2)
    assert calls[2] == ("force", None)
    assert calls[3] == ("set", (2, previous))
