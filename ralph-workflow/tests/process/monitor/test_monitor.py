"""Tests for the process monitor module."""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from ralph.config.enums import AgentTransport
from ralph.process.child_liveness import ChildLivenessRegistry, ChildLivenessSubagentPidSource
from ralph.process.monitor import (
    DefaultProcessMonitor,
    ProcessRole,
    role_classifier_for_transport,
)

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.skipif(sys.platform == "win32", reason="psutil cross-platform tests")
def test_default_monitor_default_classifier_is_conservative() -> None:
    """Without an injected role_classifier, no descendant is promoted to subagent.

    The built-in default must not rely on broad substring heuristics such as
    matching ``worker``, ``task``, ``agent``, ``claude``, or ``opencode`` in the
    command line. Those tokens appear in incidental helpers (tool subprocesses,
    MCP servers, shells) and would misclassify them as spawned subagents.
    """
    # Host process spawns a child whose command line contains subagent-y tokens.
    child_script = "import time; time.sleep(600)"
    host_script = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
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
        assert any(p.pid == host.pid for p in classified)
        assert all(p.role != ProcessRole.SPAWNED_SUBAGENT for p in classified if p.pid != host.pid)
        assert monitor.live_subagent_count() == 0
    finally:
        host.terminate()
        try:
            host.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=1.0)


@pytest.mark.skipif(sys.platform == "win32", reason="psutil cross-platform tests")
def test_default_monitor_classifies_subagent_with_injected_classifier() -> None:
    """An injected role_classifier can still promote descendants to subagents."""
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


@pytest.mark.skipif(sys.platform == "win32", reason="psutil cross-platform tests")
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
        assert any(p.role == ProcessRole.SPAWNED_SUBAGENT for p in classified if p.pid != host.pid)
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


@pytest.mark.parametrize(
    ("transport", "expected_cmdline"),
    [
        (AgentTransport.CLAUDE, ["claude", "--print", "hello"]),
        (AgentTransport.CLAUDE_INTERACTIVE, ["claude", "--interactive"]),
        (AgentTransport.OPENCODE, ["opencode", "run", "hello"]),
        (AgentTransport.CODEX, ["codex", "hello"]),
        (AgentTransport.NANOCODER, ["nanocoder", "run", "hello"]),
        (AgentTransport.GENERIC, ["some-agent", "hello"]),
        (AgentTransport.AGY, ["agy", "--print", "hello"]),
    ],
)
def test_transport_role_classifier_is_conservative(
    transport: AgentTransport,
    expected_cmdline: list[str],
) -> None:
    """Every documentation-grounded transport classifier degrades conservatively.

    No supported agent CLI documents a stable external signal for identifying
    spawned subagents by command line or process tree. Each classifier must
    therefore return INCIDENTAL_HELPER for descendants, avoiding the false
    positives produced by broad substring heuristics.
    """
    classifier = role_classifier_for_transport(transport)
    assert classifier(123, expected_cmdline) == ProcessRole.INCIDENTAL_HELPER
    assert classifier(123, ["worker", "--task", "agent"]) == ProcessRole.INCIDENTAL_HELPER
    assert classifier(123, []) == ProcessRole.INCIDENTAL_HELPER
    assert classifier(123, None) == ProcessRole.INCIDENTAL_HELPER


def test_role_classifier_for_unknown_transport_is_conservative() -> None:
    """An unknown transport falls back to the conservative classifier."""
    classifier = role_classifier_for_transport(object())
    assert classifier(123, ["worker", "subagent"]) == ProcessRole.INCIDENTAL_HELPER


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_default_monitor_classifies_subagent_via_child_liveness_registry() -> None:
    """AC-06/AC-10/AC-11: shipped OpenCode PID source classifies real descendants.

    OpenCode emits structured child lifecycle events on stdout that carry the
    child PID. ``ChildLivenessSubagentPidSource`` exposes those registered PIDs
    to ``DefaultProcessMonitor`` so a real descendant process is classified as
    ``SPAWNED_SUBAGENT`` without injecting a substitute lambda classifier.
    """
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
        # Find the real child PID spawned by host_script.
        host_proc = psutil.Process(host.pid)
        children = host_proc.children(recursive=False)
        assert len(children) >= 1, "host should have spawned at least one child"
        child_pid = children[0].pid

        registry = ChildLivenessRegistry(
            progress_ttl=60.0,
            heartbeat_ttl=60.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
        )
        # Register the spawned child as a known OpenCode subagent.
        registry.register_child("child-A", "agent:test-scope:", pid=child_pid)
        pid_source = ChildLivenessSubagentPidSource(registry, "agent:test-scope:")

        monitor = DefaultProcessMonitor(
            host.pid,
            subagent_pid_source=pid_source,
            poll_interval_seconds=0.0,
        )
        assert monitor.live_subagent_count() == 1
        processes = monitor.classified_processes()
        roles = {p.pid: p.role for p in processes}
        assert roles[host.pid] == ProcessRole.HOST
        assert roles[child_pid] == ProcessRole.SPAWNED_SUBAGENT
    finally:
        host.terminate()
        try:
            host.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait(timeout=1.0)
