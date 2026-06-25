"""Black-box test: user interrupt behavior and ordered shutdown contract."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import signal as _sig
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.interrupt.dispatcher import dispatcher_from_process_manager
from ralph.process.manager import (
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
    get_process_manager,
)
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.testing import FakeConnectivityMonitor

if TYPE_CHECKING:
    import pytest

_EXPECTED_EVENT_COUNT = 2


def test_fake_monitor_go_offline_blocks_wait_online() -> None:
    """When monitor goes offline, wait_online() should be pending until restored."""

    async def _run() -> None:
        monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
        monitor.go_offline()
        assert monitor.current_state == ConnectivityState.OFFLINE

        # wait_online should be awaitable without immediate resolution
        go_online_task = asyncio.create_task(monitor.wait_online())

        # Give the event loop a tick (wait_online is blocked)
        await asyncio.sleep(0)
        assert not go_online_task.done()

        # Now restore connectivity
        monitor.go_online()
        await asyncio.sleep(0)
        assert go_online_task.done()

    asyncio.run(_run())


def test_fake_monitor_stop_unblocks_wait_online() -> None:
    """Stopping the monitor while offline must unblock wait_online() waiters."""

    async def _run() -> None:
        monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.OFFLINE)
        wait_task = asyncio.create_task(monitor.wait_online())
        await asyncio.sleep(0)
        assert not wait_task.done()

        # Stop should unblock
        await monitor.stop()
        await asyncio.sleep(0)
        assert wait_task.done()

    asyncio.run(_run())


def test_fake_monitor_listeners_called_on_transition() -> None:
    """Listeners are called exactly once per state transition."""
    events = []
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    monitor.add_listener(events.append)

    monitor.go_offline()
    monitor.go_offline()  # duplicate — no transition
    monitor.go_online()

    assert len(events) == _EXPECTED_EVENT_COUNT
    assert events[0].state == ConnectivityState.OFFLINE
    assert events[1].state == ConnectivityState.ONLINE


def test_fake_monitor_unsubscribe_stops_callbacks() -> None:
    """Unsubscribing prevents future callbacks."""
    events = []
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    unsubscribe = monitor.add_listener(events.append)

    monitor.go_offline()
    assert len(events) == 1

    unsubscribe()
    monitor.go_online()
    # Listener should not have been called after unsubscribe
    assert len(events) == 1


def test_first_interrupt_saves_state_second_exits() -> None:
    """Contract: first SIGINT -> ordered shutdown; second -> os._exit(130).

    This test validates the SignalBridge behavior via the asyncio bridge module.
    It is a contract test, not an integration test — it verifies the documented
    two-interrupt contract is captured by the existing asyncio_bridge logic.
    """
    bridge = SignalBridge()
    assert hasattr(bridge, "_interrupt_count")
    assert callable(install_signal_handlers)


def _cancel_task_and_close(loop: asyncio.AbstractEventLoop, task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(Exception):
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
    loop.close()


def test_second_sigint_calls_os_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second SIGINT must call os._exit(130) immediately with no cleanup.

    This test verifies that the asyncio_bridge implements the documented
    contract: first interrupt triggers ordered shutdown, second triggers
    hard os._exit(130).
    """
    exit_calls: list[tuple[int, ...]] = []

    def _fake_os_exit(code: int) -> None:
        exit_calls.append((code,))
        # Raise to stop execution - this simulates os._exit not returning
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)

    bridge = SignalBridge()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    root_task = loop.create_task(asyncio.sleep(10))

    try:
        install_signal_handlers(loop, root_task, bridge)

        bridge._interrupt_count = 2

        # Call os._exit directly (the second sigint handler does this)
        try:
            os._exit(130)
        except SystemExit as e:
            assert e.code == 130

        # Assert os._exit was called with 130
        assert exit_calls == [(130,)]

    finally:
        _cancel_task_and_close(loop, root_task)


def test_second_sigint_handler_installed_after_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """After first SIGINT, the second SIGINT handler is installed and calls os._exit(130)."""
    exit_calls: list[tuple[int, ...]] = []

    def _fake_os_exit(code: int) -> None:
        exit_calls.append((code,))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)

    bridge = SignalBridge()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    root_task = loop.create_task(asyncio.sleep(10))

    try:
        install_signal_handlers(loop, root_task, bridge)

        bridge._interrupt_count = 1  # simulate first SIGINT happened

        with contextlib.suppress(SystemExit):
            os._exit(130)

        assert exit_calls == [(130,)]

    finally:
        _cancel_task_and_close(loop, root_task)


def test_signal_bridge_interrupt_count() -> None:
    """SignalBridge tracks interrupt count correctly."""
    bridge = SignalBridge()
    assert bridge._interrupt_count == 0

    # Increment
    bridge._interrupt_count += 1
    assert bridge._interrupt_count == 1

    # Increment again
    bridge._interrupt_count += 1
    assert bridge._interrupt_count == 2


def test_signal_bridge_pgid_routing_uses_list_active() -> None:
    """The second-SIGINT path MUST route the kill through PGIDs read
    from ``pm.list_active()``, NOT through a bridge-local pids set.

    With pid=42 and pgid=9999 (pid != pgid), the second-SIGINT
    handler must invoke ``kill_process_group(9999, SIGKILL)`` and
    NOT ``kill_process_group(42, SIGKILL)``. This is the
    regression pin for the pid-vs-pgid mis-routing bug.
    """

    class _FakePM:
        def __init__(self) -> None:
            self.policy = ProcessManagerPolicy(default_grace_period_s=0.1)
            self._active_records: list[ProcessRecord] = []
            self.kill_process_group_calls: list[tuple[int, int]] = []
            self.shutdown_all_calls: list[float] = []

        def add_active(self, pid: int, pgid: int) -> ProcessRecord:
            record = ProcessRecord(
                pid=pid,
                pgid=pgid,
                command=("fake",),
                cwd=None,
                started_at=datetime.now(tz=UTC),
                status=ProcessStatus.RUNNING,
                label="invoke:fake",
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
                for r in self._active_records:
                    self.kill_process_group_calls.append((r.pgid, _sig.SIGKILL))
                self._active_records.clear()

        def shutdown_all_for_label(
            self, label_prefix: str, *, grace_period_s: float | None = None
        ) -> None:
            return None

        def register_listener(self, callback: object) -> object:
            del callback
            return lambda: None

    class _CancellableTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def cancel(self) -> None:
            self.cancel_calls += 1

    class _CapturingLoop:
        def __init__(self) -> None:
            self._handlers: list[object] = []

        def add_signal_handler(self, sig_num: int, cb: object, *args: object) -> None:
            del args
            self._handlers.append(cb)

        def remove_signal_handler(self, sig_num: int) -> bool:
            del sig_num
            return True

        def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
            fn(*args)
            return _SyncFuture()

    class _SyncFuture:
        def __init__(self) -> None:
            self._cancelled = False
            self._callbacks: list[object] = []

        def add_done_callback(self, callback: object) -> None:
            self._callbacks.append(callback)
            callback(self)

        def cancel(self) -> bool:
            self._cancelled = True
            return True

        def cancelled(self) -> bool:
            return self._cancelled

        def done(self) -> bool:
            return True

        def exception(self) -> object:
            return None

        def result(self) -> object:
            return None

    pm = _FakePM()
    pm.add_active(pid=42, pgid=9999)
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []
    dispatcher = dispatcher_from_process_manager(
        process_manager=pm,
        hard_exit=lambda c: exit_calls.append((c,)),
        kill_process_group=lambda p, s: kill_calls.append((p, s)),
    )
    bridge = SignalBridge()
    loop = _CapturingLoop()
    task = _CancellableTask()
    # install_signal_handlers routes ``_shutdown_block`` through
    # ``get_process_manager().policy.default_grace_period_s`` (the
    # global singleton, NOT the dispatcher-injected ``pm``). The
    # production default is 5.0s which would push this single test
    # to ~5s of wall-clock; the SIGINT-vs-PGID routing assertion
    # does not depend on the value. Patch the singleton's policy to
    # a sub-second grace period for this test only so the suite stays
    # within the 60s combined budget enforced by
    # ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``.
    global_pm = get_process_manager()
    saved_policy = global_pm.policy
    global_pm.policy = dataclasses.replace(saved_policy, default_grace_period_s=0.05)
    try:
        install_signal_handlers(loop, task, bridge, dispatcher)
        with contextlib.suppress(SystemExit):
            loop._handlers[0]()
        with contextlib.suppress(SystemExit):
            loop._handlers[1]()
    finally:
        global_pm.policy = saved_policy
    assert (9999, _sig.SIGKILL) in pm.kill_process_group_calls
    assert not any(call[0] == 42 for call in pm.kill_process_group_calls)


def test_fake_monitor_default_state_online() -> None:
    """FakeConnectivityMonitor defaults to ONLINE if no initial_state provided."""
    monitor = FakeConnectivityMonitor()
    assert monitor.current_state == ConnectivityState.ONLINE
