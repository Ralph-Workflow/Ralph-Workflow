from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import ralph.interrupt.dispatcher as dispatcher_mod
from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.interrupt.controller import INTERRUPT_EXIT_CODE, InterruptController
from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
)
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    import pytest

_PID_A = 42
_PID_B = 1234
_PID_C = 5678
_PID_SAFE = 9999
_EXPECTED_HANDLER_INSTALL_COUNT = 4

_PID_FOR_PGID_TEST = 101
_PGID_FOR_PGID_TEST = 9999

_EARLY_ESCALATION_BUDGET = 0.5


class _FakeProcessManager:
    def __init__(self) -> None:
        self.policy = ProcessManagerPolicy(default_grace_period_s=2.5)
        self._active_records: list[ProcessRecord] = []
        self.kill_process_group_calls: list[tuple[int, int]] = []
        self.shutdown_all_calls: list[float] = []
        self.shutdown_all_for_label_calls: list[tuple[str, float]] = []

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

    def list_active(self) -> list[ProcessRecord]:
        return list(self._active_records)

    def kill_process_group(self, pgid: int, sig: int) -> None:
        self.kill_process_group_calls.append((pgid, sig))

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

    def register_listener(self, callback: object) -> object:
        del callback
        return lambda: None


def _build_dispatcher_for_async_bridge(
    manager: _FakeProcessManager,
) -> InterruptDispatcher:
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    return dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=_record_exit,
        kill_process_group=_record_kill,
    )


class _CancellableTask:
    def __init__(self) -> None:
        self.cancel_calls: int = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


class _HandlerCapturingLoop:
    def __init__(self) -> None:
        self._handlers: list[object] = []
        self._remove_signal_handler_calls: list[int] = []
        self._executor_calls: list[tuple[object, tuple[object, ...]]] = []
        self._run_in_executor_impl: object = None

    def add_signal_handler(self, sig: int, callback: object, *args: object) -> object:
        del args
        self._handlers.append(callback)
        return None

    def remove_signal_handler(self, sig: int) -> bool:
        self._remove_signal_handler_calls.append(sig)
        return True

    def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
        self._executor_calls.append((fn, args))
        impl = self._run_in_executor_impl
        if impl is not None:
            return impl(executor, fn, *args)
        loop = asyncio.new_event_loop()
        try:
            return loop.create_future()
        finally:
            loop.close()


def _install_and_get_first_handler(
    loop: _HandlerCapturingLoop,
    task: _CancellableTask,
    bridge: SignalBridge,
    controller: InterruptController | None = None,
) -> object:
    install_signal_handlers(loop, task, bridge, controller)
    return loop._handlers[0]


_PID_B_PGID = 12340
_PID_C_PGID = 56780


class TestInstallSignalHandlers:
    def test_installs_sigint_handler_on_loop(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        install_signal_handlers(loop, root_task, bridge, dispatcher)
        assert len(loop._handlers) >= 1
        assert loop._handlers[0] is not None

    def test_first_sigint_cancels_task(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert root_task.cancel_calls == 1

    def test_first_sigint_does_not_force_kill_registered_pids(self) -> None:
        manager = _FakeProcessManager()
        manager.add_active(pid=_PID_B, pgid=_PID_B_PGID)
        manager.add_active(pid=_PID_C, pgid=_PID_C_PGID)
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert manager.kill_process_group_calls == []

    def test_first_sigint_increments_interrupt_count(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert bridge._interrupt_count == 1

    def test_first_sigint_installs_second_handler(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT

    def test_second_sigint_force_kills_registered_pids_and_exits_130(self) -> None:
        manager = _FakeProcessManager()
        manager.add_active(pid=_PID_B, pgid=_PID_B_PGID)
        manager.add_active(pid=_PID_C, pgid=_PID_C_PGID)
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT
        second_handler = loop._handlers[2]
        with contextlib.suppress(SystemExit):
            second_handler()
        killed_pgids = {call[0] for call in manager.kill_process_group_calls}
        assert killed_pgids == {_PID_B_PGID, _PID_C_PGID}
        assert all(call[1] == signal.SIGKILL for call in manager.kill_process_group_calls)

    def test_injected_controller_handles_graceful_then_forced_sigint(self) -> None:
        pid_b_pgid_local = _PID_B_PGID
        events: list[tuple[str, object]] = []

        def _shutdown_all(grace_period_s: float) -> None:
            events.append(("shutdown", grace_period_s))
            if grace_period_s == 0:
                events.append(("kill", (pid_b_pgid_local, signal.SIGKILL)))

        def _record_interrupt() -> None:
            events.append(("record", None))

        def _stop_connectivity() -> None:
            events.append(("stop", None))

        def _record_exit(code: int) -> None:
            events.append(("exit", code))

        controller = InterruptController(
            shutdown_all=_shutdown_all,
            record_interrupt=_record_interrupt,
            stop_connectivity=_stop_connectivity,
            hard_exit=_record_exit,
        )
        bridge = SignalBridge()
        manager = _FakeProcessManager()
        manager.add_active(pid=_PID_B, pgid=pid_b_pgid_local)
        loop = _SynchronousExecutorLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, controller)
        first_handler()
        assert root_task.cancel_calls == 1
        assert ("record", None) in events
        assert ("stop", None) in events
        assert any(event[0] == "shutdown" and event[1] != 0 for event in events)
        assert not any(event[0] == "kill" for event in events)
        second_handler = loop._handlers[2]
        with contextlib.suppress(SystemExit):
            second_handler()
        assert any(event[0] == "kill" and event[1][0] == pid_b_pgid_local for event in events)
        assert ("exit", 130) in events

    def test_no_pids_registered_no_killpg_called(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        assert manager.kill_process_group_calls == []

    def test_asyncio_bridge_first_sigint_passes_block_true_via_shutdown_block(
        self,
    ) -> None:
        original_begin = InterruptDispatcher.__dict__["begin_interrupt"]
        block_kwargs: list[bool] = []

        def _spy(self: object, *args: object, **kwargs: object) -> object:
            block_kwargs.append(bool(kwargs.get("block", False)))
            return original_begin(self, *args, **kwargs)

        InterruptDispatcher.begin_interrupt = _spy
        try:
            manager = _FakeProcessManager()
            dispatcher = _build_dispatcher_for_async_bridge(manager)
            bridge = SignalBridge()
            loop = _SynchronousExecutorLoop()
            root_task = _CancellableTask()
            first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
            first_handler()
        finally:
            InterruptDispatcher.begin_interrupt = original_begin
        assert block_kwargs, "begin_interrupt was not called"
        assert block_kwargs == [True], (
            f"asynchro_bridge first-SIGINT must pass block=True via "
            f"run_shutdown_block (NEW contract); "
            f"got block_kwargs={block_kwargs}"
        )


def test_second_sigint_force_kills_uses_pgid_not_pid() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[0]()

    assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT

    with contextlib.suppress(SystemExit):
        loop._handlers[2]()

    killed_pgids = {call[0] for call in manager.kill_process_group_calls}
    killed_signals = {call[1] for call in manager.kill_process_group_calls}
    assert _PGID_FOR_PGID_TEST in killed_pgids, (
        f"second-SIGINT must kill PGID {_PGID_FOR_PGID_TEST}, "
        f"got kill_process_group_calls={manager.kill_process_group_calls}"
    )
    assert signal.SIGKILL in killed_signals
    assert _PID_FOR_PGID_TEST not in killed_pgids, (
        f"second-SIGINT must NOT kill PID {_PID_FOR_PGID_TEST}; "
        f"got kill_process_group_calls={manager.kill_process_group_calls}"
    )


def test_asyncio_first_sigint_cancels_task_before_begin_interrupt_returns() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[0]()

    assert root_task.cancel_calls == 1, (
        f"root_task.cancel() must be invoked synchronously; "
        f"got cancel_calls={root_task.cancel_calls}"
    )
    assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT
    assert len(loop._executor_calls) == 1, (
        "first-SIGINT must dispatch begin_interrupt+early-escalation via loop.run_in_executor"
    )


class _SynchronousExecutorLoop(_HandlerCapturingLoop):
    def __init__(self) -> None:
        super().__init__()
        self._executed_callables: list[object] = []

    def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
        self._executor_calls.append((fn, args))
        self._executed_callables.append(fn)
        result = fn(*args)
        return _SyncFuture(result=result)


class _SyncFuture:
    def __init__(self, *, result: object = None) -> None:
        self._result = result
        self._exception: object = None
        self._cancelled = False
        self._callbacks: list[object] = []

    def add_done_callback(self, callback: object) -> None:
        self._callbacks.append(callback)
        callback(self)

    def cancel(self) -> bool:
        if self._cancelled:
            return False
        self._cancelled = True
        return True

    def cancelled(self) -> bool:
        return self._cancelled

    def done(self) -> bool:
        return True

    def result(self) -> object:
        return self._result

    def exception(self) -> object:
        return self._exception


class _EscalationFakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


def test_asyncio_first_sigint_runs_early_escalation_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeProcessManager()
    manager.add_active(
        pid=_PID_FOR_PGID_TEST,
        pgid=_PGID_FOR_PGID_TEST,
        label="invoke:claude",
    )

    clock = _EscalationFakeClock()
    sleep_calls: list[float] = []

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    class _FakeCpu:
        user = 0.0
        system = 0.0

    class _FakeProc:
        def cpu_times(self) -> _FakeCpu:
            return _FakeCpu()

    class _FakePsutil:
        Process = staticmethod(lambda _pid: _FakeProc())

    real_import = dispatcher_mod.importlib.import_module

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            return _FakePsutil
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.importlib, "import_module", _fake_import)
    monkeypatch.setattr(dispatcher_mod.os, "kill", lambda _pid, _sig: None)

    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=_record_exit,
        kill_process_group=_record_kill,
        poll_interval_s=0.05,
        hard_kill_budget_s=_EARLY_ESCALATION_BUDGET,
        clock=clock.now,
        sleep=_fake_sleep,
    )

    bridge = SignalBridge()
    loop = _SynchronousExecutorLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[0]()

    assert manager.kill_process_group_calls, (
        "early-escalation poll must SIGKILL the no-progress record"
    )
    assert (_PGID_FOR_PGID_TEST, signal.SIGKILL) in manager.kill_process_group_calls


def test_install_signal_handlers_returns_idempotent_teardown_callable() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    teardown_fn = install_signal_handlers(loop, root_task, bridge, dispatcher)

    assert teardown_fn is not None, "install_signal_handlers must return a teardown callable"
    assert callable(teardown_fn), f"teardown must be callable; got {type(teardown_fn).__name__}"

    teardown_fn()
    assert signal.SIGINT in loop._remove_signal_handler_calls, (
        f"teardown must call remove_signal_handler({signal.SIGINT}); "
        f"got {loop._remove_signal_handler_calls}"
    )

    teardown_fn()


class _PausingExecutorLoop(_SynchronousExecutorLoop):
    def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
        self._executor_calls.append((fn, args))
        return _SyncFuture(result=None)


def test_second_sigint_during_first_sigint_executor_body() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)

    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=lambda code: exit_calls.append((code,)),
        kill_process_group=lambda pgid, sig: kill_calls.append((pgid, sig)),
        hard_kill_budget_s=0.05,
        poll_interval_s=0.01,
    )

    bridge = SignalBridge()
    loop = _PausingExecutorLoop()
    root_task = _CancellableTask()
    teardown_fn = install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[0]()
    assert root_task.cancel_calls == 1
    assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT
    assert len(loop._executor_calls) == 1

    with contextlib.suppress(SystemExit):
        loop._handlers[2]()

    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"second handler should call hard_exit({INTERRUPT_EXIT_CODE}) once; got {exit_calls}"
    )

    fn, args = loop._executor_calls[0]
    fn(*args)

    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"executor body must NOT trigger a second hard_exit call; got {exit_calls}"
    )

    dispatcher.force_exit(bridge_pgids=[_PGID_FOR_PGID_TEST])
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"idempotency guard should prevent a second hard_exit call; got {exit_calls}"
    )

    assert teardown_fn is not None
    teardown_fn()
    teardown_fn()


def test_first_sigint_then_second_sigterm_force_exits() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[0]()
    assert root_task.cancel_calls == 1
    assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT

    with contextlib.suppress(SystemExit):
        loop._handlers[3]()

    assert (_PGID_FOR_PGID_TEST, signal.SIGKILL) in manager.kill_process_group_calls, (
        f"second-SIGTERM must call the force-exit kill path; "
        f"got kill_process_group_calls={manager.kill_process_group_calls}"
    )


def test_first_sigterm_then_second_sigint_force_exits() -> None:
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    loop._handlers[1]()
    assert root_task.cancel_calls == 1
    assert len(loop._handlers) == _EXPECTED_HANDLER_INSTALL_COUNT

    with contextlib.suppress(SystemExit):
        loop._handlers[2]()

    assert (_PGID_FOR_PGID_TEST, signal.SIGKILL) in manager.kill_process_group_calls, (
        f"second-SIGINT must call the force-exit kill path; "
        f"got kill_process_group_calls={manager.kill_process_group_calls}"
    )


def test_install_signal_handlers_initial_state_has_two_handlers() -> None:
    manager = _FakeProcessManager()
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    assert len(loop._handlers) == 2
