"""Activity-aware idle watchdog tests with tier-aware evidence.

These tests exercise the dependency-injected watchdog with fake process
monitors and discovery strategies. They cover the acceptance criteria that
drive the first-party vs side-channel evidence distinction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)

_IDLE_TIMEOUT = 0.1
_DRAIN_WINDOW = 0.0
_MAX_WAITING = 10.0
_ACTIVITY_TTL = 30.0


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _make_policy(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = _ACTIVITY_TTL,
    silent_subagent_seconds: float | None = None,
) -> TimeoutPolicy:
    kwargs: dict[str, object] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": None,
        "max_waiting_on_child_no_progress_seconds": None,
        "activity_evidence_ttl_seconds": activity_ttl,
        "os_descendant_only_ceiling_seconds": None,
        # Disable the stuck-job sub-ceiling so this test file can use
        # a small ``max_waiting_on_child_seconds`` (10s) for fast
        # in-memory waiting-branch cycles without tripping the new
        # sub-ceiling default (600s).
        "stuck_job_sub_ceiling_seconds": None,
        # Disable the SILENT_SUBAGENT diagnostic by default so this
        # file exercises the activity-aware fire path (NO_OUTPUT_DEADLINE
        # etc.) rather than the SILENT_SUBAGENT classifier branch.
        # Tests that explicitly exercise SILENT_SUBAGENT are in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``.
        "silent_subagent_seconds": silent_subagent_seconds,
    }
    return TimeoutPolicy(**kwargs)


def _make_watchdog(
    policy: TimeoutPolicy | None = None,
    *,
    start: float = 0.0,
    process_monitor: ProcessMonitor | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    policy = policy if policy is not None else _make_policy()
    clock = FakeClock(start=start)
    return (
        IdleWatchdog(
            policy,
            clock,
            process_monitor=process_monitor,
        ),
        clock,
    )


@dataclass
class FakeProcessMonitor(ProcessMonitor):
    """Fake process monitor for black-box tests."""

    live_count: int = 0
    classified: tuple = ()
    captures: dict[str, SubagentOutputCapture] = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.live_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return dict(self.captures)


@dataclass
class FakeCapture(SubagentOutputCapture):
    """Fake subagent output capture that returns queued lines."""

    lines: list[list[str]] = field(default_factory=list)
    call_count: int = 0

    def read_lines(self, worker_id: str) -> list[str]:
        result = self.lines[self.call_count] if self.call_count < len(self.lines) else []
        self.call_count += 1
        return result


def test_first_party_mcp_tool_defers_no_output_deadline() -> None:
    """AC-1: MCP tool calls with quiet stdout defer NO_OUTPUT_DEADLINE."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(100.0)
    wd.record_mcp_tool_call()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_first_party_subagent_output_defers_no_output_deadline() -> None:
    """AC-2/AC-7: subagent output stream defers NO_OUTPUT_DEADLINE."""
    capture = FakeCapture(lines=[["hello from subagent"]])
    monitor = FakeProcessMonitor(captures={"worker-1": capture})
    wd, clock = _make_watchdog(
        _make_policy(activity_ttl=1000.0),
        process_monitor=monitor,
    )
    wd.record_activity()
    clock.advance(100.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE
    assert wd._subagent_output_count == 1

    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_first_party_subagent_progress_defers_no_output_deadline() -> None:
    """AC-2: explicit subagent progress signals defer NO_OUTPUT_DEADLINE."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE


def test_dead_subagent_detected_within_idle_window() -> None:
    """AC-3: silent subagent fires at idle deadline, not cumulative ceiling."""
    wd, clock = _make_watchdog(_make_policy())
    wd.record_activity()
    wd.record_subagent_work()
    clock.advance(31.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_truly_idle_fires_on_time() -> None:
    """AC-4: no activity on any channel fires at idle deadline."""
    wd, clock = _make_watchdog(_make_policy())
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_side_channel_workspace_source_defers_log_does_not() -> None:
    """AC-5: source workspace change defers; log change does not."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    assert wd.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE

    wd2, clock2 = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd2.record_activity()
    clock2.advance(1.0)
    wd2.record_workspace_event(kind=WorkspaceChangeKind.LOG, weight=0.0)
    assert wd2.evaluate(classify_quiet=_active) == WatchdogVerdict.FIRE


def test_bare_subagent_liveness_defers_fire() -> None:
    """AC-02 (smart-verdict): bare PID liveness defers the fire.

    The new design treats a live subagent without first-party evidence
    as the LOADING stuck kind: the classifier returns LOADING and the
    gate returns CONTINUE so a productive-but-quiet session is not
    killed. This replaces the OLD behavior where bare liveness was
    ignored and the watchdog fired at the cumulative child-wait
    ceiling regardless of whether the child was making progress.
    """
    monitor = FakeProcessMonitor(live_count=1)
    wd, clock = _make_watchdog(
        _make_policy(activity_ttl=1000.0),
        process_monitor=monitor,
    )
    wd.record_activity()
    clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE


def test_session_ceiling_unaffected_by_first_party_activity() -> None:
    """AC-13: session ceiling fires regardless of first-party activity."""
    wd, clock = _make_watchdog(_make_policy(max_session=5.0, activity_ttl=1000.0))
    for _ in range(6):
        wd.record_mcp_tool_call()
        clock.advance(1.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_cumulative_waiting_ceiling_unaffected_by_activity() -> None:
    """R3 contract (Trustworthy Idle Watchdog): the cumulative ceiling
    fires UNCONDITIONALLY regardless of fresh first-party activity.

    Per PROMPT R3: "There must be a hard, bounded ceiling after which a
    true hang fires regardless of deferral reasons." The cumulative
    waiting ceiling at ``_waiting_branch.py:238-247`` no longer
    consults ``_gate_fire``; it fires even when first-party channels
    (mcp_tool) are fresh within ``activity_evidence_ttl_seconds``.

    Pre-fix (wt-013 activity-aware): the gate deferred the fire when
    first-party channels were fresh. Post-fix (R3 hard enforcement):
    the cumulative ceiling fires regardless of mcp_tool freshness.

    Assertions:
      - verdict is FIRE at the cumulative ceiling regardless of
        fresh mcp_tool activity within ``activity_evidence_ttl_seconds``.
    """
    wd, clock = _make_watchdog(_make_policy(idle_timeout=0.1, max_waiting=2.0, activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(0.1)

    # The cumulative ceiling is 2.0s; advance the clock past it
    # in 0.1s increments while keeping the mcp_tool channel fresh
    # via ``record_mcp_tool_call``. Per R3 hard enforcement the
    # ceiling fires UNCONDITIONALLY regardless of mcp_tool
    # freshness.
    fire_observed = False
    for _ in range(30):
        wd.record_mcp_tool_call()
        verdict = wd.evaluate(classify_quiet=_waiting)
        clock.advance(0.1)
        if verdict == WatchdogVerdict.FIRE:
            fire_observed = True
            break

    # The cumulative ceiling MUST fire within 30 evaluate() calls
    # even with fresh mcp_tool activity.
    assert fire_observed, (
        "cumulative ceiling MUST fire unconditionally past the"
        " ceiling (R3 hard enforcement) regardless of mcp_tool"
        " freshness; never observed FIRE in 30 calls"
    )
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_evidence_summary_labels_tiers() -> None:
    """AC-12: evidence summary includes tier labels and deferral flags."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    clock.advance(1.0)

    summary = wd.last_evidence_summary(clock.monotonic())
    by_name = {c.channel_name: c for c in summary.channels}
    assert by_name[ChannelName.STDOUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.MCP_TOOL].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_OUTPUT].tier == EvidenceTier.FIRST_PARTY
    assert by_name[ChannelName.SUBAGENT_LIVENESS].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.WORKSPACE].tier == EvidenceTier.SIDE_CHANNEL
    assert by_name[ChannelName.SUBAGENT_LIVENESS].can_defer is False


def test_process_monitor_disabled_gracefully() -> None:
    """AC-10: when no process monitor is injected, liveness is unavailable."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    summary = wd.last_evidence_summary(clock.monotonic())
    liveness = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    assert liveness is not None
    assert liveness.last_at is None
    assert liveness.can_defer is False


def test_subagent_output_unavailable_when_no_process_monitor() -> None:
    """AC-10: when process monitor is None, subagent output is unavailable."""
    wd, clock = _make_watchdog(_make_policy(activity_ttl=1000.0))
    wd.record_activity()
    clock.advance(1.0)
    assert wd.poll_subagent_output() == 0
    output = wd.last_evidence_summary(clock.monotonic()).by_name(ChannelName.SUBAGENT_OUTPUT)
    assert output is not None
    assert output.last_at is None


def test_fire_diagnostic_includes_evidence_summary() -> None:
    """AC-12: fire diagnostic embeds per-channel evidence summary."""
    wd, clock = _make_watchdog(_make_policy())
    clock.advance(1.0)
    wd.evaluate(classify_quiet=_active)
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    summary = wd.last_evidence_summary(clock.monotonic())
    assert len(summary.channels) == 5
    assert all(
        c.tier in {EvidenceTier.FIRST_PARTY, EvidenceTier.SIDE_CHANNEL} for c in summary.channels
    )
