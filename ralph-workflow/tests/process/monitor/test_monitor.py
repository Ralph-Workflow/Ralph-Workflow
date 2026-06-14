"""Tests for the process monitor module."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ralph.process.monitor import DefaultProcessMonitor, ProcessRole

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.skipif(sys.platform == "win32", reason="psutil cross-platform tests")
def test_default_monitor_classifies_subagent_by_cmdline() -> None:
    """DefaultProcessMonitor classifies descendant processes by role."""
    # Host process spawns a child that sleeps; the child is a descendant.
    host_script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)

    try:
        monitor = DefaultProcessMonitor(host.pid, poll_interval_seconds=0.0)
        classified = monitor.classified_processes()
        # The host has at least one descendant; classify any python descendant as subagent.
        pids = {p.pid for p in classified}
        assert pids
        monitor_with_role = DefaultProcessMonitor(
            host.pid,
            role_classifier=lambda _pid, cmdline: (
                ProcessRole.SPAWNED_SUBAGENT
                if cmdline and "time.sleep(600)" in " ".join(cmdline)
                else ProcessRole.INCIDENTAL_HELPER
            ),
            poll_interval_seconds=0.0,
        )
        assert monitor_with_role.live_subagent_count() >= 1
    finally:
        host.terminate()
        try:
            host.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=1.0)


def test_default_monitor_includes_host_classification() -> None:
    """The host process itself is classified with ProcessRole.HOST."""
    host_script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)

    try:
        monitor = DefaultProcessMonitor(
            host.pid,
            role_classifier=lambda _pid, cmdline: (
                ProcessRole.SPAWNED_SUBAGENT
                if cmdline and "time.sleep(600)" in " ".join(cmdline)
                else ProcessRole.INCIDENTAL_HELPER
            ),
            poll_interval_seconds=0.0,
        )
        classified = monitor.classified_processes()
        roles = {p.pid: p.role for p in classified}
        assert roles[host.pid] == ProcessRole.HOST
        assert any(
            p.role == ProcessRole.SPAWNED_SUBAGENT
            for p in classified
            if p.pid != host.pid
        )
    finally:
        host.terminate()
        try:
            host.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=1.0)


def test_default_monitor_handles_missing_host() -> None:
    """A missing host process results in zero live subagents and no host entry."""
    monitor = DefaultProcessMonitor(999_999, poll_interval_seconds=0.0)
    assert monitor.live_subagent_count() == 0
    assert monitor.classified_processes() == ()
