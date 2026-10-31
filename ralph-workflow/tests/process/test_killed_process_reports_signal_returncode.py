"""Regression: a sync child Ralph kills must never report exit code 0.

Observed 2026-10-30. ``ManagedProcess.terminate()`` on a plain
``subprocess.Popen`` child returned ``returncode == 0``. ``psutil.wait_procs``
reaped the child with ``waitpid`` first and harvested the real status
(``-SIGTERM`` / ``-SIGKILL``); the manager then asked the ``Popen`` itself via
``poll()``, which got ``ECHILD`` -- and CPython's ``Popen`` records ``ECHILD``
as ``returncode = 0``. Because that ``0`` is not ``None``, the harvested status
was discarded. An agent stopped by the operator or a supervisor was therefore
counted as a successful run.
"""

from __future__ import annotations

import sys
import time

import pytest

from ralph.process.manager import ProcessStatus, SpawnOptions, get_process_manager

# Real POSIX processes: the defect is the interaction between psutil's
# ``waitpid`` and the ``Popen``'s own ``ECHILD`` handling, which no fake
# process can reproduce.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

_SIGTERM_RC = -15
_SIGKILL_RC = -9


def _wait_until_running(seconds: float = 0.1) -> None:
    """Give the child a moment to install its handlers before it is signalled."""
    time.sleep(seconds)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal exit statuses")
def test_terminated_child_reports_sigterm_returncode() -> None:
    """A child that dies on SIGTERM reports -15 on the handle and the record."""
    pm = get_process_manager()
    handle = pm.spawn(["/bin/sleep", "30"], SpawnOptions(label="invoke:test"))
    _wait_until_running()

    handle.terminate(grace_period_s=0.5)

    assert handle.record.status is ProcessStatus.KILLED
    assert handle.returncode == _SIGTERM_RC
    assert handle.record.returncode == _SIGTERM_RC
    assert handle.poll() == _SIGTERM_RC


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal exit statuses")
def test_force_killed_child_reports_sigkill_returncode() -> None:
    """A child that ignores SIGTERM and is SIGKILLed reports -9, not success."""
    pm = get_process_manager()
    handle = pm.spawn(
        ["/bin/sh", "-c", "trap '' TERM; while :; do sleep 0.05; done"],
        SpawnOptions(label="invoke:test"),
    )
    _wait_until_running(0.15)

    handle.terminate(grace_period_s=0.2)

    assert handle.record.status is ProcessStatus.KILLED
    assert handle.returncode == _SIGKILL_RC
    assert handle.record.returncode == _SIGKILL_RC


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal exit statuses")
def test_label_shutdown_of_running_agent_is_not_a_clean_exit() -> None:
    """The supervisor's label sweep must leave a non-zero status for the reader."""
    pm = get_process_manager()
    handle = pm.spawn(["/bin/sleep", "30"], SpawnOptions(label="invoke:agent"))
    _wait_until_running()

    pm.shutdown_all_for_label("invoke:", grace_period_s=0.5)

    assert handle.returncode is not None
    assert handle.returncode != 0
