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
    namespace: dict[str, object] = {
        "__name__": "_blackbox_recipe_snippet",
    }
    rewritten = re.sub(
        r'name="my-agent"',
        f'name="{name_override}"',
        snippet,
        count=1,
    )
    code = compile(rewritten, "<recipe-snippet>", "exec")
    exec(code, namespace)
    my_registry_obj = namespace.get("my_registry")
    assert isinstance(my_registry_obj, AgentRegistry), (
        "Recipe snippet did not bind my_registry to an AgentRegistry; "
        f"got {type(my_registry_obj).__name__!r}"
    )
    return my_registry_obj


class TestBlackboxRecipeEndToEnd:
    def test_recipe_snippet_present(self) -> None:
        snippet = _load_recipe_snippet()
        assert "register_my_agent" in snippet, (
            f"Doc snippet must call register_my_agent; got:\n{snippet}"
        )
        assert "strategy=" not in snippet, (
            "Doc snippet must demonstrate the transport-derived default "
            "by omitting strategy=; got:\n{snippet}"
        )

    def test_recipe_compiles(self) -> None:
        snippet = _load_recipe_snippet()
        code = snippet.replace("...", "pass")
        try:
            compile(code, "<recipe-snippet>", "exec")
        except SyntaxError as exc:
            msg = f"Doc recipe snippet failed to compile:\n{snippet}\n{exc}"
            raise AssertionError(msg) from exc

    def test_register_my_agent_end_to_end(self) -> None:
        snippet = _load_recipe_snippet()
        registry = _exec_recipe_snippet(snippet, name_override="recipe-agent")

        support = registry.catalog.get("recipe-agent")
        assert support is not None
        assert support.transport is AgentTransport.GENERIC
        assert support.strategy_factory is GenericExecutionStrategy

        parser = registry.catalog.get_parser("recipe-agent")
        assert isinstance(parser, GenericParser)

        strategy = registry.catalog.get_strategy(AgentTransport.GENERIC, command="recipe-agent")
        assert isinstance(strategy, GenericExecutionStrategy)

        argv = build_command(
            support.config,
            "PROMPT.md",
            options=BuildCommandOptions(
                model_flag=None,
                session_id=None,
                verbose=False,
                mcp_endpoint=None,
                allowed_mcp_tool_names=(),
                unsafe_mode=False,
                master_prompt_file=None,
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
        for transport in AgentTransport:
            entry = COMMAND_BUILDERS[transport]
            assert isinstance(entry, type), (
                f"COMMAND_BUILDERS[{transport.name}] must be a class, "
                f"got instance of {type(entry).__name__}"
            )


def test_recipe_snippet_executes_against_real_parser() -> None:
    snippet = _load_recipe_snippet()
    compiled = compile(snippet, "<recipe-snippet>", "exec")

    namespace: dict[str, object] = {"__name__": "_blackbox_recipe_runtime"}
    exec(compiled, namespace)

    registry_obj = namespace.get("my_registry")
    assert isinstance(registry_obj, AgentRegistry), (
        "Recipe snippet did not bind my_registry to an AgentRegistry"
    )

    support = registry_obj.catalog.get("my-agent")
    assert support is not None, "Recipe snippet did not register an agent under 'my-agent'"
    assert support.transport is AgentTransport.GENERIC
    assert support.strategy_factory is not BaseExecutionStrategy
