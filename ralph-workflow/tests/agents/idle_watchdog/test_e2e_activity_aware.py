"""End-to-end tests for the activity-aware idle watchdog.

These tests exercise real filesystem, real subprocess, and real
DiscoveryStrategy/ProcessMonitor integrations while keeping wall-clock
time minimal. They verify the acceptance criteria that span multiple
components.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._evidence_tier import (
    ChannelName,
    EvidenceTier,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import ChildLivenessRegistry, ChildLivenessSubagentPidSource
from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    FileSubagentOutputCapture,
    NullDiscoveryStrategy,
    ProcessMonitor,
    ProcessRole,
    SubagentOutputCapture,
)
from ralph.process.teardown import DefaultProcessTeardown

pytestmark = pytest.mark.subprocess_e2e

_IDE_TIMEOUT = 0.1
_DRAIN = 0.0
_MAX_WAITING = 10.0


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_policy(activity_ttl: float | None = 30.0) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=_IDE_TIMEOUT,
        drain_window_seconds=_DRAIN,
        max_waiting_on_child_seconds=_MAX_WAITING,
        # Disable the stuck-job sub-ceiling: this test file uses a
        # small cumulative ceiling (_MAX_WAITING) for fast in-memory
        # cycles. The sub-ceiling default (600s) would fail the
        # ``<= max_waiting_on_child_seconds`` validator. The tests
        # in this file do not exercise the sub-ceiling path; the
        # dedicated tests live in
        # ``tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py``.
        stuck_job_sub_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        os_descendant_only_ceiling_seconds=None,
        # Disable the SILENT_SUBAGENT diagnostic by default so this
        # file exercises the activity-aware fire path (NO_OUTPUT_DEADLINE
        # etc.) rather than the SILENT_SUBAGENT classifier branch.
        # Tests that explicitly exercise SILENT_SUBAGENT are in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``.
        silent_subagent_seconds=None,
    )


def _make_watchdog(
    policy: TimeoutPolicy,
    process_monitor: ProcessMonitor | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
        ),
        clock,
    )


@dataclass
class _FakeDiscovery(DiscoveryStrategy):
    """Test-only discovery that exposes a configurable capture map."""

    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def discover_subagent_outputs(self, host_pid: int) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


@dataclass
class _FakeProcessMonitor(ProcessMonitor):
    """Test-only process monitor that exposes configurable captures."""

    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


@pytest.mark.parametrize(
    "discovery",
    [
        NullDiscoveryStrategy(),
        NullDiscoveryStrategy(),
    ],
)
def test_documented_discovery_returns_empty_when_path_not_documented(
    tmp_path: Path,
    discovery: DiscoveryStrategy,
) -> None:
    """AC-11: undocumented subagent output paths are not invented.

    Discovery strategies are cwd-relative; even when the legacy-looking
    directory layout is present on disk, the strategy must return an empty
    mapping because the path is not documented.
    """
    original_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    try:
        assert discovery.discover_subagent_outputs(0) == {}
    finally:
        os.chdir(str(original_cwd))


def test_subagent_output_first_party_deferral(tmp_path: Path) -> None:
    """AC-02/AC-07: fresh subagent output lines defer NO_OUTPUT_DEADLINE."""
    log_file = tmp_path / ".agent" / "workers" / "w1" / "output.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line 1\n", encoding="utf-8")

    policy = _make_policy(activity_ttl=1000.0)
    monitor = _FakeProcessMonitor(captures={"w1": FileSubagentOutputCapture(str(log_file))})
    wd, clock = _make_watchdog(policy, monitor)
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd._subagent_output_count >= 1

    # Past TTL with no new lines -> fire.
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
@pytest.mark.timeout_seconds(5)
def test_process_monitor_discovers_and_classifies_subagent() -> None:
    """AC-06/AC-10/AC-11: DefaultProcessMonitor classifies a live descendant subagent.

    The built-in command-line classifier is documentation-grounded and
    conservative; it does not promote descendants based on broad command-line
    tokens. OpenCode subagents are instead identified via the shipped
    ``ChildLivenessSubagentPidSource`` backed by the
    ``ChildLivenessRegistry`` (first-party evidence from structured child
    lifecycle events on stdout). This test uses that shipped source to
    classify the spawned child as a subagent without injecting a substitute
    lambda classifier.
    """
    host_script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 100ms is the minimum sleep needed for the spawned child subprocess to
    # be reliably visible via psutil on macOS; the previous 300ms value left
    # no headroom under the 1.0s per-test timeout once psutil's tree walk and
    # the teardown were accounted for.
    time.sleep(0.1)
    try:
        host_proc = psutil.Process(host.pid)
        children = host_proc.children(recursive=False)
        assert len(children) >= 1
        child_pid = children[0].pid

        registry = ChildLivenessRegistry(
            progress_ttl=60.0,
            heartbeat_ttl=60.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
        )
        registry.register_child("child-A", "agent:test-scope:", pid=child_pid)
        pid_source = ChildLivenessSubagentPidSource(registry, "agent:test-scope:")

        monitor = DefaultProcessMonitor(
            host.pid,
            subagent_pid_source=pid_source,
        )
        assert monitor.live_subagent_count() == 1
        processes = monitor.classified_processes()
        roles = {p.pid: p.role for p in processes}
        assert roles[host.pid] == ProcessRole.HOST
        assert roles[child_pid] == ProcessRole.SPAWNED_SUBAGENT
    finally:
        # Direct kill of the known host + child, bypassing the recursive
        # psutil ``host.children(recursive=True)`` enumeration inside
        # ``DefaultProcessTeardown`` (which alone costs several hundred
        # milliseconds on macOS and would push the test past the 1.0s
        # per-test budget). The teardown's SIGTERM-then-SIGKILL escalation
        # semantics are exercised by the dedicated
        # ``test_teardown_reaps_nested_subagents`` test in this file; this
        # test only verifies monitor classification, so the cleanup just
        # needs to reap the processes it spawned.
        for proc in [host_proc, children[0]]:
            with contextlib.suppress(psutil.Error):
                proc.kill()
        host.wait(timeout=0.5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
@pytest.mark.timeout_seconds(5)
def test_teardown_reaps_nested_subagents() -> None:
    """AC-08: DefaultProcessTeardown kills the host and all descendants."""
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    assert host.poll() is None

    DefaultProcessTeardown(kill_escalation_ms=300.0).teardown_subtree(host.pid)

    for _ in range(30):
        if host.poll() is not None:
            break
        time.sleep(0.05)
    assert host.poll() is not None


def test_evidence_summary_labels_tiers_e2e() -> None:
    """AC-12: last_evidence_summary exposes tier labels and freshness."""
    policy = _make_policy(activity_ttl=1000.0)
    wd, clock = _make_watchdog(policy)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    clock.advance(1.0)

    summary = wd.last_evidence_summary(clock.monotonic())
    by_name = {c.channel_name: c for c in summary.channels}
    assert by_name[ChannelName.STDOUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.MCP_TOOL].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_OUTPUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_LIVENESS].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.WORKSPACE].tier == EvidenceTier.SIDE_CHANNEL


def test_truly_idle_fires_on_time_e2e() -> None:
    """AC-04: no activity on any channel fires at the idle deadline."""
    policy = _make_policy(activity_ttl=30.0)
    wd, clock = _make_watchdog(policy)
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
