from __future__ import annotations

import atexit
import contextlib
from typing import TYPE_CHECKING

from loguru import logger

from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_runtime import load_psutil_module as _load_psutil_module
from ralph.process.manager._process_termination_error import ProcessTerminationError

if TYPE_CHECKING:
    from collections.abc import Generator

    from ralph.process.manager._process_manager_policy import ProcessManagerPolicy


class _ProcessManagerState:
    instance: ProcessManager | None = None
    atexit_registered: bool = False


_DEFAULT_PSUTIL = _load_psutil_module()

_pm_state = _ProcessManagerState()


def shutdown_process_manager(*, grace_period_s: float = 0.5) -> None:
    pm = _pm_state.instance
    if pm is None:
        return
    with contextlib.suppress(Exception):
        pm.shutdown_all(grace_period_s=grace_period_s)


def _register_atexit_shutdown() -> None:
    if _pm_state.atexit_registered:
        return
    atexit.register(shutdown_process_manager)
    _pm_state.atexit_registered = True


def get_process_manager(*, policy: ProcessManagerPolicy | None = None) -> ProcessManager:
    if _pm_state.instance is None:
        _pm_state.instance = ProcessManager(policy=policy, psutil=_DEFAULT_PSUTIL)
    _register_atexit_shutdown()
    return _pm_state.instance


def install_process_manager(manager: ProcessManager) -> ProcessManager:
    _pm_state.instance = manager
    _register_atexit_shutdown()
    return manager


def reset_process_manager(*, reset_atexit_registration: bool = False) -> None:
    prior = _pm_state.instance
    try:
        if prior is not None:
            with contextlib.suppress(Exception):
                prior.shutdown_all(grace_period_s=0.5)
    finally:
        _pm_state.instance = None
        if reset_atexit_registration:
            _pm_state.atexit_registered = False


@contextlib.contextmanager
def process_phase_scope(phase_name: str) -> Generator[None, None, None]:
    try:
        yield
    finally:
        pm = get_process_manager()
        try:
            pm.shutdown_all_for_label(
                f"phase:{phase_name}",
                grace_period_s=pm.policy.default_grace_period_s,
            )
        except ProcessTerminationError:
            logger.error("phase:{} cleanup failed", phase_name)
            raise
