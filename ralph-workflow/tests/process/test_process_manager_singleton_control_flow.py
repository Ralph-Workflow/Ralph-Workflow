from __future__ import annotations

import pytest

from ralph.process import (
    get_process_manager,
    install_process_manager,
    reset_process_manager,
    shutdown_process_manager,
)
from ralph.process.manager import ProcessManager


class _ControlFlowSignal(BaseException):
    pass


class _RaisingManager(ProcessManager):
    def __init__(self, exc: BaseException) -> None:
        super().__init__(psutil=None)
        self._exc = exc

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        del grace_period_s
        raise self._exc


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3), _ControlFlowSignal()])
def test_shutdown_process_manager_propagates_control_flow(exc: BaseException) -> None:
    install_process_manager(_RaisingManager(exc))
    try:
        with pytest.raises(type(exc)):
            shutdown_process_manager()
    finally:
        install_process_manager(ProcessManager(psutil=None))


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), _ControlFlowSignal()])
def test_reset_process_manager_propagates_control_flow_but_clears_singleton(
    exc: BaseException,
) -> None:
    raising = _RaisingManager(exc)
    install_process_manager(raising)

    with pytest.raises(type(exc)):
        reset_process_manager()

    assert get_process_manager() is not raising


def test_ordinary_errors_are_still_swallowed() -> None:
    install_process_manager(_RaisingManager(RuntimeError("boom")))

    shutdown_process_manager()
    reset_process_manager()
