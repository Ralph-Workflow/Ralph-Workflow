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


def shutdown_process_manager(*, grace_period_s: float = 0.5) -> None:
    """Shut down the module-level ProcessManager, swallowing every error.

    This is the body of the interpreter-exit hook, and the explicit
    shutdown entry point for an embedder that owns the process lifetime.
    Failing to stop a child must never turn into an interpreter-exit
    traceback, so every error raised by ``shutdown_all`` is suppressed.
    """
    try:
        pm = _pm_state.instance
        if pm is None:
            return
        pm.shutdown_all(grace_period_s=grace_period_s)
    except BaseException:
        pass


def _register_atexit_shutdown() -> None:
    """Register the interpreter-exit shutdown hook exactly once."""
    if _pm_state.atexit_registered:
        return
    atexit.register(shutdown_process_manager)
    _pm_state.atexit_registered = True


def get_process_manager(*, policy: ProcessManagerPolicy | None = None) -> ProcessManager:
    """Return the module-level ProcessManager singleton, creating it on first call."""
    if _pm_state.instance is None:
        _pm_state.instance = ProcessManager(policy=policy, psutil=_DEFAULT_PSUTIL)
    _register_atexit_shutdown()
    return _pm_state.instance


def install_process_manager(manager: ProcessManager) -> ProcessManager:
    """Install ``manager`` as the module-level ProcessManager singleton.

    An embedder that builds its own ProcessManager (with an injected
    process factory, a custom policy, or a psutil replacement) installs
    it here so every later ``get_process_manager()`` call returns that
    instance instead of a freshly constructed default.

    The interpreter-exit shutdown hook is registered on first install so
    an installed manager is always reaped when the process ends.

    Returns:
        The manager that was installed, so the call can be chained.
    """
    _pm_state.instance = manager
    _register_atexit_shutdown()
    return manager


def reset_process_manager(*, reset_atexit_registration: bool = False) -> None:
    """Replace the singleton with a fresh instance.  Call from test teardown.

    The prior instance, if any, is shut down BEFORE the singleton
    reference is nulled so a dangling tracked record (a real process
    the previous test started) is reaped instead of being orphaned.
    Shutting down BEFORE nulling prevents any new caller (including
    a late shutdown triggered by a child of the prior manager) from
    seeing the prior instance via ``get_process_manager`` while it is
    still being torn down.

    Any exception raised by ``shutdown_all`` is swallowed (test
    teardown must never raise) so a refusing-to-die process cannot
    break the suite.

    Args:
        reset_atexit_registration: When True, also clear the flag that
            records whether the interpreter-exit hook is registered, so
            the next ``get_process_manager()`` call registers it again.
            Only useful when a caller needs to observe the
            registration itself.
    """
    prior = _pm_state.instance
    if prior is not None:
        with contextlib.suppress(BaseException):
            prior.shutdown_all(grace_period_s=0.5)
    _pm_state.instance = None
    if reset_atexit_registration:
        _pm_state.atexit_registered = False


@contextlib.contextmanager
def process_phase_scope(phase_name: str) -> Generator[None, None, None]:
    """Context manager that tears down all processes labeled 'phase:<phase_name>' on exit."""
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
