"""End-to-end black-box recipe test for ``register_my_agent``.

This test exercises the runtime path: it uses ``register_my_agent`` to
register a fake agent, then drives catalog lookups
(``catalog.get(name)``, ``catalog.get_parser(name).parse(lines)``,
``catalog.get_strategy(transport, command=name)``) and the PUBLIC
``build_command`` helper from ``ralph.agents.invoke``.

The test deliberately does NOT use ``COMMAND_BUILDERS[transport].build(...)``
because ``COMMAND_BUILDERS`` is a ``dict[AgentTransport, type[CommandBuilder]]``
that stores builder CLASSES, not instances. Calling ``.build`` on a class
raises ``TypeError``; the runtime always instantiates the class via
``build_command(config, prompt_file, options=BuildCommandOptions(...))``.

The 5-line recipe is read directly from
``docs/agents/adding-a-new-agent.md`` between marker comments so docs
cannot drift from the test.  See ``RECIPE_START_MARKER`` /
``RECIPE_END_MARKER`` below.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents import register_my_agent
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


RECIPE_START_MARKER = "<!-- BLACKBOX_RECIPE_START -->"
RECIPE_END_MARKER = "<!-- BLACKBOX_RECIPE_END -->"


def _load_recipe_snippet() -> str:
    """Read the 5-line recipe from docs/agents/adding-a-new-agent.md.

    The recipe lives between the marker comments so this test always
    reflects the canonical doc snippet.
    """
    doc_path = Path("docs/agents/adding-a-new-agent.md")
    content = doc_path.read_text(encoding="utf-8")
    start = content.find(RECIPE_START_MARKER)
    end = content.find(RECIPE_END_MARKER)
    if start == -1 or end == -1:
        msg = (
            f"Could not find recipe markers {RECIPE_START_MARKER!r} "
            f"and {RECIPE_END_MARKER!r} in {doc_path}"
        )
        raise AssertionError(msg)
    block = content[start + len(RECIPE_START_MARKER) : end]
    # Extract the python code block
    match = re.search(r"```python\n(.*?)\n```", block, re.DOTALL)
    if match is None:
        msg = f"No python code block found between recipe markers in {doc_path}"
        raise AssertionError(msg)
    return match.group(1)


class _FakeParser(ParserTemplateBase):
    """Minimal parser for the black-box recipe test.

    Classifies each line as raw (non-JSON) or as a JSON text event.
    """

    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        try:
            parsed = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return
        if isinstance(parsed, dict) and parsed.get("type") == "text":
            yield AgentOutputLine(
                type="text",
                content=str(parsed.get("content", "")),
                raw=stripped,
                metadata=parsed,
            )


class _FakeStrategy(BaseExecutionStrategy):
    pass


class TestBlackboxRecipeEndToEnd:
    """Drive the full ``register_my_agent`` -> catalog -> build_command path."""

    def test_recipe_snippet_present(self) -> None:
        snippet = _load_recipe_snippet()
        # The snippet must call register_my_agent (the whole point).
        assert "register_my_agent" in snippet, (
            f"Doc snippet must call register_my_agent; got:\n{snippet}"
        )

    def test_recipe_compiles(self) -> None:
        """The doc snippet must be valid Python (with sentinel substitutions)."""
        snippet = _load_recipe_snippet()
        # Substitute placeholders the doc snippet uses so the snippet
        # compiles.  ``...`` becomes ``pass`` and ``FAKE_NAME`` stays
        # intact for the test below.
        code = snippet.replace("...", "pass")
        try:
            compile(code, "<recipe-snippet>", "exec")
        except SyntaxError as exc:
            msg = f"Doc recipe snippet failed to compile:\n{snippet}\n{exc}"
            raise AssertionError(msg) from exc

    def test_register_my_agent_end_to_end(self) -> None:
        """Drive the full black-box path end to end."""
        registry = AgentRegistry()
        register_my_agent(
            name="recipe-agent",
            transport=AgentTransport.GENERIC,
            parser=_FakeParser,
            strategy=_FakeStrategy,
            agent_registry=registry,
        )

        # 1. catalog.get(name) returns the support with the expected transport.
        support = registry.catalog.get("recipe-agent")
        assert support is not None
        assert support.transport is AgentTransport.GENERIC

        # 2. catalog.get_parser(name) returns a parser instance whose
        # .parse(lines) yields the expected sequence of AgentOutputLine objects.
        parser = registry.catalog.get_parser("recipe-agent")
        assert isinstance(parser, _FakeParser)
        lines = iter(
            [
                '{"type": "text", "content": "hello"}',
                "not-json-at-all",
                '{"type": "text", "content": "world"}',
            ]
        )
        results = list(parser.parse(lines))
        types = [r.type for r in results]
        assert "text" in types
        assert "raw" in types

        # 3. catalog.get_strategy(transport, command=name) returns the strategy.
        strategy = registry.catalog.get_strategy(
            AgentTransport.GENERIC, command="recipe-agent"
        )
        assert isinstance(strategy, _FakeStrategy)

        # 4. The PUBLIC build_command helper produces the expected argv.
        #    This is the SAME path the runtime uses (see invoke_agent).
        #    Crucially, it is NOT COMMAND_BUILDERS[transport].build(...)
        #    because COMMAND_BUILDERS stores builder CLASSES, not instances.
        argv = build_command(
            support.config,
            "PROMPT.md",
            options=BuildCommandOptions(
                model_flag=None,
                session_id=None,
                verbose=False,
                pure=False,
                mcp_endpoint=None,
                allowed_mcp_tool_names=(),
                unsafe_mode=False,
                system_prompt_file=None,
                workspace_path=None,
                initial_session_id=None,
                settings_json=None,
                stop_sentinel_path=None,
            ),
        )
        # The argv must include the registered cmd.
        assert argv, "build_command returned an empty argv"
        assert "recipe-agent" in argv[0] or argv[0] == "recipe-agent", (
            f"Expected argv[0] to be the registered cmd, got {argv[0]!r}"
        )

    def test_command_builders_dispatch_table_stores_classes(self) -> None:
        """Pin the contract that ``COMMAND_BUILDERS`` stores builder CLASSES.

        The dispatch table maps ``AgentTransport`` to ``type[CommandBuilder]``.
        Calling ``.build`` on a class raises ``TypeError``; callers must
        use the public ``build_command(config, prompt_file, options=...)``
        helper which instantiates the class.
        """
        for transport in AgentTransport:
            entry = COMMAND_BUILDERS[transport]
            # The dispatch value must be a class, not an instance.
            assert isinstance(entry, type), (
                f"COMMAND_BUILDERS[{transport.name}] must be a class, "
                f"got instance of {type(entry).__name__}"
            )

    def test_explicit_strategy_via_register_my_agent(self) -> None:
        """A caller can pass an explicit strategy; the transport default is overridden."""
        registry = AgentRegistry()
        register_my_agent(
            name="explicit-strategy-agent",
            transport=AgentTransport.CLAUDE,
            parser=_FakeParser,
            strategy=GenericExecutionStrategy,
            agent_registry=registry,
        )
        support = registry.catalog.get("explicit-strategy-agent")
        assert support is not None
        assert support.strategy_factory is GenericExecutionStrategy
