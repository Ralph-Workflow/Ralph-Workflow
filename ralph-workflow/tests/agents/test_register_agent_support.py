"""Black-box tests for the unified register_agent_support API.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.execution_state import BaseExecutionStrategy
from ralph.agents.parsers import AgentOutputLine, get_parser
from ralph.agents.registration import get_registered_agent_support, register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

from ._registration_test_utils import _isolated_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeAgentParser:
    """Pass-through parser used to prove registration wiring."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(content=line, kind="output", raw=line)


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
