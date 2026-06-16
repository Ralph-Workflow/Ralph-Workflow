"""Tests for AgentRegistry register and unregister logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class DummyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=line, raw=line)


class DummyStrategy(BaseExecutionStrategy):
    pass


def test_register_unregister_flow() -> None:
    registry = AgentRegistry()
    name = "dummy-headless-agent"

    register_agent_support(
        name=name,
        transport=AgentTransport.GENERIC,
        parser_factory=DummyParser,
        strategy_factory=DummyStrategy,
        agent_registry=registry,
        interactive=False,
    )

    assert name in registry.agents
    assert default_catalog().get(name) is not None

    registry.unregister(name)

    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_idempotent_register_unregister_loop() -> None:
    # (b) registering a headless agent and unregistering it can be repeated 5 times in a row
    registry = AgentRegistry()
    name = "dummy-loop-agent"

    for _ in range(5):
        register_agent_support(
            name=name,
            transport=AgentTransport.GENERIC,
            parser_factory=DummyParser,
            strategy_factory=DummyStrategy,
            agent_registry=registry,
            interactive=False,
        )
        assert name in registry.agents
        assert default_catalog().get(name) is not None

        registry.unregister(name)
        assert name not in registry.agents
        assert default_catalog().get(name) is None


def test_interactive_register_unregister() -> None:
    # (c) the same flow works for an interactive agent
    registry = AgentRegistry()
    name = "dummy-interactive-agent"

    register_agent_support(
        name=name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=DummyParser,
        strategy_factory=DummyStrategy,
        agent_registry=registry,
        interactive=True,
    )
    assert name in registry.agents
    assert default_catalog().get(name) is not None
    assert default_catalog().get(name).spec.requires_pty is True

    registry.unregister(name)
    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_unregister_not_in_catalog() -> None:
    # (d) unregister() on a name in self.agents but NOT in catalog is safe
    # and removes from self.agents only
    registry = AgentRegistry()
    name = "legacy-alias"
    config = AgentConfig(cmd="echo alias", transport=AgentTransport.CLAUDE)
    registry.register(name, config)

    assert name in registry.agents
    # Since we bypassed register_agent_support, it is not in catalog
    assert default_catalog().get(name) is None

    registry.unregister(name)
    assert name not in registry.agents
    assert default_catalog().get(name) is None
