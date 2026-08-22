"""Production-profile regressions for conflict-resolution supervision (S-2)."""

from __future__ import annotations

import os

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.invoke import AgentRunCtx, InvokeOptions, policy_from_options
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.server._activity_sink import reset_active_sink, reset_subagent_sink


@pytest.mark.parametrize("recorder", ["record_activity", "record_mcp_tool_call", "record_subagent_work"])
def test_conflict_resolution_regression_profile_uses_each_direct_activity_channel(
    recorder: str,
) -> None:
    """S-2/R3: every direct production activity recorder resets conflict liveness."""
    clock = FakeClock()
    policy = policy_from_options(
        InvokeOptions(
            idle_timeout_seconds=900.0,
            activity_only_supervision=True,
            max_session_seconds=1.0,
            max_waiting_on_child_seconds=1.0,
        )
    )
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_invocation_start()

    for _ in range(4):
        clock.advance(899.0)
        getattr(watchdog, recorder)()
        assert watchdog.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.CONTINUE


def test_conflict_resolution_regression_workspace_activity_resets_production_profile() -> None:
    """S-2/R3: weighted workspace-only work has the same liveness effect."""
    clock = FakeClock()
    policy = policy_from_options(
        InvokeOptions(idle_timeout_seconds=900.0, activity_only_supervision=True)
    )
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_invocation_start()

    clock.advance(899.0)
    watchdog.record_workspace_event()

    assert watchdog.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.CONTINUE


def test_conflict_resolution_regression_operator_cap_preempts_fresh_activity() -> None:
    """S-2/R6: a configured cap has its own typed verdict while work remains active."""
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=900.0,
        profile=TimeoutProfile.ACTIVITY_ONLY,
        activity_only_operator_cap_seconds=60.0,
    )
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_invocation_start()
    clock.advance(60.0)
    watchdog.record_mcp_tool_call()

    assert watchdog.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason.value == "operator_cap_reached"


class _PtyHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pid = 1

    def poll(self) -> int:
        return 0

    def terminate(self, grace_period_s: float = 0.5) -> None:
        del grace_period_s

    def close(self) -> None:
        return None


def test_conflict_resolution_regression_pty_registers_relay_sink_for_mcp_liveness() -> None:
    """S-3/R3: a PTY resolver transports standalone MCP activity to its watchdog."""
    read_fd, write_fd = os.pipe()
    registered: list[object] = []
    try:
        ctx = AgentRunCtx(
            config=AgentConfig(cmd="resolver", transport=AgentTransport.CLAUDE_INTERACTIVE),
            show_progress=False,
            extra_env=None,
            workspace_path=None,
            policy=TimeoutPolicy(
                idle_timeout_seconds=900.0,
                profile=TimeoutProfile.ACTIVITY_ONLY,
            ),
            relay_activity_sink_register=lambda sink: registered.append(sink) or (lambda: None),
        )
        reader = PtyLineReader(_PtyHandle(read_fd), "resolver", ctx, FakeClock(), extras=None)
        watchdog = IdleWatchdog(ctx.policy, FakeClock())

        sink_token, subagent_token, _ = reader._register_active_sinks(watchdog)
        try:
            assert len(registered) == 1
            registered[0]("read_file")
            assert watchdog.diagnostic_snapshot(now=0.0)["mcp_tool_call_count"] == 1
        finally:
            reader._remove_relay_sink and reader._remove_relay_sink()
            reset_active_sink(sink_token)
            reset_subagent_sink(subagent_token)
            os.close(reader._input_writer_fd)
            os.close(reader._read_fd)
    finally:
        os.close(write_fd)


def test_conflict_resolution_regression_status_is_rate_limited_with_activity_metadata() -> None:
    """S-2/R8: activity-only supervision emits low-cadence observable status."""
    events = []
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=900.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
            activity_only_status_interval_seconds=30.0,
        ),
        clock,
        listener=events.append,
    )
    watchdog.record_invocation_start()
    watchdog.record_mcp_tool_call()
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    clock.advance(10.0)
    watchdog.record_workspace_event()
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    clock.advance(20.0)
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)

    assert len(events) == 2
    assert events[-1].diagnostic["last_activity_kind"] == "workspace"
    assert events[-1].diagnostic["last_activity_age_seconds"] == 20.0


@pytest.mark.parametrize(
    ("recorder", "expected_kind"),
    [
        ("record_activity", "stdout"),
        ("record_mcp_tool_call", "mcp_tool"),
        ("record_subagent_work", "subagent_output"),
        ("record_workspace_event", "workspace"),
    ],
)
def test_conflict_resolution_regression_initial_activity_keeps_its_provenance(
    recorder: str,
    expected_kind: str,
) -> None:
    """S-2/R7: a first-tick event is not misreported as baseline stdout."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    getattr(watchdog, recorder)()
    clock.advance(10.0)

    assert watchdog.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.FIRE
    assert watchdog.activity_only_snapshot() == (expected_kind, 0.0)
    assert watchdog.diagnostic_snapshot(now=10.0)["last_activity_kind"] == expected_kind
