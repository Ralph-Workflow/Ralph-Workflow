"""Regression: teardown of an unreaped direct child finishes inside its ceiling.

Observed 2026-10-30. ``DefaultProcessTeardown`` polled
``psutil.Process.is_running()`` to see whether the subtree had exited, but
``is_running()`` stays True for a zombie. A direct child whose ``Popen`` owner
has not ``wait()``-ed yet is exactly that: it died on the first SIGTERM, then
stayed "running" through the whole escalation window, was SIGKILLed, and stayed
"running" through a second full window -- ~4 s against a 2 s ceiling.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ralph.process.teardown import DefaultProcessTeardown, register_child_session

# Real child + real zombie state: a fake process cannot become a zombie.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(6)]

_CEILING_MS = 1500.0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX zombie semantics")
def test_teardown_of_unreaped_child_stays_within_one_ceiling() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        assert register_child_session(child.pid) == child.pid
        teardown = DefaultProcessTeardown(
            kill_escalation_ms=_CEILING_MS, record_lookup=lambda _p: None
        )

        start = time.monotonic()
        teardown.teardown_subtree(child.pid, issuer="test")
        elapsed = time.monotonic() - start

        assert elapsed < _CEILING_MS / 1000.0, (
            f"teardown took {elapsed:.2f}s against a {_CEILING_MS / 1000.0:.1f}s ceiling"
        )
        # The zombie is left for its owner: Popen still collects the real
        # SIGTERM status instead of the ECHILD-induced 0.
        assert child.wait(timeout=1.0) == -15
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=1.0)
