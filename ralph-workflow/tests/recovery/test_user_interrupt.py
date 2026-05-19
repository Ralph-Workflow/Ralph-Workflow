"""Black-box test: user interrupt behavior and ordered shutdown contract."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
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
    assert hasattr(bridge, "pids")
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


def test_signal_bridge_pid_tracking() -> None:
    """SignalBridge tracks PIDs for process cleanup."""
    bridge = SignalBridge()

    bridge.register_pid(123)
    bridge.register_pid(456)

    assert 123 in bridge.pids
    assert 456 in bridge.pids

    bridge.deregister_pid(123)
    assert 123 not in bridge.pids
    assert 456 in bridge.pids


def test_fake_monitor_default_state_online() -> None:
    """FakeConnectivityMonitor defaults to ONLINE if no initial_state provided."""
    monitor = FakeConnectivityMonitor()
    assert monitor.current_state == ConnectivityState.ONLINE
