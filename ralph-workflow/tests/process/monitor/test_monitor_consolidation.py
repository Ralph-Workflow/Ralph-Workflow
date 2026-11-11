from __future__ import annotations

import pytest

from ralph.config.enums import AgentTransport
from ralph.process.monitor import NullDiscoveryStrategy, ProcessRole, role_classifier_for_transport


class TestRoleClassifierConsolidation:
    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_role_classifier_for_transport_returns_conservative_for_every_transport(
        self, transport: AgentTransport
    ) -> None:
        classifier = role_classifier_for_transport(transport)
        result = classifier(12345, ["test", "command"])
        assert result == ProcessRole.INCIDENTAL_HELPER

    def test_only_conservative_role_classifier_function_exists(self) -> None:
        classifiers = {role_classifier_for_transport(transport) for transport in AgentTransport}

        assert len(classifiers) == 1, (
            f"expected every transport to share one classifier; got {len(classifiers)}"
        )
        (classifier,) = classifiers
        expected = "_conservative_role_classifier"
        assert classifier.__name__ == expected, (
            f"Expected only {expected}, got {classifier.__name__}"
        )


class TestDiscoveryStrategyConsolidation:
    def test_null_discovery_strategy_returns_empty(self) -> None:
        strategy = NullDiscoveryStrategy()
        result = strategy.discover_subagent_outputs(host_pid=12345)
        assert result == {}
