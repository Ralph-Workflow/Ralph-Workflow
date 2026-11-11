
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke._invoke_options import _PROCESS_MONITOR_FACTORY_OVERRIDE
from ralph.config.enums import AgentTransport
from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    ProcessMonitor,
    SubagentPidSource,
    role_classifier_for_transport,
)

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import TimeoutPolicy
    from ralph.config.models import AgentConfig
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


def _discovery_strategy_for_config(
    config: AgentConfig,
    *,
    registry: ChildLivenessRegistry | None = None,
    scope_prefix: str = "",
) -> DiscoveryStrategy:
    if config.transport == AgentTransport.OPENCODE and registry is not None:
        return OpenCodeRegistryDiscoveryStrategy(registry, scope_prefix)
    return NullDiscoveryStrategy()


def _make_process_monitor(
    handle: ManagedProcess | ManagedPtyProcess,
    config: AgentConfig,
    policy: TimeoutPolicy,
    subagent_pid_source: SubagentPidSource | None = None,
    *,
    registry: ChildLivenessRegistry | None = None,
    scope_prefix: str = "",
) -> ProcessMonitor | None:
    override = _PROCESS_MONITOR_FACTORY_OVERRIDE.get()
    if override is not None:
        return override(
            handle,
            config,
            policy,
            subagent_pid_source,
            registry=registry,
            scope_prefix=scope_prefix,
        )
    if not policy.process_monitor_enabled:
        return None
    discovery = _make_discovery_strategy(
        config, policy, registry=registry, scope_prefix=scope_prefix
    )
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
    *,
    registry: ChildLivenessRegistry | None = None,
    scope_prefix: str = "",
) -> DiscoveryStrategy | None:
    if not policy.subagent_output_capture_enabled:
        return None
    return _discovery_strategy_for_config(config, registry=registry, scope_prefix=scope_prefix)
