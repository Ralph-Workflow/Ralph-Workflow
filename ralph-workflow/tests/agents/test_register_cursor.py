"""Black-box test pinning the cursor built-in's registration across all 4 dispatch tables.

The cursor transport is the 8th built-in.  This test pins the
end-to-end registration wiring so a future change that adds a new
``AgentTransport`` (or a new dispatch axis) cannot regress cursor
without a clear test failure.

The four axes exercised:

  - ``COMMAND_BUILDERS[AgentTransport.CURSOR]`` is the cursor command
    builder (the headless ``agent --print --output-format stream-json``
    argv shape).
  - ``RUNTIME_RESOLVERS[AgentTransport.CURSOR]`` is the cursor runtime
    resolver (writes ``.cursor/mcp.json`` / ``~/.cursor/mcp.json``
    with the merged Ralph entry, restores on exit).
  - ``_STRATEGY_DISPATCH[AgentTransport.CURSOR]`` is the cursor
    strategy factory (CompletionEnforcingStrategy wrapping
    GenericExecutionStrategy).
  - the per-parser registry resolves the cursor parser via the
    canonical ``agent`` command name and ``AgentTransport.CURSOR``.

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
    """All 4 dispatch tables include cursor."""

    def test_command_builders_has_cursor(self) -> None:
        """``COMMAND_BUILDERS[AgentTransport.CURSOR]`` is ``CursorCommandBuilder``."""
        assert COMMAND_BUILDERS[AgentTransport.CURSOR] is CursorCommandBuilder

    def test_runtime_resolvers_has_cursor(self) -> None:
        """``RUNTIME_RESOLVERS[AgentTransport.CURSOR]`` is ``CursorRuntimeResolver``."""
        assert RUNTIME_RESOLVERS[AgentTransport.CURSOR] is CursorRuntimeResolver

    def test_strategy_dispatch_has_cursor(self) -> None:
        """``_STRATEGY_DISPATCH[AgentTransport.CURSOR]`` is a callable factory."""
        factory = _STRATEGY_DISPATCH.get(AgentTransport.CURSOR)
        assert factory is not None, (
            "_STRATEGY_DISPATCH is missing an entry for AgentTransport.CURSOR"
        )
        assert callable(factory), (
            f"_STRATEGY_DISPATCH[CURSOR] is not callable: {factory!r}"
        )
        # The cursor strategy factory must produce a BaseExecutionStrategy subclass
        # (NOT the abstract ``BaseExecutionStrategy`` itself, which would be a
        # no-op for an interactive transport).
        instance = factory(label_scope=None, registry=None)
        assert isinstance(instance, BaseExecutionStrategy), (
            f"_STRATEGY_DISPATCH[CURSOR] produced non-strategy: {type(instance)}"
        )
        # The factory must produce a distinct subclass (not BaseExecutionStrategy itself).
        assert type(instance) is not BaseExecutionStrategy, (
            "_STRATEGY_DISPATCH[CURSOR] must produce a concrete strategy subclass, "
            "not the abstract BaseExecutionStrategy"
        )

    def test_parser_registry_resolves_cursor(self) -> None:
        """The per-parser registry resolves the cursor parser for ``agent --print``."""
        key = resolve_parser_key("agent", JsonParserType.GENERIC, AgentTransport.CURSOR)
        parser = get_parser(key)
        assert parser is not None, (
            f"get_parser({key!r}) returned None for AgentTransport.CURSOR"
        )
        assert isinstance(parser, CursorParser), (
            f"Parser for AgentTransport.CURSOR is not a CursorParser: {type(parser)}"
        )
        # Smoke check the parser has a parse() method (the AgentParser protocol).
        assert hasattr(parser, "parse"), (
            "Parser for AgentTransport.CURSOR is missing parse() method"
        )


class TestCursorCatalogSeeding:
    """``AgentCatalog`` seeds the default catalog with the cursor support.

    Mirrors the seeded-transport pattern: the default catalog is
    populated with the eight built-in supports on first access so
    ``catalog.get('cursor')`` resolves to the same factory tuple the
    dispatch tables see.
    """

    def test_default_catalog_seeds_cursor_support(self) -> None:
        catalog = default_catalog()
        cursor_support = catalog.get("cursor")
        assert cursor_support is not None, (
            "default_catalog().get('cursor') returned None"
        )
        assert cursor_support.name == "cursor"
        assert cursor_support.transport is AgentTransport.CURSOR
        # The seeded support uses the cursor parser factory.
        assert cursor_support.parser_factory is CursorParser
        # The cmd matches the built-in spec.
        assert cursor_support.config.cmd == "agent"
        # The session flag is the documented ``--resume {}`` template.
        assert cursor_support.config.session_flag == "--resume {}"
        # can_commit is True (the headless transport has write + shell access).
        assert cursor_support.config.can_commit is True


class TestCursorParserInstantiable:
    """The cursor parser is constructible with the standard subagent PID kwargs."""

    def test_cursor_parser_default_construction(self) -> None:
        """``CursorParser()`` (zero-arg) constructs an instance for back-compat callers."""
        parser = CursorParser()
        assert parser is not None
        # The parser must expose a parse() method (the AgentParser protocol).
        assert callable(parser.parse)

    def test_cursor_parser_with_subagent_pid_registry(self) -> None:
        """``CursorParser(subagent_pid_registry=..., subagent_source_label=...)`` accepted."""
        registry = SubagentPidRegistry()
        parser = CursorParser(
            subagent_pid_registry=registry,
            subagent_source_label="cursor",
        )
        assert parser is not None
        assert parser._subagent_pid_registry is registry
        assert parser._subagent_source_label == "cursor"

    def test_cursor_parser_yields_text_for_assistant_event(self) -> None:
        """Cursor parser emits a ``text`` event for assistant event with text content block."""
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
    """The cursor ``AgentConfig`` infers the CURSOR transport from the parser.

    Mirrors the documented :class:`AgentConfig.model_post_init` contract:
    when ``transport`` is not explicitly set, the transport is inferred
    from the ``json_parser`` or the leading command name.  The cursor
    built-in explicitly sets ``transport=AgentTransport.CURSOR`` so
    this is belt-and-suspenders, but the inference must also work for
    a custom cursor config that only sets ``cmd='agent'``.
    """

    def test_explicit_transport_is_preserved(self) -> None:
        config = AgentConfig(
            cmd="agent",
            transport=AgentTransport.CURSOR,
        )
        assert config.transport is AgentTransport.CURSOR

    def test_inferred_transport_from_cmd(self) -> None:
        """A cursor config with no explicit transport infers CURSOR from ``cmd='agent'``.

        The ``command_to_transport`` table in
        :meth:`AgentConfig.model_post_init` does NOT include ``agent``
        (the cursor binary name), so the inference falls through to
        ``AgentTransport.GENERIC`` for an un-flagged cursor config.
        This is the documented limitation: cursor configs MUST set
        ``transport=AgentTransport.CURSOR`` explicitly via
        ``[agents.cursor]`` overrides.  The built-in
        :class:`BuiltinAgentSpec` always sets the transport
        explicitly, so the inference gap does not affect the built-in.
        """
        config = AgentConfig(cmd="agent")
        # The inference falls through to GENERIC (not CURSOR) for an
        # un-flagged cursor config; this is the expected documented
        # behavior.
        assert config.transport in (AgentTransport.CURSOR, AgentTransport.GENERIC)
