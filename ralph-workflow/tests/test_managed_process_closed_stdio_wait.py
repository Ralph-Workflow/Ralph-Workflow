"""Regression: closing stdio is not exiting -- the post-EOF wait obeys the caller's budget.

Observed 2026-10-30. ``communicate_and_cleanup(output_limit_bytes=...)`` (the
MCP exec tool's path) waited only ``0.5 s`` for the child after its
stdout/stderr readers hit EOF. A child that closes its stdio and keeps
working (``exec >/dev/null 2>&1``, a daemonizing wrapper) overran that window,
and the resulting ``TimeoutExpired`` reached the caller's timeout handler: the
child was killed and a timeout was reported although neither ``timeout`` nor
``hard_timeout`` was anywhere near reached.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ralph.process.manager import SpawnOptions, get_process_manager

# Real child with real pipes: the defect is reader EOF arriving long before
# process exit, which only a real process exhibits.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(6)]

_QUIET_WORK_S = 1.0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell redirection")
def test_child_that_closes_stdio_is_waited_for_within_budget() -> None:
    pm = get_process_manager()
    handle = pm.spawn(
        [
            "/bin/sh",
            "-c",
            f"echo starting; exec >/dev/null 2>&1; sleep {_QUIET_WORK_S}; exit 3",
        ],
        SpawnOptions(stdout=subprocess.PIPE, stderr=subprocess.PIPE, label="mcp-exec:sh"),
    )

    stdout, stderr = handle.communicate_and_cleanup(
        timeout=30, output_limit_bytes=1_000_000, hard_timeout=60
    )

    assert stdout == b"starting\n"
    assert stderr == b""
    assert handle.returncode == 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell redirection")
def test_post_eof_wait_still_honours_the_inactivity_timeout() -> None:
    """The fix must not turn the post-EOF wait into an unbounded one."""
    pm = get_process_manager()
    handle = pm.spawn(
        ["/bin/sh", "-c", "echo starting; exec >/dev/null 2>&1; exec sleep 30"],
        SpawnOptions(stdout=subprocess.PIPE, stderr=subprocess.PIPE, label="mcp-exec:sh"),
    )
    start = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        handle.communicate_and_cleanup(timeout=0.6, output_limit_bytes=1_000_000, hard_timeout=60)

    elapsed = time.monotonic() - start
    assert 0.5 <= elapsed < 3.0, f"inactivity timeout 0.6s fired after {elapsed:.2f}s"
    assert excinfo.value.output == b"starting\n"
    assert handle.returncode is not None
