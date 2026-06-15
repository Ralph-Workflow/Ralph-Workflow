"""Black-box tests for the unified register_agent_support API.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import _PARSER_REGISTRY, AgentOutputLine, get_parser
from ralph.agents.registration import (
    _CUSTOM_TRANSPORT_STRATEGIES,
    _NAME_TRANSPORT_INDEX,
    get_registered_agent_support,
    register_agent_support,
)
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing.smoke_plumbing import (
    _count_parsed_events,
    _parser_key_for_config,
)

from ._registration_test_utils import _isolated_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeAgentParser:
    """Pass-through parser used to prove registration wiring."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class FakeAgentStrategy(BaseExecutionStrategy):
    """Minimal custom strategy that inherits all defaults."""


class FakeInteractiveAgentStrategy(BaseExecutionStrategy):
    """Minimal interactive strategy that inherits all defaults."""


class TestRegisterAgentSupport:
    """Unified API writes into parser, strategy, and agent-name registries."""

    def test_registers_headless_agent_round_trip(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            config = register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            assert isinstance(config, AgentConfig)
            assert config.transport == AgentTransport.GENERIC
            parser = get_parser("fake")
            assert isinstance(parser, FakeAgentParser)
            pair = get_registered_agent_support("fake")
            assert pair is not None
            assert isinstance(pair[0], FakeAgentParser)
            assert isinstance(pair[1], FakeAgentStrategy)
            assert "fake" in registry.agents
            assert registry.agents["fake"].transport == AgentTransport.GENERIC

    def test_fake_parser_yields_expected_output_lines(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            parser = get_parser("fake")
            parsed = list(parser.parse(iter(["hello", "world"])))
            assert len(parsed) == 2
            assert all(line.type == "output" for line in parsed)
            assert parsed[0].content == "hello"
            assert parsed[1].content == "world"

    def test_runtime_parser_selection_uses_registered_config(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            config = register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            assert _parser_key_for_config(config) == "fake"
            assert isinstance(get_parser(_parser_key_for_config(config)), FakeAgentParser)
            assert _count_parsed_events(config, list(iter(["hello", "world"]))) == 2

    def test_registers_interactive_agent_round_trip(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            config = register_agent_support(
                "fake-interactive",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeInteractiveAgentStrategy,
                agent_registry=registry,
                interactive=True,
            )

            assert isinstance(config, AgentConfig)
            assert config.transport == AgentTransport.CLAUDE_INTERACTIVE
            assert config.session_flag is not None
            parser = get_parser("fake-interactive")
            assert isinstance(parser, FakeAgentParser)
            pair = get_registered_agent_support("fake-interactive")
            assert pair is not None
            assert isinstance(pair[1], FakeInteractiveAgentStrategy)
            assert "fake-interactive" in registry.agents

    def test_independent_registrations_do_not_cross_contaminate(self) -> None:
        with _isolated_registries():
            registry_one = AgentRegistry()
            registry_two = AgentRegistry()

            register_agent_support(
                "fake-one",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry_one,
            )
            register_agent_support(
                "fake-two",
                transport=AgentTransport.CODEX,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeInteractiveAgentStrategy,
                agent_registry=registry_two,
            )

            pair_one = get_registered_agent_support("fake-one")
            pair_two = get_registered_agent_support("fake-two")
            assert pair_one is not None
            assert pair_two is not None
            assert isinstance(pair_one[1], FakeAgentStrategy)
            assert isinstance(pair_two[1], FakeInteractiveAgentStrategy)
            assert "fake-one" in registry_one.agents
            assert "fake-two" not in registry_one.agents
            assert "fake-two" in registry_two.agents
            assert "fake-one" not in registry_two.agents

    def test_same_transport_registration_is_rejected(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            register_agent_support(
                "fake-one",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            with pytest.raises(ValueError):
                register_agent_support(
                    "fake-two",
                    transport=AgentTransport.GENERIC,
                    parser_factory=FakeAgentParser,
                    strategy_factory=FakeInteractiveAgentStrategy,
                    agent_registry=registry,
                )

            pair = get_registered_agent_support("fake-one")
            assert pair is not None
            assert isinstance(pair[1], FakeAgentStrategy)

    def test_returns_registered_config(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            config = register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            assert config is registry.agents["fake"]

    def test_isolation_context_restores_all_mutable_registries(self) -> None:
        baseline_parsers = dict(_PARSER_REGISTRY)
        baseline_strategies = dict(_STRATEGY_DISPATCH)
        baseline_name_transport = dict(_NAME_TRANSPORT_INDEX)
        baseline_custom_transports = dict(_CUSTOM_TRANSPORT_STRATEGIES)

        with _isolated_registries():
            registry = AgentRegistry()
            register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )
            assert "fake" in _PARSER_REGISTRY
            assert AgentTransport.GENERIC in _STRATEGY_DISPATCH
            assert "fake" in _NAME_TRANSPORT_INDEX
            assert AgentTransport.GENERIC in _CUSTOM_TRANSPORT_STRATEGIES

        assert dict(_PARSER_REGISTRY) == baseline_parsers
        assert dict(_STRATEGY_DISPATCH) == baseline_strategies
        assert dict(_NAME_TRANSPORT_INDEX) == baseline_name_transport
        assert dict(_CUSTOM_TRANSPORT_STRATEGIES) == baseline_custom_transports
