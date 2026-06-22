"""Factory helpers for constructing process-monitor and discovery dependencies.

These helpers live in their own module so both the subprocess and PTY readers
can import them without creating a circular import through
``ralph.agents.invoke``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.process.monitor import (
    DefaultProcessMonitor,
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
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
    """Return the transport-specific discovery strategy, or a null fallback.

    The mapping is documentation-grounded:

    * **OpenCode** emits structured child lifecycle events on stdout that the
      ``OpenCodeExecutionStrategy`` ingests into a per-invocation
      ``ChildLivenessRegistry``. When a registry is provided for the
      ``OPENCODE`` transport, the factory wires
      :class:`OpenCodeRegistryDiscoveryStrategy` so a per-child
      :class:`RegistryBackedSubagentOutputCapture` can surface textual
      descriptions of progress / heartbeat / terminal events. When no
      registry is provided the factory degrades to
      :class:`NullDiscoveryStrategy` (the watchdog must not invent a
      registry it does not have).

    * **Claude / Claude-interactive / Codex / Nanocoder / Generic / Agy / Pi**
      do not document a stable per-worker subagent output log path. The
      factory returns :class:`NullDiscoveryStrategy` for these transports.
      Real-time subagent visibility flows through the cross-transport
      subagent activity sink (:meth:`IdleWatchdog.record_subagent_work`).

    Args:
        config: The agent configuration whose transport dictates the
            strategy choice.
        registry: Optional child-liveness registry. For ``OPENCODE``, when
            a registry is provided, the factory returns
            :class:`OpenCodeRegistryDiscoveryStrategy` wired to the
            registry and ``scope_prefix``; without a registry, the factory
            falls back to :class:`NullDiscoveryStrategy`.
        scope_prefix: The label scope prefix used to filter registry
            records. Only meaningful for ``OPENCODE`` with a registry.

    Returns:
        A documentation-grounded :class:`DiscoveryStrategy`. The factory
        never invents a path; transports without a documented per-worker
        subagent output log path always receive
        :class:`NullDiscoveryStrategy`.
    """
    if (
        config.transport == AgentTransport.OPENCODE
        and registry is not None
    ):
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
) -> DefaultProcessMonitor | None:
    """Construct a DefaultProcessMonitor when the policy enables it."""
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
    """Construct a discovery strategy when the policy enables output capture."""
    if not policy.subagent_output_capture_enabled:
        return None
    return _discovery_strategy_for_config(
        config, registry=registry, scope_prefix=scope_prefix
    )
