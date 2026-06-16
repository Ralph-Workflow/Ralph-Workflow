"""Consolidation tests for role classifier and discovery strategy.

These tests verify that the role classifier and discovery strategy are consolidated
into a single conservative implementation for all transports.
"""

from __future__ import annotations

import pytest

import ralph.process.monitor._role_classifier as rc_module
from ralph.config.enums import AgentTransport
from ralph.process.monitor._discovery_strategy import NullDiscoveryStrategy
from ralph.process.monitor._process_monitor import ProcessRole
from ralph.process.monitor._role_classifier import (
    role_classifier_for_transport,
)


class TestRoleClassifierConsolidation:
    """Test that role classification is consolidated to the conservative implementation."""

    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_role_classifier_for_transport_returns_conservative_for_every_transport(
        self, transport: AgentTransport
    ) -> None:
        """Every transport should use the conservative role classifier."""
        classifier = role_classifier_for_transport(transport)
        result = classifier(12345, ["test", "command"])
        assert result == ProcessRole.INCIDENTAL_HELPER

    def test_only_conservative_role_classifier_function_exists(self) -> None:
        """Only the conservative role classifier function should exist in the module."""
        classifier_functions = [
            name
            for name in dir(rc_module)
            if not name.startswith("__")
            and callable(getattr(rc_module, name))
            and name.startswith("_")
        ]

        expected = {"_conservative_role_classifier"}
        assert set(classifier_functions) == expected, (
            f"Expected only {expected}, got {set(classifier_functions)}"
        )


class TestDiscoveryStrategyConsolidation:
    """Test that discovery strategy returns empty for all implementations."""

    def test_null_discovery_strategy_returns_empty(self) -> None:
        """NullDiscoveryStrategy should return an empty dict for any host_pid."""
        strategy = NullDiscoveryStrategy()
        result = strategy.discover_subagent_outputs(host_pid=12345)
        assert result == {}
