"""Demonstrates legacy register_agent_support and new AgentCatalog API for interactive agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeInteractiveAgentParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class FakeInteractiveAgentStrategy(BaseExecutionStrategy):
    pass


class TestAddANewInteractiveAgentRecipe:
    def test_fresh_catalog_resolves_parser_and_strategy(self) -> None:
        catalog = AgentCatalog()
        support = AgentSupport(
            name="fake-interactive",
            spec=AgentSpec(
                name="fake-interactive",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                interactive=True,
                requires_pty=True,
                session_resume_template="--resume {}",
                completion_required=True,
            ),
            parser_factory=FakeInteractiveAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            config=AgentConfig(cmd="fake-interactive"),
        )
        register_agent_support_to_catalog("fake-interactive", support, catalog)

        parser = catalog.get_parser("fake-interactive")
        assert isinstance(parser, FakeInteractiveAgentParser)

        strategy = catalog.get_strategy(
            AgentTransport.CLAUDE_INTERACTIVE, command="fake-interactive"
        )
        assert isinstance(strategy, FakeInteractiveAgentStrategy)

    def test_default_catalog_resolves_after_registration(self) -> None:
        support = AgentSupport(
            name="fake-interactive-default",
            spec=AgentSpec(
                name="fake-interactive-default",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                interactive=True,
                requires_pty=True,
                session_resume_template="--resume {}",
                completion_required=True,
            ),
            parser_factory=FakeInteractiveAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            config=AgentConfig(cmd="fake-interactive-default"),
        )
        register_agent_support_to_catalog(
            "fake-interactive-default", support, default_catalog()
        )

        found = default_catalog().get("fake-interactive-default")
        assert found is not None
        assert found.name == "fake-interactive-default"
