"""Factory helpers for constructing process-monitor and discovery dependencies.

These helpers live in their own module so both the subprocess and PTY readers
can import them without creating a circular import through
``ralph.agents.invoke``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    SubagentPidSource,
    role_classifier_for_transport,
)

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import TimeoutPolicy
    from ralph.config.models import AgentConfig
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


def _discovery_strategy_for_config(config: AgentConfig) -> DiscoveryStrategy | None:
    """Return the agent-specific discovery strategy, if any.

    The mapping is intentionally conservative: only transports whose
    subagent-output location is documented get a strategy. For all other
    agents the watchdog degrades gracefully to stdout/MCP/workspace evidence.
    """
    return NullDiscoveryStrategy()


def _make_process_monitor(
    handle: ManagedProcess | ManagedPtyProcess,
    config: AgentConfig,
    policy: TimeoutPolicy,
    subagent_pid_source: SubagentPidSource | None = None,
) -> DefaultProcessMonitor | None:
    """Construct a DefaultProcessMonitor when the policy enables it."""
    if not policy.process_monitor_enabled:
        return None
    discovery = _make_discovery_strategy(config, policy)
    return DefaultProcessMonitor(
        handle.pid,
        role_classifier=role_classifier_for_transport(config.transport),
        discovery_strategy=discovery,
        subagent_pid_source=subagent_pid_source,
        poll_interval_seconds=policy.subagent_output_poll_interval_seconds,
    )


def _make_discovery_strategy(
    config: AgentConfig,
    policy: TimeoutPolicy,
) -> DiscoveryStrategy | None:
    """Construct a discovery strategy when the policy enables output capture."""
    if not policy.subagent_output_capture_enabled:
        return None
    return _discovery_strategy_for_config(config)
