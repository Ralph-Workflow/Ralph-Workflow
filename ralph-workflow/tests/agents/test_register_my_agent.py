from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.agents
from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _make_agy_strategy
from ralph.agents.registration import (
    register_agent_support,
    register_my_agent,
)

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
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport, JsonParserType

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_default_strategy_table_covers_every_transport": (
        "AgentCatalog._DEFAULT_STRATEGIES was renamed to _default_strategies when the "
        "uppercase property lost its banned noqa suppression; the transport-coverage "
        "expectation is unchanged and now reads the live symbol"
    ),
    "test_default_strategies_factories_are_callable": (
        "AgentCatalog._DEFAULT_STRATEGIES was renamed to _default_strategies when the "
        "uppercase property lost its banned noqa suppression; the callability "
        "expectation is unchanged and now reads the live symbol"
    ),
    "test_register_my_agent_listed_before_register_agent_support": (
        "the export-order preference only held through a banned inline noqa for ruff "
        "RUF022, so the sweep retired it; the check now pins the canonical export "
        "order that the lint rule enforces"
    ),
    "test_ralph_agents_docstring_mentions_register_my_agent": (
        "the docstring mandate is retired by the absolute no-docstring policy; the "
        "check now asserts the package carries no docstring, which is the same "
        "contract in the opposite direction"
    ),
}


class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class TestRegisterMyAgent:
    def test_minimum_recipe_uses_5_kwargs(self) -> None:
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
        assert support.strategy_factory is GenericExecutionStrategy

    def test_strategy_default_for_claude_interactive_is_not_base(self) -> None:
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
        assert support.strategy_factory is not BaseExecutionStrategy
        assert support.strategy_factory is ClaudeInteractiveExecutionStrategy
        strategy = registry.catalog.get_strategy(AgentTransport.CLAUDE_INTERACTIVE, command="ci")
        assert isinstance(strategy, ClaudeInteractiveExecutionStrategy)
        assert not isinstance(strategy, BaseExecutionStrategy) or (
            type(strategy) is ClaudeInteractiveExecutionStrategy
        )

    def test_agy_transport_defaults_to_make_agy_strategy(self) -> None:
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


class TestStaticDispatchTableCoversEveryTransport:
    def test_default_strategy_table_covers_every_transport(self) -> None:
        default_strategies = default_catalog()._default_strategies
        assert set(default_strategies) == set(AgentTransport), (
            f"Default strategy table missing transport(s). "
            f"Expected {sorted(AgentTransport)}, got {sorted(default_strategies)}"
        )

    def test_default_strategies_factories_are_callable(self) -> None:
        default_strategies = default_catalog()._default_strategies
        for transport, factory in default_strategies.items():
            assert callable(factory), (
                f"AgentCatalog._default_strategies[{transport.name!r}] must be callable"
            )


class TestPublicSurfaceDiscoverability:
    def test_register_my_agent_in_ralph_agents_all(self) -> None:
        assert "register_my_agent" in ralph.agents.__all__, (
            "ralph.agents.__all__ must include register_my_agent"
        )
        assert "register_agent_support" in ralph.agents.__all__, (
            "ralph.agents.__all__ must keep register_agent_support for advanced use"
        )

    def test_ralph_agents_export_order_is_canonical(self) -> None:
        all_list = list(ralph.agents.__all__)
        assert all_list == sorted(all_list), (
            "ralph.agents.__all__ must stay in canonical order; the 90% recipe is "
            "surfaced by docs/agents/adding-a-new-agent.md, not by export order. "
            f"got order: {all_list}"
        )

    def test_ralph_agents_package_carries_no_docstring(self) -> None:
        assert ralph.agents.__doc__ is None, (
            "ralph.agents must carry no module docstring; contracts live in names, "
            "type annotations, and tests."
        )

    def test_ralph_agents_package_root_imports_still_work(self) -> None:
        assert callable(register_my_agent)
        assert callable(register_agent_support)
