"""Black-box test pinning the kimi built-in's registration across all 4 dispatch tables.

The kimi transport is the 9th built-in.  This test pins the
end-to-end registration wiring so a future change that adds a new
``AgentTransport`` (or a new dispatch axis) cannot regress kimi
without a clear test failure.

The four axes exercised:

  - ``COMMAND_BUILDERS[AgentTransport.KIMI]`` is the kimi command
    builder (the headless ``kimi --output-format=stream-json -p
    <prompt>`` argv shape).
  - ``RUNTIME_RESOLVERS[AgentTransport.KIMI]`` is the kimi runtime
    resolver (writes ``.kimi-code/mcp.json`` /
    ``$KIMI_CODE_HOME/mcp.json`` with the merged Ralph entry,
    restores on exit).
  - ``_STRATEGY_DISPATCH[AgentTransport.KIMI]`` is the kimi
    strategy factory (CompletionEnforcingStrategy wrapping
    GenericExecutionStrategy).
  - the per-parser registry resolves the kimi parser via the
    canonical ``kimi`` command name and ``AgentTransport.KIMI``.

These tests are pure black-box; no live subprocess, no live network.
"""

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
    """All 4 dispatch tables include kimi."""

    def test_command_builders_has_kimi(self) -> None:
        """``COMMAND_BUILDERS[AgentTransport.KIMI]`` is ``KimiCommandBuilder``."""
        assert COMMAND_BUILDERS[AgentTransport.KIMI] is KimiCommandBuilder

    def test_runtime_resolvers_has_kimi(self) -> None:
        """``RUNTIME_RESOLVERS[AgentTransport.KIMI]`` is ``KimiRuntimeResolver``."""
        assert RUNTIME_RESOLVERS[AgentTransport.KIMI] is KimiRuntimeResolver

    def test_strategy_dispatch_has_kimi(self) -> None:
        """``_STRATEGY_DISPATCH[AgentTransport.KIMI]`` is a callable factory."""
        factory = _STRATEGY_DISPATCH.get(AgentTransport.KIMI)
        assert factory is not None, (
            "_STRATEGY_DISPATCH is missing an entry for AgentTransport.KIMI"
        )
        assert callable(factory), f"_STRATEGY_DISPATCH[KIMI] is not callable: {factory!r}"
        # The kimi strategy factory must produce a BaseExecutionStrategy subclass
        # (NOT the abstract ``BaseExecutionStrategy`` itself, which would be a
        # no-op for the headless transport).
        instance = factory(label_scope=None, registry=None)
        assert isinstance(instance, BaseExecutionStrategy), (
            f"_STRATEGY_DISPATCH[KIMI] produced non-strategy: {type(instance)}"
        )
        # The factory must produce a distinct subclass (not BaseExecutionStrategy itself).
        assert type(instance) is not BaseExecutionStrategy, (
            "_STRATEGY_DISPATCH[KIMI] must produce a concrete strategy subclass, "
            "not the abstract BaseExecutionStrategy"
        )

    def test_parser_registry_resolves_kimi(self) -> None:
        """The per-parser registry resolves the kimi parser for ``kimi -p``."""
        key = resolve_parser_key("kimi", JsonParserType.GENERIC, AgentTransport.KIMI)
        parser = get_parser(key)
        assert parser is not None, f"get_parser({key!r}) returned None for AgentTransport.KIMI"
        assert isinstance(parser, KimiParser), (
            f"Parser for AgentTransport.KIMI is not a KimiParser: {type(parser)}"
        )
        # Smoke check the parser has a parse() method (the AgentParser protocol).
        assert hasattr(parser, "parse"), (
            "Parser for AgentTransport.KIMI is missing parse() method"
        )


class TestKimiCatalogSeeding:
    """``AgentCatalog`` seeds the default catalog with the kimi support.

    Mirrors the seeded-transport pattern: the default catalog is
    populated with the nine built-in supports on first access so
    ``catalog.get('kimi')`` resolves to the same factory tuple the
    dispatch tables see.
    """

    def test_default_catalog_seeds_kimi_support(self) -> None:
        catalog = default_catalog()
        kimi_support = catalog.get("kimi")
        assert kimi_support is not None, "default_catalog().get('kimi') returned None"
        assert kimi_support.name == "kimi"
        assert kimi_support.transport is AgentTransport.KIMI
        # The seeded support uses the kimi parser factory.
        assert kimi_support.parser_factory is KimiParser
        # The cmd matches the built-in spec.
        assert kimi_support.config.cmd == "kimi"
        # The output flag is the documented stream-json selector.
        assert kimi_support.config.output_flag == "--output-format=stream-json"
        # The print flag is the documented short prompt-mode flag.
        assert kimi_support.config.print_flag == "-p"
        # The session flag is the documented resume template.
        assert kimi_support.config.session_flag == "-r {}"
        # can_commit is True (prompt mode auto-approves its tool calls,
        # giving the headless transport write + shell access).
        assert kimi_support.config.can_commit is True


class TestKimiParserInstantiable:
    """The kimi parser is constructible with the standard subagent PID kwargs."""

    def test_kimi_parser_default_construction(self) -> None:
        """``KimiParser()`` (zero-arg) constructs an instance for back-compat callers."""
        parser = KimiParser()
        assert parser is not None
        # The parser must expose a parse() method (the AgentParser protocol).
        assert callable(parser.parse)

    def test_kimi_parser_with_subagent_pid_registry(self) -> None:
        """``KimiParser(subagent_pid_registry=..., subagent_source_label=...)`` accepted."""
        registry = SubagentPidRegistry()
        parser = KimiParser(
            subagent_pid_registry=registry,
            subagent_source_label="kimi",
        )
        assert parser is not None
        assert parser._subagent_pid_registry is registry
        assert parser._subagent_source_label == "kimi"

    def test_kimi_parser_yields_text_for_assistant_message(self) -> None:
        """Kimi parser emits a ``text`` event for an assistant message with string content."""
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
    """The kimi ``AgentConfig`` carries the KIMI transport when set explicitly.

    The ``command_to_transport`` inference table mapping ``cmd='kimi'``
    to ``AgentTransport.KIMI`` is pinned separately by
    ``tests/config/test_kimi_cmd_transport_inference.py``; this class
    only pins that an explicit transport survives model validation.
    """

    def test_explicit_transport_is_preserved(self) -> None:
        config = AgentConfig(
            cmd="kimi",
            transport=AgentTransport.KIMI,
        )
        assert config.transport is AgentTransport.KIMI
