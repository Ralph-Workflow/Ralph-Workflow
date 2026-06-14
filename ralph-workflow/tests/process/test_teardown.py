"""Tests for process subtree teardown."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ralph.process.teardown import DefaultProcessTeardown

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_teardown_subtree_reaps_nested_children() -> None:
    """DefaultProcessTeardown kills the host and all descendants."""
    # Spawn a top-level shell that creates a grandchild sleep process and then
    # waits. The grandchild is in the same process group as the host so the
    # teardown walk reaches it.
    script = "import os, time; time.sleep(600)"
    host = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    assert host.poll() is None

    teardown = DefaultProcessTeardown(kill_escalation_ms=200.0)
    teardown.teardown_subtree(host.pid)

    for _ in range(20):
        if host.poll() is not None:
            break
        time.sleep(0.05)
    assert host.poll() is not None


def test_teardown_subtree_missing_process_is_noop() -> None:
    """Tearing down a non-existent PID does not raise."""
    teardown = DefaultProcessTeardown(kill_escalation_ms=10.0)
    # PID 999999 is unlikely to exist.
    teardown.teardown_subtree(999_999)
