"""Unit tests for ralph.interrupt.asyncio_bridge.

Tests the SignalBridge dataclass and install_signal_handlers() function.
No real subprocesses are spawned — os.killpg and os._exit are mocked.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import MagicMock, patch

from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.interrupt.controller import InterruptController

_PID_A = 42
_PID_B = 1234
_PID_C = 5678
_PID_DEAD = 999
_PID_PERM = 777
_PID_SAFE = 9999


def _extract_sigint_callback(loop: MagicMock) -> tuple[object, ...]:
    """Return (first_handler, second_handler) from add_signal_handler call args."""
    calls = loop.add_signal_handler.call_args_list
    return calls


def _install_and_get_first_handler(
    loop: MagicMock,
    task: MagicMock,
    bridge: SignalBridge,
    controller: InterruptController | None = None,
) -> object:
    """Install handlers and return the first SIGINT callback."""
    install_signal_handlers(loop, task, bridge, controller)
    first_call_args = loop.add_signal_handler.call_args_list[0][0]
    assert first_call_args[0] == signal.SIGINT
    return first_call_args[1]


class TestSignalBridge:
    def test_register_pid_adds_to_pids(self) -> None:
        bridge = SignalBridge()
        bridge.register_pid(_PID_A)
        assert _PID_A in bridge.pids

    def test_deregister_pid_removes_from_pids(self) -> None:
        bridge = SignalBridge()
        bridge.register_pid(_PID_A)
        bridge.deregister_pid(_PID_A)
        assert _PID_A not in bridge.pids

    def test_deregister_nonexistent_pid_is_safe(self) -> None:
        bridge = SignalBridge()
        bridge.deregister_pid(_PID_SAFE)

    def test_register_deregister_multiple_pids(self) -> None:
        pid_one, pid_two, pid_three = 1, 2, 3
        bridge = SignalBridge()
        bridge.register_pid(pid_one)
        bridge.register_pid(pid_two)
        bridge.register_pid(pid_three)
        bridge.deregister_pid(pid_two)
        assert pid_one in bridge.pids
        assert pid_two not in bridge.pids
        assert pid_three in bridge.pids

    def test_initial_pids_empty(self) -> None:
        bridge = SignalBridge()
        assert len(bridge.pids) == 0

    def test_initial_interrupt_count_zero(self) -> None:
        bridge = SignalBridge()
        assert bridge._interrupt_count == 0


class TestInstallSignalHandlers:
    def test_installs_sigint_handler_on_loop(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        install_signal_handlers(loop, task, bridge)

        loop.add_signal_handler.assert_called()
        first_call = loop.add_signal_handler.call_args_list[0][0]
        assert first_call[0] == signal.SIGINT

    def test_first_sigint_cancels_task(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        task.cancel.assert_called_once()

    def test_first_sigint_kills_registered_pids(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        bridge.register_pid(_PID_B)
        bridge.register_pid(_PID_C)

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg") as mock_killpg:
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        killed_pids = {c[0][0] for c in mock_killpg.call_args_list}
        assert _PID_B in killed_pids
        assert _PID_C in killed_pids
        for c in mock_killpg.call_args_list:
            assert c[0][1] == signal.SIGKILL

    def test_first_sigint_ignores_dead_processes(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        bridge.register_pid(_PID_DEAD)

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg", side_effect=ProcessLookupError):
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        task.cancel.assert_called_once()

    def test_first_sigint_ignores_permission_error(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        bridge.register_pid(_PID_PERM)

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg", side_effect=PermissionError):
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        task.cancel.assert_called_once()

    def test_first_sigint_increments_interrupt_count(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        assert bridge._interrupt_count == 1

    def test_first_sigint_installs_second_handler(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        expected_handler_install_count = 2
        assert loop.add_signal_handler.call_count == expected_handler_install_count
        second_call = loop.add_signal_handler.call_args_list[1][0]
        assert second_call[0] == signal.SIGINT

    def test_second_sigint_exits_130(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        second_call_args = loop.add_signal_handler.call_args_list[1][0]
        second_handler = second_call_args[1]

        with patch("os._exit") as mock_exit:
            second_handler()

        mock_exit.assert_called_once_with(130)

    def test_first_sigint_uses_injected_controller(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        events: list[tuple[str, object]] = []
        controller = InterruptController(
            shutdown_all=lambda grace_period_s: events.append(("shutdown", grace_period_s)),
            record_interrupt=lambda: events.append(("record", None)),
            stop_connectivity=lambda: events.append(("stop", None)),
            kill_process_group=lambda pid, sig: events.append(("kill", (pid, sig))),
            hard_exit=lambda code: events.append(("exit", code)),
        )
        bridge.register_pid(_PID_B)

        first_handler = _install_and_get_first_handler(loop, task, bridge, controller)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        task.cancel.assert_called_once()
        assert ("record", None) in events
        assert ("shutdown", 0) in events
        assert any(event[0] == "kill" and event[1][0] == _PID_B for event in events)

    def test_no_pids_registered_no_killpg_called(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg") as mock_killpg:
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        mock_killpg.assert_not_called()
