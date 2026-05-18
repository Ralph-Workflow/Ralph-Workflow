"""Unit tests for ralph.interrupt.asyncio_bridge.

Tests the SignalBridge dataclass and install_signal_handlers() function.
No real subprocesses are spawned — os.killpg and os._exit are mocked.
"""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers

if TYPE_CHECKING:
    from unittest.mock import MagicMock

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
