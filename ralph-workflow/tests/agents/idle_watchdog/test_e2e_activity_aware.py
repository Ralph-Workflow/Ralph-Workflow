"""End-to-end tests for the activity-aware idle watchdog.

These tests exercise real filesystem, real subprocess, and real
DiscoveryStrategy/ProcessMonitor integrations while keeping wall-clock
time minimal. They verify the acceptance criteria that span multiple
components.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
from ralph.process.monitor import (
    ClaudeCodeSubagentOutputDiscovery,
    DefaultProcessMonitor,
    OpencodeSubagentOutputDiscovery,
)
from ralph.process.teardown import DefaultProcessTeardown

if TYPE_CHECKING:
    from ralph.process.monitor import DiscoveryStrategy

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
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
    )


def _make_watchdog(
    policy: TimeoutPolicy,
    discovery_strategy: DiscoveryStrategy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=None,
            discovery_strategy=discovery_strategy,
        ),
        clock,
    )


@pytest.mark.parametrize(
    ("discovery", "log_path", "worker_id"),
    [
        (
            OpencodeSubagentOutputDiscovery(),
            ".agent/workers/w1/output.log",
            "w1",
        ),
        (
            ClaudeCodeSubagentOutputDiscovery(),
            ".claude/session/s1/worker-1/log.txt",
            "s1/worker-1",
        ),
    ],
)
def test_subagent_output_discovery_reads_new_lines(
    tmp_path: Path,
    discovery: DiscoveryStrategy,
    log_path: str,
    worker_id: str,
) -> None:
    """AC-07/AC-10/AC-11: observable subagent log files are discovered and read.

    Writing a new line to the documented log path advances the
    SUBAGENT_OUTPUT first-party channel and can defer NO_OUTPUT_DEADLINE.
    """
    log_file = tmp_path / log_path
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("hello subagent\n", encoding="utf-8")

    # Discovery strategies are cwd-relative; run from tmp_path.
    original_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    try:
        captures = discovery.discover_subagent_outputs(0)
        assert worker_id in captures, f"expected {worker_id} in {list(captures)}"
        assert captures[worker_id].read_lines(worker_id) == ["hello subagent"]
    finally:
        os.chdir(str(original_cwd))


def test_subagent_output_first_party_deferral(tmp_path: Path) -> None:
    """AC-02/AC-07: fresh subagent output lines defer NO_OUTPUT_DEADLINE."""
    log_file = tmp_path / ".agent" / "workers" / "w1" / "output.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line 1\n", encoding="utf-8")

    original_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    try:
        policy = _make_policy(activity_ttl=1000.0)
        wd, clock = _make_watchdog(policy, OpencodeSubagentOutputDiscovery())
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
    finally:
        os.chdir(str(original_cwd))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_process_monitor_discovers_and_classifies_subagent() -> None:
    """AC-06/AC-10: DefaultProcessMonitor classifies a live descendant subagent."""
    host_script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'subagent = True; import time; time.sleep(600)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(600)"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    try:
        monitor = DefaultProcessMonitor(host.pid)
        assert monitor.live_subagent_count() == 1
        processes = monitor.classified_processes()
        assert any(p.role.value == "spawned_subagent" for p in processes)
    finally:
        DefaultProcessTeardown(kill_escalation_ms=300.0).teardown_subtree(host.pid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
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
