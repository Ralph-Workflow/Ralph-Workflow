"""Tests for process subtree teardown."""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from ralph.process.teardown import DefaultProcessTeardown

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_teardown_subtree_reaps_nested_children() -> None:
    """DefaultProcessTeardown kills the host and all descendants transitively."""
    # Spawn a host that spawns a child; the child spawns a grandchild. All three
    # processes must be reaped by teardown_subtree.
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
    time.sleep(0.4)
    assert host.poll() is None

    # Verify both child and grandchild exist before teardown.
    host_proc = psutil.Process(host.pid)
    children = host_proc.children(recursive=True)
    assert len(children) >= 2, f"expected nested descendants, got {len(children)}"
    child_pids = {p.pid for p in children}

    teardown = DefaultProcessTeardown(kill_escalation_ms=500.0)
    teardown.teardown_subtree(host.pid)

    for _ in range(40):
        if host.poll() is not None:
            break
        time.sleep(0.05)
    assert host.poll() is not None

    # Confirm no descendants survived.
    gone: set[int] = set()
    for pid in child_pids:
        try:
            psutil.Process(pid)
        except psutil.NoSuchProcess:
            gone.add(pid)
    assert gone == child_pids, f"some descendants survived teardown: {child_pids - gone}"


def test_teardown_subtree_missing_process_is_noop() -> None:
    """Tearing down a non-existent PID does not raise."""
    teardown = DefaultProcessTeardown(kill_escalation_ms=10.0)
    # PID 999999 is unlikely to exist.
    teardown.teardown_subtree(999_999)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_teardown_subtree_reaps_orphaned_children_after_host_exit() -> None:
    """When the host is already dead, descendants are reaped via the process group.

    The host is the session leader (``start_new_session=True``), so its PID is
    also the process group ID. After the host is killed, the child ignores
    SIGHUP and would normally be orphaned; ``teardown_subtree`` must still reap
    it by signaling the process group.
    """
    child_script = (
        "import signal, time; "
        "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
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
        start_new_session=True,
    )
    time.sleep(0.4)
    assert host.poll() is None

    host_proc = psutil.Process(host.pid)
    children = host_proc.children(recursive=True)
    assert len(children) >= 1, f"expected at least one descendant, got {len(children)}"
    child_pid = children[0].pid

    # Kill the host directly. The child ignores SIGHUP, so it survives.
    host.kill()
    host.wait(timeout=2)
    with pytest.raises(psutil.NoSuchProcess):
        psutil.Process(host.pid)

    # Now teardown_subtree cannot enumerate via psutil, but it should fall
    # back to signaling the host's process group and reap the child.
    teardown = DefaultProcessTeardown(kill_escalation_ms=500.0)
    teardown.teardown_subtree(host.pid)

    for _ in range(40):
        try:
            psutil.Process(child_pid)
        except psutil.NoSuchProcess:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"descendant {child_pid} survived process-group teardown")

    with pytest.raises(psutil.NoSuchProcess):
        psutil.Process(child_pid)
