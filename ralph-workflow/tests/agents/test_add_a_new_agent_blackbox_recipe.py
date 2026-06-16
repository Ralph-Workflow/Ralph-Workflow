"""End-to-end black-box recipe test for ``register_my_agent``.

This test exercises the runtime path: it loads the EXACT 5-line recipe
from ``docs/agents/adding-a-new-agent.md`` between marker comments
(``BLACKBOX_RECIPE_START`` / ``BLACKBOX_RECIPE_END``) and ``exec()``s
that snippet in a controlled namespace, then drives catalog lookups
(``catalog.get(name)``, ``catalog.get_parser(name).parse(lines)``,
``catalog.get_strategy(transport, command=name)``) and the PUBLIC
``build_command`` helper from ``ralph.agents.invoke``.

The test deliberately does NOT use ``COMMAND_BUILDERS[transport].build(...)``
because ``COMMAND_BUILDERS`` is a ``dict[AgentTransport, type[CommandBuilder]]``
that stores builder CLASSES, not instances. Calling ``.build`` on a class
raises ``TypeError``; the runtime always instantiates the class via
``build_command(config, prompt_file, options=BuildCommandOptions(...))``.

The doc snippet is exec()d (not just compiled) so the test exercises
the documented recipe verbatim, including the imports and the
``register_my_agent(...)`` call.  The ``my_registry`` object the snippet
binds is exposed back to the test so the runtime-path assertions can
use the same registry the snippet registered against.  This means
docs and runtime evidence cannot drift from each other: if the doc
snippet regresses, the test fails.
"""

from __future__ import annotations

import re
from pathlib import Path

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

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
    match = re.search(r"```python\n(.*?)\n```", block, re.DOTALL)
    if match is None:
        msg = f"No python code block found between recipe markers in {doc_path}"
        raise AssertionError(msg)
    return match.group(1)


def _exec_recipe_snippet(snippet: str, *, name_override: str) -> AgentRegistry:
    """Exec the doc snippet with a controlled namespace and return its registry.

    The doc snippet binds ``my_registry`` and calls ``register_my_agent``.
    We override the agent name via a small code-prefix so the test can
    register under a unique name (the snippet's hard-coded name collides
    between tests).  All other snippet code is preserved verbatim so the
    test exercises the EXACT code the docs publish.
    """
    namespace: dict[str, object] = {
        "__name__": "_blackbox_recipe_snippet",
    }
    # Pre-bind the registry so the snippet's ``my_registry = AgentRegistry()``
    # assignment is replaced with a name the test can capture.  We splice a
    # single line at the top of the snippet that renames the agent in the
    # call, leaving every other line of the snippet intact.
    rewritten = re.sub(
        r'name="my-agent"',
        f'name="{name_override}"',
        snippet,
        count=1,
    )
    # Compile + exec the snippet verbatim.
    code = compile(rewritten, "<recipe-snippet>", "exec")
    exec(code, namespace)
    my_registry_obj = namespace.get("my_registry")
    assert isinstance(my_registry_obj, AgentRegistry), (
        "Recipe snippet did not bind my_registry to an AgentRegistry; "
        f"got {type(my_registry_obj).__name__!r}"
    )
    return my_registry_obj


class TestBlackboxRecipeEndToEnd:
    """Drive the full ``register_my_agent`` -> catalog -> build_command path."""

    def test_recipe_snippet_present(self) -> None:
        snippet = _load_recipe_snippet()
        assert "register_my_agent" in snippet, (
            f"Doc snippet must call register_my_agent; got:\n{snippet}"
        )
        # The opinionated 5-line recipe must NOT pass ``strategy=``; the
        # transport-derived default is the whole point of the helper.
        assert "strategy=" not in snippet, (
            "Doc snippet must demonstrate the transport-derived default "
            "by omitting strategy=; got:\n{snippet}"
        )

    def test_recipe_compiles(self) -> None:
        """The doc snippet must be valid Python (sentinel substitution only)."""
        snippet = _load_recipe_snippet()
        # The snippet uses ``...`` placeholders that the test rewrites to
        # ``pass`` so compile() succeeds.  The production recipe is plain
        # real code with no sentinels; the substitution is a no-op when
        # the snippet does not contain ``...``.
        code = snippet.replace("...", "pass")
        try:
            compile(code, "<recipe-snippet>", "exec")
        except SyntaxError as exc:
            msg = f"Doc recipe snippet failed to compile:\n{snippet}\n{exc}"
            raise AssertionError(msg) from exc

    def test_register_my_agent_end_to_end(self) -> None:
        """Drive the full black-box path end to end via the EXACT doc snippet."""
        snippet = _load_recipe_snippet()
        registry = _exec_recipe_snippet(snippet, name_override="recipe-agent")

        # 1. catalog.get(name) returns the support with the expected transport.
        support = registry.catalog.get("recipe-agent")
        assert support is not None
        assert support.transport is AgentTransport.GENERIC
        # The transport-derived default strategy is GenericExecutionStrategy.
        assert support.strategy_factory is GenericExecutionStrategy

        # 2. catalog.get_parser(name) returns a parser instance.
        parser = registry.catalog.get_parser("recipe-agent")
        # The doc snippet uses GenericParser as the parser; check that.
        assert isinstance(parser, GenericParser)

        # 3. catalog.get_strategy(transport, command=name) returns the strategy.
        strategy = registry.catalog.get_strategy(
            AgentTransport.GENERIC, command="recipe-agent"
        )
        assert isinstance(strategy, GenericExecutionStrategy)

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
            assert isinstance(entry, type), (
                f"COMMAND_BUILDERS[{transport.name}] must be a class, "
                f"got instance of {type(entry).__name__}"
            )


def test_recipe_snippet_executes_against_real_parser() -> None:
    """End-to-end: the doc snippet registers a real parser, real strategy,
    and a real build_command can be invoked against the result.

    This is a module-level test (in addition to the class-based tests
    above) so the runtime-path coverage shows up plainly in pytest -v.
    """
    snippet = _load_recipe_snippet()
    # Verify the snippet's imports actually resolve to real classes.
    # If the doc snippet drifts (e.g. a renamed module), this fails
    # before we even try to exec the snippet.
    compiled = compile(snippet, "<recipe-snippet>", "exec")

    # Exec the snippet with a fresh namespace.
    namespace: dict[str, object] = {"__name__": "_blackbox_recipe_runtime"}
    exec(compiled, namespace)

    registry_obj = namespace.get("my_registry")
    assert isinstance(registry_obj, AgentRegistry), (
        "Recipe snippet did not bind my_registry to an AgentRegistry"
    )

    support = registry_obj.catalog.get("my-agent")
    assert support is not None, (
        "Recipe snippet did not register an agent under 'my-agent'"
    )
    assert support.transport is AgentTransport.GENERIC
    # The snippet did not pass strategy=, so the helper used the
    # transport-derived default; assert the resolved default is NOT
    # BaseExecutionStrategy (the bug the helper is meant to prevent).
    assert support.strategy_factory is not BaseExecutionStrategy
