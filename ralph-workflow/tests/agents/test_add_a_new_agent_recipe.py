"""Consolidated add/update/remove recipe test for headless and interactive agents.

Canonical recipe: docs/agents/adding-a-new-agent.md
This test file acts as the executable form of the Add/Update/Remove sections.

Tests add, update, and remove workflows for both headless and interactive agents
using the public AgentCatalog API exclusively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import AgentCatalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class _FakeStrategy(BaseExecutionStrategy):
    pass


_HEADLESS_SPEC = AgentSpec(name="fake-headless", transport=AgentTransport.GENERIC)

_INTERACTIVE_SPEC = AgentSpec(
    name="fake-interactive",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    interactive=True,
    requires_pty=True,
    session_resume_template="--resume {}",
    completion_required=True,
)


def _make_support(name: str, spec: AgentSpec) -> AgentSupport:
    return AgentSupport(
        name=name,
        spec=spec,
        parser_factory=_FakeParser,
        strategy_factory=_FakeStrategy,
        config=AgentConfig(cmd=name),
    )


class TestAddUpdateRemoveAgentRecipe:
    @pytest.mark.parametrize(
        "name,spec",
        [
            ("fake-headless", _HEADLESS_SPEC),
            ("fake-interactive", _INTERACTIVE_SPEC),
        ],
    )
    def test_add_workflow(self, name: str, spec: AgentSpec) -> None:
        catalog = AgentCatalog()
        support = _make_support(name, spec)
        catalog.add(support)

        assert isinstance(catalog.get_parser(name), _FakeParser)
        strategy = catalog.get_strategy(spec.transport, command=name)
        assert isinstance(strategy, _FakeStrategy)

    @pytest.mark.parametrize(
        "name,spec",
        [
            ("fake-headless", _HEADLESS_SPEC),
            ("fake-interactive", _INTERACTIVE_SPEC),
        ],
    )
    def test_update_workflow(self, name: str, spec: AgentSpec) -> None:
        catalog = AgentCatalog()
        support = _make_support(name, spec)

        catalog.add(support)
        catalog.remove(name)
        catalog.add(support)

        assert catalog.get(name) is not None
        assert isinstance(catalog.get_parser(name), _FakeParser)
        strategy = catalog.get_strategy(spec.transport, command=name)
        assert isinstance(strategy, _FakeStrategy)

    @pytest.mark.parametrize(
        "name,spec",
        [
            ("fake-headless", _HEADLESS_SPEC),
            ("fake-interactive", _INTERACTIVE_SPEC),
        ],
    )
    def test_remove_workflow(self, name: str, spec: AgentSpec) -> None:
        catalog = AgentCatalog()
        support = _make_support(name, spec)

        catalog.add(support)
        catalog.remove(name)

        assert catalog.get(name) is None
        with pytest.raises(ValueError):
            catalog.get_parser(name)

    @pytest.mark.parametrize(
        "name,spec",
        [
            ("fake-interactive", _INTERACTIVE_SPEC),
        ],
    )
    def test_interactive_pty_invariant(self, name: str, spec: AgentSpec) -> None:
        """Interactive agents with CLAUDE_INTERACTIVE transport require PTY."""
        assert spec.requires_pty == (spec.transport == AgentTransport.CLAUDE_INTERACTIVE)
