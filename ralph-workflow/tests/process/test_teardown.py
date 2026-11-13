from __future__ import annotations

import contextlib
import subprocess
import sys
import time

import psutil
import pytest

from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.process.teardown import DefaultProcessTeardown, verified_child_session_pgid

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _wait_for_descendants(
    host: subprocess.Popen[bytes],
    *,
    min_count: int = 1,
    timeout: float = 0.5,
) -> list[psutil.Process]:
    deadline = time.monotonic() + timeout
    last: list[psutil.Process] = []
    while time.monotonic() < deadline:
        if host.poll() is not None:
            break
        try:
            last = psutil.Process(host.pid).children(recursive=True)
        except psutil.Error:
            last = []
        if len(last) >= min_count:
            return last
        time.sleep(0.005)
    return last


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_teardown_subtree_reaps_nested_children() -> None:
    grandchild_script = "import time; time.sleep(600)"
    child_script = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_script!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host_script = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    children = _wait_for_descendants(host, min_count=2)
    assert host.poll() is None
    assert len(children) >= 2, f"expected nested descendants, got {len(children)}"
    child_pids = {p.pid for p in children}

    teardown = DefaultProcessTeardown(kill_escalation_ms=5.0)
    teardown.teardown_subtree(host.pid, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)

    with contextlib.suppress(subprocess.TimeoutExpired):
        host.wait(timeout=0.5)
    assert host.poll() is not None

    survivors = {pid for pid in child_pids if _is_live_process(pid)}
    assert not survivors, f"some descendants survived teardown: {survivors}"


def _is_live_process(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_teardown_subtree_missing_process_is_noop() -> None:
    teardown = DefaultProcessTeardown(kill_escalation_ms=5.0)
    teardown.teardown_subtree(999_999, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_teardown_subtree_reaps_orphaned_children_after_host_exit() -> None:
    child_script = (
        "import signal, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(600)"
    )
    host_script = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    children = _wait_for_descendants(host, min_count=1)
    assert host.poll() is None
    assert len(children) >= 1, f"expected at least one descendant, got {len(children)}"
    child_pid = children[0].pid

    host_pgid = verified_child_session_pgid(host.pid)
    assert host_pgid == host.pid

    host.kill()
    host.wait(timeout=2)
    with pytest.raises(psutil.NoSuchProcess):
        psutil.Process(host.pid)

    teardown = DefaultProcessTeardown(kill_escalation_ms=5.0)
    teardown.teardown_subtree(host.pid, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN, pgid=host_pgid)

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            psutil.Process(child_pid)
        except psutil.NoSuchProcess:
            break
        time.sleep(0.005)
    else:
        pytest.fail(f"descendant {child_pid} survived process-group teardown")

    with pytest.raises(psutil.NoSuchProcess):
        psutil.Process(child_pid)
