from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

import ralph.cli.commands.run as ralph_cli_run_module
import ralph.interrupt.dispatcher as dispatcher_mod
from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE,
    InterruptController,
    controller_from_process_manager,
)
from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
    run_shutdown_block,
)
from ralph.pipeline._runner_interrupt import handle_keyboard_interrupt
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable


_PID = 101
_PGID = 9999
_INVOKE_GRACE = 0.1
_QUICK_BUDGET = 0.05
_POLL_INTERVAL = 0.01
_SIGINT = signal.SIGINT
_SIGTERM = signal.SIGTERM
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


def _build_dispatcher(**overrides: object) -> tuple[FakeProcessManager, InterruptDispatcher]:
    manager = FakeProcessManager()
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []
    record_calls: list[None] = []

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_interrupt() -> None:
        record_calls.append(None)

    kwargs: dict[str, object] = {
        "process_manager": manager,
        "hard_exit": _record_exit,
        "kill_process_group": _record_kill,
        "record_interrupt": _record_interrupt,
    }
    kwargs.update(overrides)
    dispatcher = dispatcher_from_process_manager(**kwargs)
    return manager, dispatcher


def test_dispatcher_begin_interrupt_calls_label_targeted_shutdown() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    _, dispatcher = _build_dispatcher(process_manager=manager)
    dispatcher.begin_interrupt(grace_period_s=_INVOKE_GRACE)

    assert manager.shutdown_all_for_label_calls == [("invoke:", _INVOKE_GRACE)]
    assert manager.shutdown_all_calls == []


def test_dispatcher_force_exit_calls_shutdown_zero_and_hard_exit_with_pgid() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        kill_process_group=lambda pgid, sig: kill_calls.append((pgid, sig)),
        hard_exit=lambda code: exit_calls.append((code,)),
    )

    dispatcher.force_exit(bridge_pgids=[_PGID])

    assert manager.shutdown_all_calls == [0]
    assert manager.kill_process_group_calls == [(_PGID, signal.SIGKILL)]
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_dispatcher_factory_wires_process_manager() -> None:
    manager = FakeProcessManager()
    exit_calls: list[tuple[int, ...]] = []
    dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
    )[1]
    assert dispatcher.controller.shutdown_all is not None
    assert dispatcher.controller.shutdown_all_for_label is not None
    dispatcher.hard_exit(INTERRUPT_EXIT_CODE)
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_dispatcher_force_exit_is_idempotent_on_repeat_bridge_pids() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    _, dispatcher = _build_dispatcher(process_manager=manager)
    dispatcher.force_exit(bridge_pgids=[_PID])
    dispatcher.force_exit(bridge_pgids=[_PID])
    assert manager.shutdown_all_calls == [0]


def test_controller_force_exit_has_no_idempotency_guarantee() -> None:
    exit_calls: list[tuple[int, ...]] = []
    controller = InterruptController(
        shutdown_all=lambda grace_period_s: None,
        record_interrupt=lambda: None,
        hard_exit=lambda code: exit_calls.append((code,)),
    )
    controller.force_exit(bridge_pgids=[_PID])
    controller.force_exit(bridge_pgids=[_PID])
    assert exit_calls == [(INTERRUPT_EXIT_CODE,), (INTERRUPT_EXIT_CODE,)]


def test_dispatcher_uses_process_manager_default_grace_period_when_none() -> None:
    manager = FakeProcessManager()
    manager.policy = ProcessManagerPolicy(default_grace_period_s=2.5)
    manager.shutdown_all_for_label_calls = []
    manager._active_records.clear()

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=lambda _c: None,
    )
    dispatcher.begin_interrupt(grace_period_s=None)

    assert manager.shutdown_all_for_label_calls == [("invoke:", 2.5)]


def test_dispatcher_force_exit_calls_record_interrupt_exactly_once() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    record_calls: list[None] = []
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        record_interrupt=lambda: record_calls.append(None),
    )
    dispatcher.force_exit(bridge_pgids=[_PID])
    assert record_calls == [None]


def test_interrupt_dispatcher_constructor_rejects_invalid_budget() -> None:
    manager = FakeProcessManager()
    with pytest.raises(RuntimeError):
        InterruptDispatcher(
            controller=InterruptController(shutdown_all=lambda _g: None),
            process_manager=manager,
            hard_exit=lambda _c: None,
            poll_interval_s=0.01,
            hard_kill_budget_s=0,
        )


def test_dispatcher_factory_uses_explicit_process_manager_not_singleton() -> None:
    manager = FakeProcessManager()
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=lambda _c: None,
    )
    assert dispatcher.process_manager is manager


def _patch_psutil(monkeypatch: pytest.MonkeyPatch, cpu_factory: Callable[[int], float]) -> None:

    class _FakeCpu:
        def __init__(self, user: float) -> None:
            self.user = user
            self.system = 0.0

    class _FakeProc:
        def __init__(self) -> None:
            self._n = 0

        def cpu_times(self) -> _FakeCpu:
            self._n += 1
            return _FakeCpu(cpu_factory(self._n))

    class _FakePsutil:
        @staticmethod
        def get_process(_pid: int) -> _FakeProc:
            return _FakeProc()

    _FakePsutil.Process = staticmethod(_FakePsutil.get_process)

    real_import = dispatcher_mod.importlib.import_module

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            return _FakePsutil
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.importlib, "import_module", _fake_import)


def _patch_psutil_shared(
    monkeypatch: pytest.MonkeyPatch, cpu_factory: Callable[[int], float]
) -> None:

    class _FakeCpu:
        def __init__(self, user: float) -> None:
            self.user = user
            self.system = 0.0

    class _FakeProc:
        def __init__(self) -> None:
            self._n = 0

        def cpu_times(self) -> _FakeCpu:
            self._n += 1
            return _FakeCpu(cpu_factory(self._n))

    instances: dict[int, _FakeProc] = {}

    class _FakePsutil:
        @staticmethod
        def get_process(pid: int) -> _FakeProc:
            if pid not in instances:
                instances[pid] = _FakeProc()
            return instances[pid]

    _FakePsutil.Process = staticmethod(_FakePsutil.get_process)

    real_import = dispatcher_mod.importlib.import_module

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            return _FakePsutil
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.importlib, "import_module", _fake_import)


def _patch_pid_alive(monkeypatch: pytest.MonkeyPatch, *, alive: bool) -> None:

    def _fake_kill(_pid: int, _sig: int) -> None:
        if not alive:
            raise ProcessLookupError(_pid)

    monkeypatch.setattr(dispatcher_mod.os, "kill", _fake_kill)


def test_early_escalation_poll_kills_when_no_cpu_progress_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []
    _patch_psutil(monkeypatch, lambda _call_n: 1.0)
    _patch_pid_alive(monkeypatch, alive=True)

    clock_state: dict[str, float] = {"now": 0.0}

    def _fake_clock() -> float:
        return clock_state["now"]

    def _fake_sleep(_seconds: float) -> None:
        clock_state["now"] += _seconds

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=lambda _c: None,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=_fake_clock,
        sleep=_fake_sleep,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == [(_PGID, signal.SIGKILL)]


def test_early_escalation_poll_does_not_kill_when_cpu_progresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []
    _patch_psutil_shared(monkeypatch, float)
    _patch_pid_alive(monkeypatch, alive=True)
    clock_state: dict[str, float] = {"now": 0.0}

    def _fake_clock() -> float:
        return clock_state["now"]

    def _fake_sleep(seconds: float) -> None:
        clock_state["now"] += seconds

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=lambda _c: None,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=_fake_clock,
        sleep=_fake_sleep,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == []


def test_early_escalation_poll_exits_when_process_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []

    def _kill_returns_dead(_pid: int, _sig: int) -> None:
        raise ProcessLookupError(_pid)

    monkeypatch.setattr(dispatcher_mod.os, "kill", _kill_returns_dead)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=lambda _c: None,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == []


def test_dispatcher_begin_interrupt_block_true_blocks_until_list_active_empty() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    _, dispatcher = _build_dispatcher(process_manager=manager)
    drained = threading.Event()
    begin_called = threading.Event()

    def _drain_after_begin() -> None:
        begin_called.wait(timeout=0.5)
        manager.drain()
        drained.set()

    drainer = threading.Thread(target=_drain_after_begin, daemon=True)
    drainer.start()
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    begin_called.set()
    assert drained.wait(timeout=0.5)
    drainer.join(timeout=0.1)
    assert manager.shutdown_all_for_label_calls == [("invoke:", _INVOKE_GRACE)]


class _FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


def _make_fake_sleep(clock: _FakeClock) -> Callable[[float], None]:

    def _fake_sleep(s: float) -> None:
        clock.advance(s)

    return _fake_sleep


def test_dispatcher_uses_injected_clock_for_block_wait_deadline() -> None:
    clock = _FakeClock()
    sleep_calls: list[float] = []
    active_records: list[ProcessRecord] = []
    record = ProcessRecord(
        pid=_PID,
        pgid=_PGID,
        command=("fake",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
        label="invoke:fake",
    )
    active_records.append(record)

    def _fake_clock() -> float:
        return clock.now()

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        if active_records:
            active_records.clear()

    manager = FakeProcessManager()
    manager._active_records = active_records
    exit_calls: list[tuple[int, ...]] = []
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
        clock=_fake_clock,
        sleep=_fake_sleep,
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    assert len(sleep_calls) >= 1
    import_dispatcher_poll = 0.2
    expected_bound = min(import_dispatcher_poll, _INVOKE_GRACE)
    assert sleep_calls[0] <= expected_bound
    assert sleep_calls[0] <= _INVOKE_GRACE


def test_dispatcher_block_wait_sleep_never_exceeds_remaining_deadline() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
        poll_interval_s=0.2,
        clock=clock.now,
        sleep=_fake_sleep,
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=1.0)

    assert sleep_calls, "block-wait must call sleep at least once"
    for s in sleep_calls:
        assert s <= 0.2, f"sleep {s} exceeds poll_interval_s 0.2"
        assert s <= 1.0, f"sleep {s} exceeds grace_period_s 1.0"
    assert 4 <= len(sleep_calls) <= 6, (
        f"sleep count {len(sleep_calls)} out of expected range [4, 6]; sleep_calls={sleep_calls}"
    )


def test_dispatcher_block_wait_does_not_sleep_when_already_empty() -> None:
    clock = _FakeClock()
    sleep_calls: list[float] = []

    manager = FakeProcessManager()
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=lambda _c: None,
        clock=clock.now,
        sleep=sleep_calls.append,
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    assert sleep_calls == []


def test_dispatcher_early_escalation_uses_injected_clock_for_deadline() -> None:
    clock = _FakeClock()
    sleep_calls: list[float] = []

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=lambda _c: None,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=clock.now,
        sleep=_fake_sleep,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert sleep_calls, "sleep must be called by run_early_escalation_poll"
    assert sleep_calls[0] == _POLL_INTERVAL


def test_dispatcher_constructor_rejects_clock_returning_non_float() -> None:
    manager = FakeProcessManager()
    with pytest.raises(RuntimeError):
        InterruptDispatcher(
            controller=InterruptController(shutdown_all=lambda _g: None),
            process_manager=manager,
            hard_exit=lambda _c: None,
            poll_interval_s=0.01,
            hard_kill_budget_s=0.05,
            clock=lambda: "1.0",
            sleep=lambda _s: None,
        )


def test_dispatcher_factory_defaults_to_time_monotonic() -> None:
    dispatcher = dispatcher_from_process_manager(
        hard_exit=lambda _c: None,
    )
    assert dispatcher.clock is time.monotonic
    assert dispatcher.sleep is time.sleep


def test_dispatcher_early_escalation_poll_sleeps_before_matched_check() -> None:
    clock = _FakeClock()
    event_order: list[str] = []

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    original_list_active = manager.list_active

    def _recording_list_active() -> list[ProcessRecord]:
        event_order.append("list_active")
        return original_list_active()

    manager.list_active = _recording_list_active

    def _fake_sleep(s: float) -> None:
        event_order.append(f"sleep({s})")

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=lambda _c: None,
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=clock.now,
        sleep=_fake_sleep,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    sleep_indices = [i for i, e in enumerate(event_order) if e.startswith("sleep(")]
    list_indices = [i for i, e in enumerate(event_order) if e == "list_active"]
    assert sleep_indices and list_indices
    assert sleep_indices[0] < list_indices[0], (
        f"sleep(poll) MUST come before the first list_active check; got event_order={event_order}"
    )


def test_dispatcher_waits_until_empty_or_escalates_when_stuck() -> None:
    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
        kill_process_group=lambda pgid, sig: kill_calls.append((pgid, sig)),
        clock=clock.now,
        sleep=_fake_sleep,
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=0.05)
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]
    assert (_PGID, signal.SIGKILL) in manager.kill_process_group_calls


def test_begin_interrupt_with_slow_body_still_escalates_after_body_returns() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []

    body_event = threading.Event()
    body_started = threading.Event()

    def _slow_record_interrupt() -> None:
        body_started.set()
        body_event.wait(timeout=1.0)

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
        kill_process_group=lambda pgid, sig: kill_calls.append((pgid, sig)),
        record_interrupt=_slow_record_interrupt,
        poll_interval_s=0.001,
        hard_kill_budget_s=0.05,
        clock=clock.now,
        sleep=_fake_sleep,
    )

    def _worker() -> None:
        dispatcher.begin_interrupt(block=True, grace_period_s=0.05)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    assert body_started.wait(timeout=0.5), (
        "worker thread did not enter _slow_record_interrupt in 0.5s"
    )

    clock.advance(0.5)

    body_event.set()

    worker.join(timeout=1.0)
    assert not worker.is_alive(), (
        f"worker thread did not complete in 1.0s; "
        f"sleep_calls={sleep_calls}, exit_calls={exit_calls}"
    )

    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"hard_exit should be called exactly once; got {exit_calls}"
    )

    assert manager.shutdown_all_for_label_calls == [("invoke:", 0.05)], (
        f"shutdown_all_for_label should be called once with the dispatcher's "
        f"kill_label and grace_period_s; got {manager.shutdown_all_for_label_calls}"
    )


def test_handle_keyboard_interrupt_force_kill_handler_restores_previous() -> None:
    manager = FakeProcessManager()
    _, dispatcher = _build_dispatcher(process_manager=manager)
    set_calls: list[tuple[int, object]] = []
    previous_handler = object()

    def _fake_getsignal(signum: int) -> object:
        return previous_handler

    def _fake_set(signum: int, handler: object) -> object:
        set_calls.append((signum, handler))
        return handler

    handle_keyboard_interrupt(
        monitor_stop=None,
        dispatcher=dispatcher,
        signal_getter=_fake_getsignal,
        signal_setter=_fake_set,
    )
    assert len(set_calls) == 4
    assert set_calls[0][0] == _SIGINT
    assert set_calls[1][0] == _SIGTERM
    assert set_calls[2][0] == _SIGINT
    assert set_calls[3][0] == _SIGTERM
    assert set_calls[2][1] is previous_handler


class _CancellableTask:
    def __init__(self) -> None:
        self.cancel_calls: int = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


class _HandlerCapturingLoop(asyncio.AbstractEventLoop):
    def __init__(self) -> None:
        self._handlers: list[Callable[[], None]] = []

    def add_signal_handler(self, sig: int, callback: Callable[[], None], *args: object) -> object:
        del sig, args
        self._handlers.append(callback)
        return None

    def remove_signal_handler(self, sig: int) -> bool:
        del sig
        return True

    def close(self) -> None:
        return

    def run_forever(self) -> None:
        raise NotImplementedError

    def run_until_complete(self, future: object) -> object:
        raise NotImplementedError

    def is_closed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return False

    def get_debug(self) -> bool:
        return False

    def set_debug(self, enabled: bool) -> None:
        del enabled

    def default_exception_handler(self, context: object) -> None:
        del context

    def call_exception_handler(self, context: object) -> None:
        del context

    def get_exception_handler(self) -> object:
        return None

    def set_exception_handler(self, handler: object) -> None:
        del handler

    def time(self) -> float:
        return 0.0

    def call_later(self, delay: float, callback: Callable[[], None], *args: object) -> object:
        del delay, callback, args
        return None

    def call_soon(self, callback: Callable[[], None], *args: object) -> object:
        del callback, args
        return None

    def create_task(self, _coro: object, *, name: str | None = None) -> object:
        raise NotImplementedError

    def create_future(self) -> object:
        raise NotImplementedError


@pytest.mark.subprocess_e2e
def test_async_first_sigint_propagates_kill_label_to_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _ExitsOnGracefulShutdown()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    _, dispatcher = _build_dispatcher(process_manager=manager)

    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.get_process_manager", lambda: manager)

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    assert root_task.cancel_calls == 1
    assert manager.shutdown_all_for_label_calls == [("invoke:", _FAKE_DEFAULT_GRACE)]


class _SyncExecutorHandlerLoop(_HandlerCapturingLoop):
    def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
        fn(*args)
        return _SyncDoneHandle()


class _SyncDoneHandle:
    def add_done_callback(self, callback: object) -> None:
        callback(self)

    def cancelled(self) -> bool:
        return False

    def exception(self) -> object:
        return None


def test_cli_catch_uses_dispatcher_with_block_true() -> None:
    real_factory = dispatcher_from_process_manager
    block_calls: list[bool] = []

    def _spy(**kwargs: object) -> InterruptDispatcher:
        pm = kwargs.get("process_manager")
        dispatcher: InterruptDispatcher = real_factory(process_manager=pm)
        real_begin = dispatcher.begin_interrupt

        class _Wrap:
            def __init__(self, inner: InterruptDispatcher) -> None:
                self._inner = inner

            def __call__(self, *args: object, **kw: object) -> None:
                block_calls.append(bool(kw.get("block", False)))
                real_begin(*args, **kw)

        object.__setattr__(dispatcher, "begin_interrupt", _Wrap(dispatcher))
        return dispatcher

    original_factory: object = getattr(
        ralph_cli_run_module, "dispatcher_from_process_manager", None
    )
    ralph_cli_run_module.dispatcher_from_process_manager = _spy
    try:
        manager = FakeProcessManager()
        spy_dispatcher: InterruptDispatcher = _spy(process_manager=manager)
        spy_dispatcher.begin_interrupt(block=True)
    finally:
        ralph_cli_run_module.dispatcher_from_process_manager = original_factory
    assert block_calls == [True]


def test_async_install_signal_handlers_accepts_dispatcher_positionally() -> None:
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    _, dispatcher = _build_dispatcher(process_manager=manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)
    assert len(loop._handlers) == 2
    assert dispatcher.controller.shutdown_all_for_label is not None


def test_async_dispatcher_synthesis_threads_kill_process_group_and_hard_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []
    os_exit_calls: list[tuple[int, ...]] = []

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    def _fake_os_exit(code: int) -> None:
        os_exit_calls.append((code,))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    controller = controller_from_process_manager(
        process_manager=manager,
        kill_process_group=_record_kill,
        hard_exit=_record_exit,
    )

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, controller)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    assert len(loop._handlers) == 4
    bridge._interrupt_count = 2
    with contextlib.suppress(SystemExit):
        loop._handlers[2]()
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]
    assert os_exit_calls == []


@pytest.mark.subprocess_e2e
class _ExitsOnGracefulShutdown(FakeProcessManager):
    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        super().shutdown_all_for_label(label_prefix, grace_period_s=grace_period_s)
        self.drain()


def test_async_install_signal_handlers_3arg_call_path_controller_none_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os_exit_calls: list[tuple[int, ...]] = []
    manager = _ExitsOnGracefulShutdown()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_os_exit(code: int) -> None:
        os_exit_calls.append((code,))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)
    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.get_process_manager", lambda: manager)

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    assert len(loop._handlers) == 4
    bridge._interrupt_count = 2
    with contextlib.suppress(SystemExit):
        loop._handlers[2]()
    assert os_exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_run_shutdown_block_does_not_sigkill_alive_but_zero_cpu_long_running_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    sleep_iterations = 0
    captured_manager: list[FakeProcessManager] = []

    def _fake_sleep(s: float) -> None:
        nonlocal sleep_iterations
        sleep_iterations += 1
        clock.advance(s)
        if sleep_iterations == 3 and captured_manager:
            captured_manager[0].drain()

    _patch_psutil(monkeypatch, lambda _n: 0.0)
    _patch_pid_alive(monkeypatch, alive=True)

    manager, dispatcher = _build_dispatcher(
        clock=clock.now,
        sleep=_fake_sleep,
        poll_interval_s=0.001,
    )
    captured_manager.append(manager)
    manager.add_active(pid=201, pgid=9999, label="invoke:fake")
    manager.policy = ProcessManagerPolicy(default_grace_period_s=1.0)

    run_shutdown_block(
        dispatcher,
        grace_period_s=1.0,
        join_timeout_s=0.05,
    )

    assert manager.kill_process_group_calls == [], (
        f"alive-but-zero-CPU agent was SIGKILLed; "
        f"kill_process_group_calls={manager.kill_process_group_calls}"
    )
    assert manager._active_records == [], (
        f"records should be drained naturally by the fake sleep; "
        f"got _active_records={manager._active_records}"
    )
