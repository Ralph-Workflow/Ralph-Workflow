"""Dispatch contract for discovery strategy across all AgentTransport members.

Per-transport discovery contract:

* **OpenCode** with a registry: the factory returns
  :class:`OpenCodeRegistryDiscoveryStrategy` so a per-child
  :class:`RegistryBackedSubagentOutputCapture` can surface real-time
  progress, heartbeat, and terminal events.
* **OpenCode** without a registry: the factory degrades to
  :class:`NullDiscoveryStrategy` (the watchdog must not invent a
  registry it does not have).
* **Claude / Claude-interactive / Codex / Nanocoder / Generic / Agy / Pi**:
  the factory returns :class:`NullDiscoveryStrategy` because these
  transports do not document a stable per-worker subagent output log
  path. Real-time subagent visibility flows through the cross-transport
  subagent activity sink (:meth:`IdleWatchdog.record_subagent_work`).

This test pins the dispatch contract independently of
``tests/agents/invoke/test_invoke_monitor_wiring.py``, which intentionally
excludes ``AgentTransport.PI`` from its parametrization.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._monitor_factory import _discovery_strategy_for_config
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.monitor import NullDiscoveryStrategy, OpenCodeRegistryDiscoveryStrategy

_NON_OPENCODE_TRANSPORTS: tuple[AgentTransport, ...] = tuple(
    t for t in AgentTransport if t is not AgentTransport.OPENCODE
)


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


@pytest.mark.parametrize("transport", list(_NON_OPENCODE_TRANSPORTS))
def test_discovery_strategy_is_null_for_non_opencode_transport(
    transport: AgentTransport,
) -> None:
    """Non-OpenCode transports always receive ``NullDiscoveryStrategy``."""
    config = AgentConfig(
        cmd=transport.value,
        transport=transport,
    )
    strategy = _discovery_strategy_for_config(config, registry=_make_registry())
    assert isinstance(strategy, NullDiscoveryStrategy)


def test_discovery_strategy_is_registry_backed_for_opencode_with_registry() -> None:
    """OpenCode + registry receives ``OpenCodeRegistryDiscoveryStrategy``."""
    config = AgentConfig(
        cmd=AgentTransport.OPENCODE.value,
        transport=AgentTransport.OPENCODE,
    )
    registry = _make_registry()
    strategy = _discovery_strategy_for_config(
        config, registry=registry, scope_prefix="agent:test-scope:"
    )
    assert isinstance(strategy, OpenCodeRegistryDiscoveryStrategy)


def test_discovery_strategy_is_null_for_opencode_without_registry() -> None:
    """OpenCode without a registry degrades to ``NullDiscoveryStrategy``."""
    config = AgentConfig(
        cmd=AgentTransport.OPENCODE.value,
        transport=AgentTransport.OPENCODE,
    )
    strategy = _discovery_strategy_for_config(config, registry=None)
    assert isinstance(strategy, NullDiscoveryStrategy)
