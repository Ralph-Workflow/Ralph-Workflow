"""Dispatch contract for discovery strategy across all AgentTransport members.

No transport currently documents a stable per-worker subagent output log path;
visibility for subagent activity comes from stdout forwarding, MCP tool-call
events, and the OpenCode ``_subagent_activity_sink``. ``NullDiscoveryStrategy``
is the documented default for every transport.

This test pins the dispatch contract independently of
``tests/agents/invoke/test_invoke_monitor_wiring.py``, which intentionally
excludes ``AgentTransport.PI`` from its parametrization.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._monitor_factory import _discovery_strategy_for_config
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.monitor import NullDiscoveryStrategy


@pytest.mark.parametrize("transport", list(AgentTransport.__members__.values()))
def test_discovery_strategy_is_null_for_every_transport(
    transport: AgentTransport,
) -> None:
    """_discovery_strategy_for_config returns NullDiscoveryStrategy for all transports."""
    config = AgentConfig(
        cmd=transport.value,
        transport=transport,
    )
    strategy = _discovery_strategy_for_config(config)
    assert isinstance(strategy, NullDiscoveryStrategy)
