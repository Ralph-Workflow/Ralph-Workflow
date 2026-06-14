"""Factory helpers for constructing process-monitor and discovery dependencies.

These helpers live in their own module so both the subprocess and PTY readers
can import them without creating a circular import through
``ralph.agents.invoke``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.process.monitor import (
    ClaudeCodeSubagentOutputDiscovery,
    DefaultProcessMonitor,
    DiscoveryStrategy,
    OpencodeSubagentOutputDiscovery,
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
    transport = config.transport
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return ClaudeCodeSubagentOutputDiscovery()
    if transport == AgentTransport.OPENCODE:
        return OpencodeSubagentOutputDiscovery()
    return None


def _make_process_monitor(
    handle: ManagedProcess | ManagedPtyProcess,
    policy: TimeoutPolicy,
) -> DefaultProcessMonitor | None:
    """Construct a DefaultProcessMonitor when the policy enables it."""
    if not policy.process_monitor_enabled:
        return None
    return DefaultProcessMonitor(
        handle.pid,
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
