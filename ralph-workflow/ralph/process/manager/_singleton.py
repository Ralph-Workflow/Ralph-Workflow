"""Module-level ProcessManager singleton and related helpers."""

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
    """Mutable holder for module-level singleton and registration flag."""

    instance: ProcessManager | None = None
    atexit_registered: bool = False


_DEFAULT_PSUTIL = _load_psutil_module()

_pm_state = _ProcessManagerState()


def _atexit_shutdown() -> None:
    try:
        pm = _pm_state.instance
        if pm is None:
            return
        pm.shutdown_all(grace_period_s=0.5)
    except BaseException:
        pass


def get_process_manager(*, policy: ProcessManagerPolicy | None = None) -> ProcessManager:
    """Return the module-level ProcessManager singleton, creating it on first call."""
    if _pm_state.instance is None:
        _pm_state.instance = ProcessManager(policy=policy, psutil=_DEFAULT_PSUTIL)
    if not _pm_state.atexit_registered:
        atexit.register(_atexit_shutdown)
        _pm_state.atexit_registered = True
    return _pm_state.instance


def reset_process_manager() -> None:
    """Replace the singleton with a fresh instance.  Call from test teardown."""
    _pm_state.instance = None


@contextlib.contextmanager
def process_phase_scope(phase_name: str) -> Generator[None, None, None]:
    """Context manager that tears down all processes labeled 'phase:<phase_name>' on exit."""
    try:
        yield
    finally:
        try:
            get_process_manager().shutdown_all_for_label(
                f"phase:{phase_name}",
                grace_period_s=get_process_manager().policy.default_grace_period_s,
            )
        except ProcessTerminationError as exc:
            logger.warning(
                "phase:{} cleanup could not terminate all processes: {}", phase_name, exc
            )
