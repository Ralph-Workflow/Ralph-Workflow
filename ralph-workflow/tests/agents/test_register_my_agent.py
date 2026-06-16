"""Tests for the public ``register_my_agent`` opinionated helper.

``register_my_agent`` is the 5-line recipe for adding a new agent.  When
``strategy`` is omitted, the helper picks a transport-derived default so
an interactive caller can never accidentally register an interactive
agent with :class:`BaseExecutionStrategy`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _make_agy_strategy

if TYPE_CHECKING:
    from collections.abc import Iterator
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.registration import register_my_agent
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport, JsonParserType


class _FakeParser(ParserTemplateBase):
    """Trivial parser for tests; yields one raw line per input line."""

    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class TestRegisterMyAgent:
    """Pin the public ``register_my_agent`` contract."""

    def test_minimum_recipe_uses_5_kwargs(self) -> None:
        """The minimum recipe is name + transport + parser + agent_registry (no strategy).

        The helper picks the transport-derived default strategy so the
        caller does not have to know which strategy class goes with which
        transport.  This pins the documented 5-line recipe.
        """
        registry = AgentRegistry()
        config = register_my_agent(
            name="my-headless",
            transport=AgentTransport.GENERIC,
            parser=GenericParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("my-headless")
        assert support is not None
        assert config.cmd == "my-headless"
        # The transport-derived default is GenericExecutionStrategy.
        assert support.strategy_factory is GenericExecutionStrategy

    def test_strategy_default_for_claude_interactive_is_not_base(self) -> None:
        """``register_my_agent(..., transport=CLAUDE_INTERACTIVE, interactive=True)``
        without an explicit strategy MUST default to
        :class:`ClaudeInteractiveExecutionStrategy`, not
        :class:`BaseExecutionStrategy`.
        """
        registry = AgentRegistry()
        register_my_agent(
            name="ci",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser=ClaudeParser,
            agent_registry=registry,
            interactive=True,
        )
        support = registry.catalog.get("ci")
        assert support is not None
        # The transport-derived default MUST NOT be BaseExecutionStrategy.
        assert support.strategy_factory is not BaseExecutionStrategy
        # The transport-derived default IS ClaudeInteractiveExecutionStrategy.
        assert support.strategy_factory is ClaudeInteractiveExecutionStrategy
        strategy = registry.catalog.get_strategy(
            AgentTransport.CLAUDE_INTERACTIVE, command="ci"
        )
        assert isinstance(strategy, ClaudeInteractiveExecutionStrategy)
        assert not isinstance(strategy, BaseExecutionStrategy) or (
            type(strategy) is ClaudeInteractiveExecutionStrategy
        )

    def test_agy_transport_defaults_to_make_agy_strategy(self) -> None:
        """The AGY transport's default strategy is the agy strategy factory."""
        registry = AgentRegistry()
        register_my_agent(
            name="agy-test",
            transport=AgentTransport.AGY,
            parser=GenericParser,
            agent_registry=registry,
            interactive=True,
            no_default_session_flag=True,
        )
        support = registry.catalog.get("agy-test")
        assert support is not None
        # The support's strategy_factory must be the agy factory.
        assert support.strategy_factory is _make_agy_strategy

    def test_opencode_transport_defaults_to_opencode_strategy(self) -> None:
        registry = AgentRegistry()
        register_my_agent(
            name="oc",
            transport=AgentTransport.OPENCODE,
            parser=OpenCodeParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("oc")
        assert support is not None
        assert support.strategy_factory is OpenCodeExecutionStrategy

    def test_claude_transport_defaults_to_claude_strategy(self) -> None:
        registry = AgentRegistry()
        register_my_agent(
            name="cl",
            transport=AgentTransport.CLAUDE,
            parser=ClaudeParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("cl")
        assert support is not None
        assert support.strategy_factory is ClaudeExecutionStrategy

    def test_codex_transport_defaults_to_generic_strategy(self) -> None:
        registry = AgentRegistry()
        register_my_agent(
            name="cx",
            transport=AgentTransport.CODEX,
            parser=CodexParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("cx")
        assert support is not None
        assert support.strategy_factory is GenericExecutionStrategy

    def test_nanocoder_transport_defaults_to_generic_strategy(self) -> None:
        registry = AgentRegistry()
        register_my_agent(
            name="nc",
            transport=AgentTransport.NANOCODER,
            parser=GenericParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("nc")
        assert support is not None
        assert support.strategy_factory is GenericExecutionStrategy

    def test_cmd_defaults_to_name(self) -> None:
        """When ``cmd`` is not given, it defaults to ``name``."""
        registry = AgentRegistry()
        register_my_agent(
            name="my-cmd",
            transport=AgentTransport.GENERIC,
            parser=GenericParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("my-cmd")
        assert support is not None
        assert support.config.cmd == "my-cmd"

    def test_explicit_strategy_overrides_transport_default(self) -> None:
        """An explicit ``strategy=`` argument must win over the transport default."""
        registry = AgentRegistry()

        class _MyCustom(BaseExecutionStrategy):
            pass

        register_my_agent(
            name="explicit-strategy",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser=ClaudeParser,
            strategy=_MyCustom,
            agent_registry=registry,
            interactive=True,
        )
        support = registry.catalog.get("explicit-strategy")
        assert support is not None
        assert support.strategy_factory is _MyCustom

    def test_interactive_agent_gets_default_session_flag(self) -> None:
        """Interactive agent without no_default_session_flag gets ``--resume {}``."""
        registry = AgentRegistry()
        register_my_agent(
            name="ic-with-session",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser=ClaudeParser,
            agent_registry=registry,
            interactive=True,
        )
        support = registry.catalog.get("ic-with-session")
        assert support is not None
        assert support.config.session_flag == "--resume {}"

    def test_no_default_session_flag_suppresses_session_flag(self) -> None:
        """When ``no_default_session_flag=True``, session_flag is None."""
        registry = AgentRegistry()
        register_my_agent(
            name="no-session",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser=ClaudeParser,
            agent_registry=registry,
            interactive=True,
            no_default_session_flag=True,
        )
        support = registry.catalog.get("no-session")
        assert support is not None
        assert support.config.session_flag is None
        assert support.no_default_session_flag is True

    def test_interactive_transports_get_requires_pty(self) -> None:
        """Every interactive transport's spec.requires_pty must be True."""
        for transport in (
            AgentTransport.CLAUDE_INTERACTIVE,
            AgentTransport.AGY,
        ):
            registry = AgentRegistry()
            register_my_agent(
                name=f"pty-{transport.name}",
                transport=transport,
                parser=GenericParser,
                agent_registry=registry,
                interactive=True,
            )
            support = registry.catalog.get(f"pty-{transport.name}")
            assert support is not None
            assert support.spec.requires_pty is True, (
                f"Interactive {transport.name} must have requires_pty=True"
            )

    def test_json_parser_default(self) -> None:
        """The default json_parser kwarg is JsonParserType.GENERIC."""
        registry = AgentRegistry()
        register_my_agent(
            name="default-json",
            transport=AgentTransport.GENERIC,
            parser=GenericParser,
            agent_registry=registry,
        )
        support = registry.catalog.get("default-json")
        assert support is not None
        assert support.config.json_parser is JsonParserType.GENERIC
