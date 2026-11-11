from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.idle_watchdog import SubagentPidRegistry
from ralph.agents.invoke._command_builders import (
    COMMAND_BUILDERS,
    CursorCommandBuilder,
)
from ralph.agents.invoke._runtime_resolvers import (
    RUNTIME_RESOLVERS,
    CursorRuntimeResolver,
)
from ralph.agents.parsers import get_parser, resolve_parser_key
from ralph.agents.parsers.cursor import CursorParser
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine


class TestCursorRegistrationAcrossDispatchTables:
    def test_command_builders_has_cursor(self) -> None:
        assert COMMAND_BUILDERS[AgentTransport.CURSOR] is CursorCommandBuilder

    def test_runtime_resolvers_has_cursor(self) -> None:
        assert RUNTIME_RESOLVERS[AgentTransport.CURSOR] is CursorRuntimeResolver

    def test_strategy_dispatch_has_cursor(self) -> None:
        factory = _STRATEGY_DISPATCH.get(AgentTransport.CURSOR)
        assert factory is not None, (
            "_STRATEGY_DISPATCH is missing an entry for AgentTransport.CURSOR"
        )
        assert callable(factory), f"_STRATEGY_DISPATCH[CURSOR] is not callable: {factory!r}"
        instance = factory(label_scope=None, registry=None)
        assert isinstance(instance, BaseExecutionStrategy), (
            f"_STRATEGY_DISPATCH[CURSOR] produced non-strategy: {type(instance)}"
        )
        assert type(instance) is not BaseExecutionStrategy, (
            "_STRATEGY_DISPATCH[CURSOR] must produce a concrete strategy subclass, "
            "not the abstract BaseExecutionStrategy"
        )

    def test_parser_registry_resolves_cursor(self) -> None:
        key = resolve_parser_key("agent", JsonParserType.GENERIC, AgentTransport.CURSOR)
        parser = get_parser(key)
        assert parser is not None, f"get_parser({key!r}) returned None for AgentTransport.CURSOR"
        assert isinstance(parser, CursorParser), (
            f"Parser for AgentTransport.CURSOR is not a CursorParser: {type(parser)}"
        )
        assert hasattr(parser, "parse"), (
            "Parser for AgentTransport.CURSOR is missing parse() method"
        )


class TestCursorCatalogSeeding:
    def test_default_catalog_seeds_cursor_support(self) -> None:
        catalog = default_catalog()
        cursor_support = catalog.get("cursor")
        assert cursor_support is not None, "default_catalog().get('cursor') returned None"
        assert cursor_support.name == "cursor"
        assert cursor_support.transport is AgentTransport.CURSOR
        assert cursor_support.parser_factory is CursorParser
        assert cursor_support.config.cmd == "agent"
        assert cursor_support.config.session_flag == "--resume {}"


class TestCursorParserInstantiable:
    def test_cursor_parser_default_construction(self) -> None:
        parser = CursorParser()
        assert parser is not None
        assert callable(parser.parse)

    def test_cursor_parser_with_subagent_pid_registry(self) -> None:
        registry = SubagentPidRegistry()
        parser = CursorParser(
            subagent_pid_registry=registry,
            subagent_source_label="cursor",
        )
        assert parser is not None
        assert parser._subagent_pid_registry is registry
        assert parser._subagent_source_label == "cursor"

    def test_cursor_parser_yields_text_for_assistant_event(self) -> None:
        parser = CursorParser()
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "hello world"},
                    ],
                },
            }
        )
        results: list[AgentOutputLine] = list(parser.parse(iter([line])))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello world"


class TestCursorConfigInferredTransport:
    def test_explicit_transport_is_preserved(self) -> None:
        config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )
        assert config.transport is AgentTransport.CURSOR

    def test_inferred_transport_from_cmd(self) -> None:
        config = AgentConfig(cmd="agent")
        assert config.transport in (AgentTransport.CURSOR, AgentTransport.GENERIC)
