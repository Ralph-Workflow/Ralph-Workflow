from __future__ import annotations

import importlib

import pytest

from ralph.agents.execution_state._completion_mixin import CompletionEnforcingStrategy
from ralph.agents.execution_state._factory import strategy_for_transport
from ralph.agents.execution_state.generic_execution_strategy import (
    GenericExecutionStrategy,
)
from ralph.config.enums import AgentTransport


class TestAgyStrategyConsolidation:
    def test_agy_strategy_is_factory_composition(self) -> None:
        strategy = strategy_for_transport(AgentTransport.AGY)

        assert isinstance(strategy, CompletionEnforcingStrategy)
        assert isinstance(strategy, GenericExecutionStrategy)
        assert hasattr(strategy, "supports_completion_enforcement")
        assert strategy.supports_completion_enforcement() is True

    def test_agy_execution_strategy_class_no_longer_importable(self) -> None:
        with pytest.raises(ImportError):
            importlib.import_module("ralph.agents.execution_state.agy_execution_strategy")

    def test_generic_execution_strategy_remains_as_base(self) -> None:
        strategy = GenericExecutionStrategy()
        assert isinstance(strategy, GenericExecutionStrategy)
        assert isinstance(strategy, CompletionEnforcingStrategy) is False
