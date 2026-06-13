"""Unit tests for ralph.interrupt.asyncio_bridge.

Tests the SignalBridge dataclass and install_signal_handlers() function.
No real subprocesses are spawned — os.killpg and os._exit are mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

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
_EXPECTED_HANDLER_INSTALL_COUNT = 2

# AC-01 fixtures: pid != pgid so mis-routing is observable.
_PID_FOR_PGID_TEST = 101
_PGID_FOR_PGID_TEST = 9999

# AC-04 fixtures: kill-budget window for early-escalation poll.
_EARLY_ESCALATION_BUDGET = 0.5


class _FakeProcessManager:
    """Minimal ProcessManager fake for the asyncio bridge tests.

    The fake exposes only the methods ``install_signal_handlers`` and
    the dispatcher exercise: ``list_active``, ``kill_process_group``,
    ``shutdown_all``, ``shutdown_all_for_label``, and ``register_listener``
    (which returns a no-op unsubscribe callable).
    """

    def __init__(self) -> None:
        self.policy = ProcessManagerPolicy(default_grace_period_s=2.5)
        self._active_records: list[ProcessRecord] = []
        self.kill_process_group_calls: list[tuple[int, int]] = []
        self.shutdown_all_calls: list[float] = []
        self.shutdown_all_for_label_calls: list[tuple[str, float]] = []

    def add_active(
        self, pid: int, pgid: int, label: str = "invoke:fake"
    ) -> ProcessRecord:
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
        # AC-11 contract: manager.shutdown_all(0) is the only kill
        # path in the post-fix controller. The fake mirrors the real
        # ProcessManager's escalation behaviour so the test can
        # assert the kill through kill_process_group_calls.
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
        hard_exit=cast("Any", _record_exit),
        kill_process_group=cast("Any", _record_kill),
    )


class _CancellableTask:
    """Minimal task-like object whose ``cancel()`` records the call."""

    def __init__(self) -> None:
        self.cancel_calls: int = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


class _HandlerCapturingLoop:
    """Captures ``add_signal_handler`` and ``remove_signal_handler`` calls.

    Mirrors the duck-typed surface ``install_signal_handlers`` exercises
    without dragging in a real asyncio event loop. The ``run_in_executor``
    shim records the (fn, args) tuple and returns a never-resolving
    Future by default (override ``run_in_executor`` per-test to actually
    invoke the callable).
    """

    def __init__(self) -> None:
        self._handlers: list[object] = []
        self._remove_signal_handler_calls: list[int] = []
        self._executor_calls: list[tuple[object, tuple[object, ...]]] = []
        self._run_in_executor_impl: object = None

    def add_signal_handler(
        self, sig: int, callback: object, *args: object
    ) -> object:
        del args
        self._handlers.append(callback)
        return None

    def remove_signal_handler(self, sig: int) -> bool:
        self._remove_signal_handler_calls.append(sig)
        return True

    def run_in_executor(
        self, executor: object, fn: object, *args: object
    ) -> object:
        self._executor_calls.append((fn, args))
        impl = self._run_in_executor_impl
        if impl is not None:
            return impl(executor, fn, *args)
        # Default: never resolve. Returning a Future that the test never
        # awaits keeps the executor body unobserved.
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
    """Install handlers and return the first SIGINT callback."""
    install_signal_handlers(loop, task, bridge, controller)
    return loop._handlers[0]


# Second-SIGINT route uses PGIDs from pm.list_active() — define the
# PGIDs alongside the PIDs so the rewrite of the old register_pid-based
# tests can assert the correct (PGID, not PID) kill path.
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
        """The first-SIGINT path must NOT send SIGKILL to any tracked
        process group. The AC-01 fix routes the second-SIGINT kill
        via ``pm.list_active()`` (PGIDs), so the first handler is
        graceful-only: cancel ``root_task``, install the second
        handler, dispatch begin_interrupt + early-escalation poll
        via the executor.
        """
        manager = _FakeProcessManager()
        manager.add_active(pid=_PID_B, pgid=_PID_B_PGID)
        manager.add_active(pid=_PID_C, pgid=_PID_C_PGID)
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        # First handler does NOT call kill_process_group — kills happen
        # only on the second-SIGINT path. The executor body may run
        # (synchronously here) but does not call the kill seam
        # because the no-progress / dead-record escalation does not
        # fire for fresh records with no CPU-time history.
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
        """The second-SIGINT path force-kills the active records
        (PGIDs) and exits with code 130. With the AC-01 fix the
        kill is routed through ``pm.list_active()`` PGIDs, so the
        PGIDs in the FakePM's ``kill_process_group_calls`` are
        ``_PID_B_PGID`` and ``_PID_C_PGID`` — NOT the PIDs.
        """
        manager = _FakeProcessManager()
        manager.add_active(pid=_PID_B, pgid=_PID_B_PGID)
        manager.add_active(pid=_PID_C, pgid=_PID_C_PGID)
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        # Second handler must be installed.
        assert len(loop._handlers) == 2
        second_handler = loop._handlers[1]
        with contextlib.suppress(SystemExit):
            second_handler()
        killed_pgids = {call[0] for call in manager.kill_process_group_calls}
        assert killed_pgids == {_PID_B_PGID, _PID_C_PGID}
        assert all(call[1] == signal.SIGKILL for call in manager.kill_process_group_calls)

    def test_injected_controller_handles_graceful_then_forced_sigint(self) -> None:
        """PA-019 PINNED CONTRACT: when a raw InterruptController is
        passed to ``install_signal_handlers``, the synthesised
        dispatcher must forward ``kill_process_group`` and
        ``hard_exit`` so the controller's injected exit callable is
        the one invoked on the second-SIGINT path.

        The test wires a custom ``shutdown_all`` closure that records
        kill events when ``grace_period_s == 0`` (mirroring the
        AC-11 contract for FakePM.shutdown_all) and asserts the
        PGID ``_PID_B_PGID`` is killed.
        """
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
        # Use the synchronous-executor loop so the first-SIGINT
        # executor body actually runs in the test thread.
        loop = _SynchronousExecutorLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, controller)
        first_handler()
        assert root_task.cancel_calls == 1
        assert ("record", None) in events
        assert ("stop", None) in events
        assert any(event[0] == "shutdown" and event[1] != 0 for event in events)
        assert not any(event[0] == "kill" for event in events)
        second_handler = loop._handlers[1]
        with contextlib.suppress(SystemExit):
            second_handler()
        assert any(
            event[0] == "kill" and event[1][0] == pid_b_pgid_local
            for event in events
        )
        assert ("exit", 130) in events

    def test_no_pids_registered_no_killpg_called(self) -> None:
        manager = _FakeProcessManager()
        dispatcher = _build_dispatcher_for_async_bridge(manager)
        bridge = SignalBridge()
        loop = _HandlerCapturingLoop()
        root_task = _CancellableTask()
        first_handler = _install_and_get_first_handler(loop, root_task, bridge, dispatcher)
        first_handler()
        # No records in the manager, so no kill happens on the first
        # or (synchronously dispatched) executor body path.
        assert manager.kill_process_group_calls == []

    def test_asyncio_bridge_first_sigint_does_not_pass_block_true(self) -> None:
        """The first-SIGINT path in the asynchro_bridge must call
        ``active_dispatcher.begin_interrupt(...)`` WITHOUT
        ``block=True`` (intentional — the bridge relies on
        ``root_task.cancel()`` to wake the event loop instead of
        blocking). A regression that flips this to ``block=True``
        would deadlock the asyncio event loop waiting for a process
        manager that is never drained.

        The test monkeypatches ``InterruptDispatcher.begin_interrupt``
        on the CLASS (via setattr on the class object) with a wrapper
        that records the ``block`` kwarg and delegates to the original
        method via the original bound method. Class-level patching
        makes the wrapper apply to ALL instances, including the one
        the asynchro_bridge builds internally.
        """
        original_begin = InterruptDispatcher.__dict__["begin_interrupt"]
        block_kwargs: list[bool] = []

        def _spy(self: object, *args: object, **kwargs: object) -> object:
            block_kwargs.append(bool(kwargs.get("block", False)))
            return original_begin(self, *args, **kwargs)

        InterruptDispatcher.begin_interrupt = cast("Any", _spy)
        try:
            manager = _FakeProcessManager()
            dispatcher = _build_dispatcher_for_async_bridge(manager)
            bridge = SignalBridge()
            # Use the synchronous-executor loop so the executor
            # body (which calls begin_interrupt) actually runs.
            loop = _SynchronousExecutorLoop()
            root_task = _CancellableTask()
            first_handler = _install_and_get_first_handler(
                loop, root_task, bridge, dispatcher
            )
            first_handler()
        finally:
            InterruptDispatcher.begin_interrupt = cast("Any", original_begin)
        # The first-SIGINT path passes no ``block`` kwarg, so the
        # default ``block=False`` is recorded.
        assert block_kwargs, "begin_interrupt was not called"
        assert block_kwargs == [False], (
            f"asynchro_bridge first-SIGINT must NOT pass block=True; "
            f"got block_kwargs={block_kwargs}"
        )


# =====================================================================
# AC-01: second-SIGINT pid-vs-pgid routing
# =====================================================================


def test_second_sigint_force_kills_uses_pgid_not_pid() -> None:
    """AC-01: Second-SIGINT force-kill must use PGIDs from
    ``pm.list_active()``, not PIDs from a bridge-local pids set.

    With ``pid=101`` and ``pgid=9999`` the production code MUST
    call ``kill_process_group(9999, SIGKILL)`` and MUST NOT call
    ``kill_process_group(101, SIGKILL)``. The unfixed code routed
    via ``bridge.pids`` which stored PIDs from a process-event
    listener; the new code routes via ``pm.list_active()`` which
    returns records carrying PGIDs.

    The fake ``_FakeProcessManager`` does NOT fire
    ``ProcessEvent``s on ``add_active()``, so under the unfixed
    code ``bridge.pids`` stays empty and no kill happens. The
    test asserts ``kill_process_group_calls`` contains
    ``(9999, SIGKILL)``, which FAILS under the unfixed code (no
    kill recorded) and PASSES under the fixed code (force_exit
    reads ``pm.list_active()`` and forwards ``[r.pgid for r in
    active] == [9999]`` to the controller).
    """
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    # First handler increments count, cancels root_task, and installs
    # the second-SIGINT handler.
    loop._handlers[0]()

    # Second handler must now be installed.
    assert len(loop._handlers) == 2

    # Invoke the second handler; suppress the SystemExit raised by
    # hard_exit(130) in the dispatcher's force_exit tail.
    with contextlib.suppress(SystemExit):
        loop._handlers[1]()

    # The kill list must contain the PGID, not the PID.
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


# =====================================================================
# AC-03: asyncio first-SIGINT non-blocking cancel
# =====================================================================


def test_asyncio_first_sigint_cancels_task_before_begin_interrupt_returns() -> None:
    """AC-03: the asyncio first-SIGINT handler must cancel ``root_task``
    and swap to the second-SIGINT handler synchronously, even when
    ``begin_interrupt`` would block forever.

    The fake ``_FakeProcessManager`` overrides the dispatcher-bound
    ``hard_exit`` is irrelevant here — the test does not need
    ``begin_interrupt`` to actually return. Instead the test asserts
    that ``root_task.cancel()`` was called BEFORE the executor body
    runs. Under the unfixed code the first handler calls
    ``active_dispatcher.begin_interrupt(...)`` synchronously, so
    ``cancel()`` is only invoked after begin_interrupt returns (which
    here it never does). Under the fixed code the first handler
    does ``cancel()`` + ``add_signal_handler(SIGINT, _second_sigint)``
    + ``run_in_executor(...)`` synchronously, then the executor body
    runs in the background.
    """
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    # Invoke the first handler. The test must not hang: under the
    # fixed code, the cancel + handler-swap happens synchronously.
    loop._handlers[0]()

    # cancel() was called synchronously, BEFORE the executor body runs.
    assert root_task.cancel_calls == 1, (
        f"root_task.cancel() must be invoked synchronously; "
        f"got cancel_calls={root_task.cancel_calls}"
    )
    # The second-SIGINT handler was installed synchronously.
    assert len(loop._handlers) == 2
    # The executor body was dispatched (recorded in _executor_calls).
    assert len(loop._executor_calls) == 1, (
        "first-SIGINT must dispatch begin_interrupt+early-escalation "
        "via loop.run_in_executor"
    )


# =====================================================================
# AC-04: asyncio first-SIGINT early-escalation poll
# =====================================================================


class _SynchronousExecutorLoop(_HandlerCapturingLoop):
    """Loop variant whose ``run_in_executor`` invokes the supplied
    callable synchronously in the current thread. Lets the AC-04 test
    exercise the full dispatch path: cancel + handler-swap + executor
    body (begin_interrupt + run_early_escalation_poll).

    The returned object is a minimal ``add_done_callback``-supporting
    handle (not a real ``asyncio.Future``) so the done callback can be
    invoked synchronously without depending on a running event loop.
    """

    def __init__(self) -> None:
        super().__init__()
        self._executed_callables: list[object] = []

    def run_in_executor(
        self, executor: object, fn: object, *args: object
    ) -> object:
        self._executor_calls.append((fn, args))
        self._executed_callables.append(fn)
        # Invoke synchronously to exercise the dispatch path.
        result = fn(*args)
        return _SyncFuture(result=result)


class _SyncFuture:
    """Minimal future stand-in that supports ``add_done_callback``.

    The handle is invoked synchronously (no event loop dependency).
    ``add_done_callback`` runs the callback immediately if the future
    is already done; the production code only uses
    ``add_done_callback`` for the done-callback path on
    ``loop.run_in_executor`` futures, so this is sufficient.
    """

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
    """Clock abstraction for the AC-04 early-escalation test."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


def test_asyncio_first_sigint_runs_early_escalation_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-04: the asyncio first-SIGINT path must dispatch
    ``run_early_escalation_poll`` so a no-progress agent is
    SIGKILLd within ``hard_kill_budget_s``.

    The fake's ``kill_process_group`` is wired into the controller
    (via ``kill_process_group=_record_kill`` in the dispatcher
    factory), and the no-progress criterion fires because the
    record's CPU time never changes across polls. The test drives
    the fake clock past the early-escalation budget and asserts
    the manager recorded a SIGKILL for the record's PGID.
    """
    manager = _FakeProcessManager()
    manager.add_active(
        pid=_PID_FOR_PGID_TEST,
        pgid=_PGID_FOR_PGID_TEST,
        label="invoke:claude",
    )

    # Inject clock + sleep into the dispatcher so the early-escalation
    # poll can be advanced via the fake clock without wall-clock waits.
    clock = _EscalationFakeClock()
    sleep_calls: list[float] = []

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    # Patch ``psutil`` import to return a stub whose CPU time NEVER
    # changes, so the no-progress criterion fires.
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

    monkeypatch.setattr(
        dispatcher_mod.importlib, "import_module", _fake_import
    )
    # Patch os.kill (in the dispatcher module) so _pid_is_alive returns True.
    monkeypatch.setattr(dispatcher_mod.os, "kill", lambda _pid, _sig: None)

    # Build the dispatcher via the factory so the test exercises
    # the real wiring (kill_process_group + hard_exit).
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=cast("Any", _record_exit),
        kill_process_group=cast("Any", _record_kill),
        poll_interval_s=0.05,
        hard_kill_budget_s=_EARLY_ESCALATION_BUDGET,
        clock=cast("Any", clock.now),
        sleep=cast("Any", _fake_sleep),
    )

    bridge = SignalBridge()
    loop = _SynchronousExecutorLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)

    # Invoke the first handler. The synchronous-executor loop
    # runs the executor body inline, so begin_interrupt +
    # run_early_escalation_poll both run to completion.
    loop._handlers[0]()

    # The early-escalation poll must have SIGKILLd the record.
    assert manager.kill_process_group_calls, (
        "early-escalation poll must SIGKILL the no-progress record"
    )
    assert (_PGID_FOR_PGID_TEST, signal.SIGKILL) in manager.kill_process_group_calls


# =====================================================================
# AC-05: idempotent teardown callable returned by install_signal_handlers
# =====================================================================


def test_install_signal_handlers_returns_idempotent_teardown_callable() -> None:
    """AC-05: ``install_signal_handlers`` must return a non-None,
    callable teardown that removes the second-SIGINT handler.
    The teardown is idempotent: a second invocation must NOT
    raise.

    The handler-capturing loop records ``remove_signal_handler``
    calls. The test asserts:

    * ``teardown_fn`` is not None and is callable,
    * invoking it once calls ``remove_signal_handler(signal.SIGINT)``,
    * invoking it a second time does NOT raise.
    """
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)
    dispatcher = _build_dispatcher_for_async_bridge(manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    teardown_fn = install_signal_handlers(loop, root_task, bridge, dispatcher)

    # The teardown callable must be returned (the unfixed code returns
    # None implicitly).
    assert teardown_fn is not None, (
        "install_signal_handlers must return a teardown callable"
    )
    assert callable(teardown_fn), (
        "teardown must be callable; got "
        f"{type(teardown_fn).__name__}"
    )

    # First invocation: removes the second-SIGINT handler.
    teardown_fn()
    assert signal.SIGINT in loop._remove_signal_handler_calls, (
        f"teardown must call remove_signal_handler({signal.SIGINT}); "
        f"got {loop._remove_signal_handler_calls}"
    )

    # Second invocation: idempotent, no exception raised.
    teardown_fn()
    # The remove_signal_handler count may stay the same (idempotent
    # short-circuit) or grow (re-remove); both are acceptable. The
    # important contract is no exception.


# =====================================================================
# Long-running-task pin: second SIGINT during first SIGINT executor body
# =====================================================================


class _PausingExecutorLoop(_SynchronousExecutorLoop):
    """Loop whose ``run_in_executor`` records the callable but does
    NOT invoke it; returns a done ``_SyncFuture`` so the
    production code's done callback is a no-op.

    Subclasses :class:`_SynchronousExecutorLoop` to reuse the
    handler-capturing mechanics. Overrides only ``run_in_executor``
    so the executor body is paused; the test invokes the recorded
    callable manually after the second-SIGINT handler has fired.
    """

    def run_in_executor(
        self, executor: object, fn: object, *args: object
    ) -> object:
        self._executor_calls.append((fn, args))
        return _SyncFuture(result=None)


def test_second_sigint_during_first_sigint_executor_body() -> None:
    """NEW BEHAVIOR PIN: when the second SIGINT arrives while the
    first SIGINT's ``_shutdown_block`` executor body is still in
    flight, the second handler synchronously invokes
    ``force_exit``, and the executor body, when it eventually
    runs, does NOT trigger a second ``force_exit`` (the
    ``_force_exit_called`` idempotency guard fires correctly).

    The existing tests
    (``test_second_sigint_force_kills_uses_pgid_not_pid``,
    ``test_injected_controller_handles_graceful_then_forced_sigint``)
    assume the first-SIGINT executor body has already completed
    before the second handler fires. This test pins the
    body-still-in-flight scenario.

    Mechanism:

    1. Subclass :class:`_SynchronousExecutorLoop` with
       :class:`_PausingExecutorLoop` whose ``run_in_executor``
       records the ``(fn, args)`` but does NOT invoke ``fn(*args)``;
       returns a done ``_SyncFuture`` so the production code's
       done callback is a no-op.
    2. Invoke the first handler. Assert: ``root_task.cancel_calls
       == 1``, ``len(loop._handlers) == 2``,
       ``len(loop._executor_calls) == 1``, the executor body has
       NOT yet been invoked.
    3. Invoke the second handler. Assert: ``hard_exit`` was
       called exactly once (the second handler's ``force_exit``
       fires).
    4. Manually invoke the recorded executor body. Assert:
       ``hard_exit`` count remains 1 (the executor body
       completed normally; the idempotency guard is verified by
       an explicit second ``force_exit`` call after the body
       completes).
    5. Invoke the teardown callable twice and assert it does
       not raise.

    The test must run in under 200ms using only fakes. The
    dispatcher's ``hard_kill_budget_s`` is set to ``0.05`` so the
    early-escalation poll thread (which ``_shutdown_block``
    starts in a daemon thread) terminates within ~50ms.
    """
    manager = _FakeProcessManager()
    manager.add_active(pid=_PID_FOR_PGID_TEST, pgid=_PGID_FOR_PGID_TEST)

    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=cast("Any", lambda code: exit_calls.append((code,))),
        kill_process_group=cast("Any", lambda pgid, sig: kill_calls.append((pgid, sig))),
        hard_kill_budget_s=0.05,
        poll_interval_s=0.01,
    )

    bridge = SignalBridge()
    loop = _PausingExecutorLoop()
    root_task = _CancellableTask()
    teardown_fn = install_signal_handlers(loop, root_task, bridge, dispatcher)

    # First handler fires: cancel + install second handler + record
    # executor body (but do NOT invoke it).
    loop._handlers[0]()
    assert root_task.cancel_calls == 1
    assert len(loop._handlers) == 2
    assert len(loop._executor_calls) == 1

    # Second handler fires while the executor body is still paused.
    with contextlib.suppress(SystemExit):
        loop._handlers[1]()

    # hard_exit was called exactly once (by the second handler's
    # force_exit).
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"second handler should call hard_exit({INTERRUPT_EXIT_CODE}) once; "
        f"got {exit_calls}"
    )

    # Now manually invoke the executor body that was recorded. The
    # body calls _shutdown_block, which calls begin_interrupt
    # (no block=True) + the early-escalation poll in a daemon
    # thread. It completes normally without raising.
    fn, args = loop._executor_calls[0]
    fn(*args)

    # The executor body did NOT trigger a second force_exit; hard_exit
    # was still called exactly once.
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"executor body must NOT trigger a second hard_exit call; "
        f"got {exit_calls}"
    )

    # Explicit idempotency check: invoking force_exit a second
    # time after the executor body completed must NOT call
    # hard_exit again. The _force_exit_called guard fires
    # correctly.
    dispatcher.force_exit(bridge_pgids=[_PGID_FOR_PGID_TEST])
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"idempotency guard should prevent a second hard_exit call; "
        f"got {exit_calls}"
    )

    # The teardown callable is safe to invoke after the second
    # handler has fired (no exception raised).
    assert teardown_fn is not None
    teardown_fn()
    teardown_fn()
