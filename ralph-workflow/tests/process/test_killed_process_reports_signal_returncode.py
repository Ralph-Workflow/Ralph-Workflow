from __future__ import annotations

import sys
import time

import pytest

from ralph.process.manager import ProcessStatus, SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

_SIGTERM_RC = -15
_SIGKILL_RC = -9


def _wait_until_running(seconds: float = 0.1) -> None:
    time.sleep(seconds)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal exit statuses")
def test_terminated_child_reports_sigterm_returncode() -> None:
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
    pm = get_process_manager()
    handle = pm.spawn(["/bin/sleep", "30"], SpawnOptions(label="invoke:agent"))
    _wait_until_running()

    pm.shutdown_all_for_label("invoke:", grace_period_s=0.5)

    assert handle.returncode is not None
    assert handle.returncode != 0
