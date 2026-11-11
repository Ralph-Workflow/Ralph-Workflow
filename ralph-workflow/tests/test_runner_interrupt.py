from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
)
from ralph.pipeline._runner_interrupt import handle_keyboard_interrupt
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.interrupt.signal_handler import SignalHandler


_PID = 101
_QUICK_BUDGET = 0.05
_POLL_INTERVAL = 0.001
_FAKE_DEFAULT_GRACE = 2.5


@dataclass
class FakeProcessManager:
    policy: ProcessManagerPolicy = field(
        default_factory=lambda: ProcessManagerPolicy(default_grace_period_s=_FAKE_DEFAULT_GRACE)
    )
    shutdown_all_calls: list[float] = field(default_factory=list)
    shutdown_all_for_label_calls: list[tuple[str, float]] = field(default_factory=list)
    kill_process_group_calls: list[tuple[int, int]] = field(default_factory=list)
    _active_records: list[ProcessRecord] = field(default_factory=list)

    def add_active(self, pid: int, pgid: int, label: str = "invoke:fake") -> ProcessRecord:
        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=("fake",),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.RUNNING,
            label=label,
        )
        self._active_records.append(record)
        return record

    def drain(self) -> None:
        self._active_records.clear()

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        resolved = grace_period_s if grace_period_s is not None else 0.0
        self.shutdown_all_calls.append(resolved)
        if resolved == 0:
            for record in self._active_records:
                self.kill_process_group_calls.append((record.pgid, signal.SIGKILL))
            self._active_records.clear()

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        self.shutdown_all_for_label_calls.append(
            (label_prefix, grace_period_s if grace_period_s is not None else 0.0)
        )

    def list_active(self) -> list[ProcessRecord]:
        return list(self._active_records)

    def kill_process_group(self, pgid: int, sig: int) -> None:
        self.kill_process_group_calls.append((pgid, sig))

    def register_listener(self, callback: object) -> Callable[[], None]:
        del callback
        return lambda: None


def _build_dispatcher(
    manager: FakeProcessManager,
    *,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptDispatcher:
    return dispatcher_from_process_manager(
        process_manager=manager,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        hard_exit=hard_exit,
    )


def _make_signal_fakes() -> tuple[
    Callable[[int], SignalHandler],
    Callable[[int, SignalHandler], SignalHandler],
    list[tuple[int, SignalHandler]],
    SignalHandler,
]:
    set_calls: list[tuple[int, SignalHandler]] = []

    def _previous_handler(signum: int, frame: object) -> object:
        del signum, frame
        return None

    def _fake_getsignal(signum: int) -> SignalHandler:
        del signum
        return _previous_handler

    def _fake_set(signum: int, handler: SignalHandler) -> SignalHandler:
        set_calls.append((signum, handler))
        return handler

    previous_handler: SignalHandler = _previous_handler
    return (
        _fake_getsignal,
        _fake_set,
        set_calls,
        previous_handler,
    )


class _RecordingDispatcher:
    def __init__(self, real: InterruptDispatcher) -> None:
        self._real = real
        self.begin_calls: list[dict[str, object]] = []
        self.poll_calls: list[tuple[float, ...]] = []
        self.force_exit_calls: list[list[int]] = []

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        self.begin_calls.append({"grace_period_s": grace_period_s, "block": block})
        self._real.begin_interrupt(grace_period_s=grace_period_s, block=block)

    def run_early_escalation_poll(
        self,
        *,
        progress_poll_interval_s: float | None = None,
        max_wait_s: float | None = None,
    ) -> None:
        self.poll_calls.append(())
        self._real.run_early_escalation_poll(
            progress_poll_interval_s=progress_poll_interval_s,
            max_wait_s=max_wait_s,
        )

    def force_exit(self, *, bridge_pgids: object = ()) -> None:
        pgids_list = list(bridge_pgids) if bridge_pgids else []
        self.force_exit_calls.append(pgids_list)
        self._real.force_exit(bridge_pgids=bridge_pgids)


class _NotAnException(BaseException):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _DrainOnBeginDispatcher(_RecordingDispatcher):
    def __init__(
        self,
        real: InterruptDispatcher,
        manager: FakeProcessManager,
        pids_pgids: list[tuple[int, int]],
    ) -> None:
        super().__init__(real)
        self._manager = manager
        self._pids_pgids = pids_pgids
        self._drained = False

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        if not self._drained:
            self._drained = True
            self._manager.drain()
        super().begin_interrupt(grace_period_s=grace_period_s, block=block)
        for pid, pgid in self._pids_pgids:
            self._manager.add_active(pid=pid, pgid=pgid, label="invoke:fake")


def test_handle_keyboard_interrupt_uses_injected_poll_interval_for_polling() -> None:
    manager = FakeProcessManager()
    dispatcher = _build_dispatcher(manager)
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=dispatcher,
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    assert len(set_calls) == 4
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[3][0] == signal.SIGTERM
    assert set_calls[2][1] is previous_handler


def test_handle_keyboard_interrupt_recovers_from_dispatcher_exception() -> None:
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    class _BoomDispatcher:
        def begin_interrupt(
            self,
            grace_period_s: float | None = None,
            *,
            block: bool = False,
        ) -> None:
            del grace_period_s, block
            raise RuntimeError("boom")

    sink: list[object] = []

    def _log_sink(message: object) -> None:
        sink.append(message)

    handler_id = logger.add(_log_sink)
    try:
        handle_keyboard_interrupt(
            dispatcher=_BoomDispatcher(),
            signal_getter=getter,
            signal_setter=setter,
        )
    finally:
        logger.remove(handler_id)
    assert len(set_calls) == 4
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[3][0] == signal.SIGTERM
    assert set_calls[2][1] is previous_handler
    assert any("Interrupt controller raised" in str(record) for record in sink), sink


def test_handle_keyboard_interrupt_rejects_monitor_stop_with_pre_built_dispatcher() -> None:
    manager = FakeProcessManager()
    dispatcher = _build_dispatcher(manager)
    getter, setter, _set_calls, _previous_handler = _make_signal_fakes()
    with pytest.raises(RuntimeError, match="monitor_stop") as exc_info:
        handle_keyboard_interrupt(
            dispatcher=dispatcher,
            monitor_stop=lambda: None,
            signal_getter=getter,
            signal_setter=setter,
        )
    msg = str(exc_info.value)
    assert "monitor_stop" in msg
    assert "dispatcher" in msg


def test_handle_keyboard_interrupt_runs_early_escalation_poll_after_begin_interrupt() -> None:
    manager = FakeProcessManager()
    real_dispatcher = _build_dispatcher(manager)
    recorder = _RecordingDispatcher(real_dispatcher)
    getter, setter, _set_calls, _previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=recorder,
        process_manager=manager,
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    assert manager.shutdown_all_for_label_calls == [
        ("invoke:", manager.policy.default_grace_period_s)
    ], manager.shutdown_all_for_label_calls
    assert len(recorder.begin_calls) == 1, recorder.begin_calls
    assert recorder.begin_calls[0]["block"] is True, recorder.begin_calls
    assert recorder.poll_calls == [], recorder.poll_calls


def test_handle_keyboard_interrupt_second_sigint_force_exit_uses_active_pgids() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=101, pgid=9101, label="invoke:fake")
    manager.add_active(pid=202, pgid=9202, label="invoke:fake")
    exit_calls: list[tuple[int, ...]] = []
    real_dispatcher = _build_dispatcher(
        manager,
        hard_exit=lambda code: exit_calls.append((code,)),
    )
    recorder = _DrainOnBeginDispatcher(
        real_dispatcher,
        manager,
        [(101, 9101), (202, 9202)],
    )
    getter, setter, set_calls, _previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=recorder,
        process_manager=manager,
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    set_calls[0][1](signal.SIGINT, None)
    assert sorted(recorder.force_exit_calls[-1]) == [9101, 9202], (
        f"expected bridge_pgids from list_active() in any order; got {recorder.force_exit_calls}"
    )
    assert exit_calls == [(130,)], exit_calls


def test_handle_keyboard_interrupt_propagates_baseexception_from_dispatcher() -> None:
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    class _BoomNotException:
        def begin_interrupt(
            self,
            grace_period_s: float | None = None,
            *,
            block: bool = False,
        ) -> None:
            del grace_period_s, block
            raise _NotAnException("boom")

    captured: list[threading.ExceptHookArgs] = []
    excepthook_called = threading.Event()
    original_excepthook = threading.excepthook

    def _capture_excepthook(args: threading.ExceptHookArgs) -> None:
        captured.append(args)
        excepthook_called.set()

    sink: list[object] = []

    def _log_sink(message: object) -> None:
        sink.append(message)

    handler_id = logger.add(_log_sink)
    threading.excepthook = _capture_excepthook
    try:
        handle_keyboard_interrupt(
            dispatcher=_BoomNotException(),
            signal_getter=getter,
            signal_setter=setter,
        )
        excepthook_called.wait(timeout=2.0)
    finally:
        threading.excepthook = original_excepthook
        logger.remove(handler_id)
    assert len(set_calls) == 4, set_calls
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[2][1] is previous_handler, set_calls
    assert set_calls[3][0] == signal.SIGTERM
    assert captured, (
        "Expected the background _begin_interrupt thread to raise "
        "_NotAnException; threading.excepthook captured nothing. "
        "The OLD 'except BaseException' code would have silently "
        "caught it and the excepthook would never fire."
    )
    assert any(isinstance(args.exc_value, _NotAnException) for args in captured), captured
    assert not any("Interrupt controller raised" in str(record) for record in sink), sink


class _SlowBeginDispatcher:
    def __init__(self, real: InterruptDispatcher, begin_done: threading.Event) -> None:
        self._real = real
        self._begin_done = begin_done

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        self._begin_done.wait(timeout=5.0)
        self._real.begin_interrupt(grace_period_s=grace_period_s, block=block)

    def run_early_escalation_poll(self, *args: object, **kwargs: object) -> None:
        self._real.run_early_escalation_poll(*args, **kwargs)

    def force_exit(self, *, bridge_pgids: object = ()) -> None:
        self._real.force_exit(bridge_pgids=bridge_pgids)


def test_second_sigint_during_first_sigint_interrupt_thread() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=101, pgid=9999, label="invoke:fake")
    exit_calls: list[tuple[int, ...]] = []
    real_dispatcher = _build_dispatcher(
        manager,
        hard_exit=lambda code: exit_calls.append((code,)),
    )
    begin_done = threading.Event()
    slow_dispatcher = _SlowBeginDispatcher(real_dispatcher, begin_done)
    set_calls: list[tuple[int, object]] = []

    def _previous_handler(_signum: int, _frame: object) -> None:
        return None

    previous_handler: object = _previous_handler
    handler_installed = threading.Event()

    def _getter(_signum: int) -> object:
        return previous_handler

    def _setter(signum: int, handler: object) -> object:
        set_calls.append((signum, handler))
        if signum == signal.SIGINT and len(set_calls) == 1:
            handler_installed.set()
        return handler

    def _entry_point() -> None:
        handle_keyboard_interrupt(
            dispatcher=slow_dispatcher,
            process_manager=manager,
            signal_getter=_getter,
            signal_setter=_setter,
            poll_interval_s=_POLL_INTERVAL,
        )

    thread = threading.Thread(target=_entry_point, daemon=True)
    thread.start()
    try:
        assert handler_installed.wait(timeout=1.0), (
            "force-kill handler was not installed within 1.0s; set_calls did not grow to len==1"
        )
        set_calls[0][1](signal.SIGINT, None)
        assert exit_calls == [(130,)], (
            f"second SIGINT should call hard_exit(130) exactly once; got {exit_calls}"
        )
        assert getattr(real_dispatcher, "_force_exit_called", None) is True, (
            "real dispatcher's _force_exit_called guard must be set "
            "synchronously by the second-SIGINT handler"
        )
    finally:
        begin_done.set()
        thread.join(timeout=2.0)
    assert not thread.is_alive(), (
        "entry-point thread must complete within 2.0s of releasing "
        "_begin_done; the interrupt thread's eventual completion "
        "must NOT hang the main thread"
    )
    assert exit_calls == [(130,)], (
        f"interrupt thread's eventual completion must NOT call "
        f"hard_exit a second time; got {exit_calls}"
    )
    real_dispatcher.force_exit(bridge_pgids=[9999])
    assert exit_calls == [(130,)], (
        f"_force_exit_called guard must prevent a second hard_exit call; got {exit_calls}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
