from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.idle_watchdog import SubagentPidRegistry
from ralph.agents.invoke._command_builders import (
    COMMAND_BUILDERS,
    KimiCommandBuilder,
)
from ralph.agents.invoke._runtime_resolvers import (
    RUNTIME_RESOLVERS,
    KimiRuntimeResolver,
)
from ralph.agents.parsers import get_parser, resolve_parser_key
from ralph.agents.parsers.kimi import KimiParser
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine


class TestKimiRegistrationAcrossDispatchTables:
    def test_command_builders_has_kimi(self) -> None:
        assert COMMAND_BUILDERS[AgentTransport.KIMI] is KimiCommandBuilder

    def test_runtime_resolvers_has_kimi(self) -> None:
        assert RUNTIME_RESOLVERS[AgentTransport.KIMI] is KimiRuntimeResolver

    def test_strategy_dispatch_has_kimi(self) -> None:
        factory = _STRATEGY_DISPATCH.get(AgentTransport.KIMI)
        assert factory is not None, "_STRATEGY_DISPATCH is missing an entry for AgentTransport.KIMI"
        assert callable(factory), f"_STRATEGY_DISPATCH[KIMI] is not callable: {factory!r}"
        instance = factory(label_scope=None, registry=None)
        assert isinstance(instance, BaseExecutionStrategy), (
            f"_STRATEGY_DISPATCH[KIMI] produced non-strategy: {type(instance)}"
        )
        assert type(instance) is not BaseExecutionStrategy, (
            "_STRATEGY_DISPATCH[KIMI] must produce a concrete strategy subclass, "
            "not the abstract BaseExecutionStrategy"
        )

    def test_parser_registry_resolves_kimi(self) -> None:
        key = resolve_parser_key("kimi", JsonParserType.GENERIC, AgentTransport.KIMI)
        parser = get_parser(key)
        assert parser is not None, f"get_parser({key!r}) returned None for AgentTransport.KIMI"
        assert isinstance(parser, KimiParser), (
            f"Parser for AgentTransport.KIMI is not a KimiParser: {type(parser)}"
        )
        assert hasattr(parser, "parse"), "Parser for AgentTransport.KIMI is missing parse() method"


class TestKimiCatalogSeeding:
    def test_default_catalog_seeds_kimi_support(self) -> None:
        catalog = default_catalog()
        kimi_support = catalog.get("kimi")
        assert kimi_support is not None, "default_catalog().get('kimi') returned None"
        assert kimi_support.name == "kimi"
        assert kimi_support.transport is AgentTransport.KIMI
        assert kimi_support.parser_factory is KimiParser
        assert kimi_support.config.cmd == "kimi"
        assert kimi_support.config.output_flag == "--output-format=stream-json"
        assert kimi_support.config.print_flag == "-p"
        assert kimi_support.config.session_flag == "-S {}"


class TestKimiParserInstantiable:
    def test_kimi_parser_default_construction(self) -> None:
        parser = KimiParser()
        assert parser is not None
        assert callable(parser.parse)

    def test_kimi_parser_with_subagent_pid_registry(self) -> None:
        registry = SubagentPidRegistry()
        parser = KimiParser(
            subagent_pid_registry=registry,
            subagent_source_label="kimi",
        )
        assert parser is not None
        assert parser._subagent_pid_registry is registry
        assert parser._subagent_source_label == "kimi"

    def test_kimi_parser_yields_text_for_assistant_message(self) -> None:
        parser = KimiParser()
        line = json.dumps(
            {
                "role": "assistant",
                "content": "hello world",
            }
        )
        results: list[AgentOutputLine] = list(parser.parse(iter([line])))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello world"


class TestKimiConfigInferredTransport:
    def test_explicit_transport_is_preserved(self) -> None:
        config = AgentConfig(
            cmd="kimi",
            transport=AgentTransport.KIMI,
        )
        assert config.transport is AgentTransport.KIMI
