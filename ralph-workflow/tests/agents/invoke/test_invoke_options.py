"""Tests for mapping invocation options and timeout policy flags."""

from __future__ import annotations

from ralph.agents.invoke._options import (
    InvokeRuntimeOptions,
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig


def test_build_invoke_options_maps_new_monitor_flags() -> None:
    """New process-monitor and output-capture flags flow from GeneralConfig."""
    config = GeneralConfig(
        agent_process_monitor_enabled=False,
        agent_subagent_output_capture_enabled=False,
        agent_subagent_output_poll_interval_seconds=2.5,
    )
    opts = build_invoke_options_from_config(config)
    assert opts.process_monitor_enabled is False
    assert opts.subagent_output_capture_enabled is False
    assert opts.subagent_output_poll_interval_seconds == 2.5


def test_policy_from_options_maps_new_monitor_flags() -> None:
    """TimeoutPolicy receives the new monitor/output-capture flags."""
    opts = build_invoke_options_from_config(
        GeneralConfig(
            agent_process_monitor_enabled=False,
            agent_subagent_output_capture_enabled=False,
            agent_subagent_output_poll_interval_seconds=2.5,
        )
    )
    policy = _policy_from_options(opts)
    assert policy.process_monitor_enabled is False
    assert policy.subagent_output_capture_enabled is False
    assert policy.subagent_output_poll_interval_seconds == 2.5


def test_invoke_runtime_options_pass_through() -> None:
    """InvokeRuntimeOptions are preserved when building InvokeOptions."""
    runtime = InvokeRuntimeOptions(show_progress=False, session_id="abc")
    opts = build_invoke_options_from_config(GeneralConfig(), runtime)
    assert opts.show_progress is False
    assert opts.session_id == "abc"


def test_agent_config_transport_inference_for_subagent() -> None:
    """AgentConfig infers transport from command name."""
    cfg = AgentConfig(cmd="opencode")
    assert cfg.transport == AgentTransport.OPENCODE
