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
_PID_SAFE = 9999
_EXPECTED_HANDLER_INSTALL_COUNT = 2


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

    def test_first_sigint_does_not_force_kill_registered_pids(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        bridge.register_pid(_PID_B)
        bridge.register_pid(_PID_C)

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg") as mock_killpg:
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        mock_killpg.assert_not_called()

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

        assert loop.add_signal_handler.call_count == _EXPECTED_HANDLER_INSTALL_COUNT
        second_call = loop.add_signal_handler.call_args_list[1][0]
        assert second_call[0] == signal.SIGINT

    def test_second_sigint_force_kills_registered_pids_and_exits_130(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        bridge.register_pid(_PID_B)
        bridge.register_pid(_PID_C)

        first_handler = _install_and_get_first_handler(loop, task, bridge)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        second_handler = loop.add_signal_handler.call_args_list[1][0][1]

        with patch("os.killpg") as mock_killpg, patch("os._exit") as mock_exit:
            second_handler()

        killed_pids = {call.args[0] for call in mock_killpg.call_args_list}
        assert killed_pids == {_PID_B, _PID_C}
        assert all(call.args[1] == signal.SIGKILL for call in mock_killpg.call_args_list)
        mock_exit.assert_called_once_with(130)

    def test_injected_controller_handles_graceful_then_forced_sigint(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()
        events: list[tuple[str, object]] = []

        def shutdown_all(grace_period_s: float) -> None:
            events.append(("shutdown", grace_period_s))

        def record_interrupt() -> None:
            events.append(("record", None))

        def stop_connectivity() -> None:
            events.append(("stop", None))

        def kill_process_group(pid: int, sig: int) -> None:
            events.append(("kill", (pid, sig)))

        def hard_exit(code: int) -> None:
            events.append(("exit", code))

        controller = InterruptController(
            shutdown_all=shutdown_all,
            record_interrupt=record_interrupt,
            stop_connectivity=stop_connectivity,
            kill_process_group=kill_process_group,
            hard_exit=hard_exit,
        )
        bridge.register_pid(_PID_B)

        first_handler = _install_and_get_first_handler(loop, task, bridge, controller)
        first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        task.cancel.assert_called_once()
        assert ("record", None) in events
        assert ("stop", None) in events
        assert any(event[0] == "shutdown" and event[1] != 0 for event in events)
        assert not any(event[0] == "kill" for event in events)

        second_handler = loop.add_signal_handler.call_args_list[1][0][1]
        second_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        assert any(event[0] == "kill" and event[1][0] == _PID_B for event in events)
        assert ("exit", 130) in events

    def test_no_pids_registered_no_killpg_called(self) -> None:
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        task = MagicMock(spec=asyncio.Task)
        bridge = SignalBridge()

        first_handler = _install_and_get_first_handler(loop, task, bridge)

        with patch("os.killpg") as mock_killpg:
            first_handler()  # type: ignore[operator]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

        mock_killpg.assert_not_called()
