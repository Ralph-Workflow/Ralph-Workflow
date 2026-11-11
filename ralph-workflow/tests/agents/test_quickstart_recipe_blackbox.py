from __future__ import annotations

import re
from pathlib import Path

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

RECIPE_START_MARKER = "<!-- BLACKBOX_RECIPE_START -->"
RECIPE_END_MARKER = "<!-- BLACKBOX_RECIPE_END -->"
QUICKSTART_DOC_PATH = Path("docs/agents/quickstart-add-a-new-agent.md")


def _load_recipe_snippets() -> list[tuple[int, str]]:
    if not QUICKSTART_DOC_PATH.exists():
        msg = f"Quickstart doc not found at {QUICKSTART_DOC_PATH}"
        raise AssertionError(msg)
    content = QUICKSTART_DOC_PATH.read_text(encoding="utf-8")

    snippets: list[tuple[int, str]] = []
    cursor = 0
    block_index = 0
    while True:
        start = content.find(RECIPE_START_MARKER, cursor)
        if start == -1:
            break
        end = content.find(RECIPE_END_MARKER, start)
        if end == -1:
            msg = (
                f"Quickstart doc has BLACKBOX_RECIPE_START at offset {start} "
                f"without a matching BLACKBOX_RECIPE_END"
            )
            raise AssertionError(msg)
        block = content[start + len(RECIPE_START_MARKER) : end]
        match = re.search(r"```python\n(.*?)\n```", block, re.DOTALL)
        if match is None:
            msg = f"No python code block found between markers (block {block_index})"
            raise AssertionError(msg)
        snippets.append((block_index, match.group(1)))
        block_index += 1
        cursor = end + len(RECIPE_END_MARKER)
    return snippets


def _exec_recipe_snippet(snippet: str, *, name_override: str) -> AgentRegistry:
    namespace: dict[str, object] = {"__name__": "_quickstart_recipe_snippet"}
    rewritten = re.sub(
        r'name="[^"]+"',
        f'name="{name_override}"',
        snippet,
        count=1,
    )
    if rewritten == snippet:
        msg = f"Quickstart snippet missing ``name=`` kwarg; got:\n{snippet}"
        raise AssertionError(msg)
    code = compile(rewritten, "<quickstart-recipe-snippet>", "exec")
    exec(code, namespace)
    registry_obj = namespace.get("my_registry")
    assert isinstance(registry_obj, AgentRegistry), (
        "Quickstart snippet did not bind my_registry to an AgentRegistry; "
        f"got {type(registry_obj).__name__!r}"
    )
    return registry_obj


def _build_default_options() -> BuildCommandOptions:
    return BuildCommandOptions(
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
    )


class TestQuickstartRecipeBlackbox:
    def test_quickstart_doc_has_two_recipes(self) -> None:
        snippets = _load_recipe_snippets()
        assert len(snippets) == 2, (
            f"Expected exactly 2 BLACKBOX_RECIPE blocks in {QUICKSTART_DOC_PATH}, "
            f"got {len(snippets)}"
        )

    def test_headless_recipe_executes_and_registers(self) -> None:
        snippets = _load_recipe_snippets()
        _, snippet = snippets[0]
        registry = _exec_recipe_snippet(snippet, name_override="quickstart-headless")

        support = registry.catalog.get("quickstart-headless")
        assert support is not None, (
            "Headless recipe did not register an agent under the override name"
        )
        assert support.transport is AgentTransport.GENERIC, (
            f"Headless recipe transport must be GENERIC, got {support.transport!r}"
        )
        assert support.spec.interactive is False, (
            f"Headless recipe must produce spec.interactive=False, got {support.spec.interactive!r}"
        )

        parser = registry.catalog.get_parser("quickstart-headless")
        assert isinstance(parser, GenericParser), (
            f"Headless recipe parser must be GenericParser, got {type(parser).__name__}"
        )

        strategy = registry.catalog.get_strategy(
            AgentTransport.GENERIC, command="quickstart-headless"
        )
        assert isinstance(strategy, BaseExecutionStrategy), (
            f"Headless recipe strategy must be a BaseExecutionStrategy, "
            f"got {type(strategy).__name__}"
        )

        argv = build_command(support.config, "PROMPT.md", options=_build_default_options())
        assert argv, "build_command returned an empty argv for the headless recipe"
        assert argv[0] == "quickstart-headless", (
            f"Headless recipe argv[0] must be the agent's cmd, got {argv[0]!r}"
        )

    def test_interactive_recipe_executes_and_registers(self) -> None:
        snippets = _load_recipe_snippets()
        _, snippet = snippets[1]
        registry = _exec_recipe_snippet(snippet, name_override="quickstart-interactive")

        support = registry.catalog.get("quickstart-interactive")
        assert support is not None, (
            "Interactive recipe did not register an agent under the override name"
        )
        assert support.transport is AgentTransport.CLAUDE_INTERACTIVE, (
            f"Interactive recipe transport must be CLAUDE_INTERACTIVE, got {support.transport!r}"
        )
        assert support.spec.interactive is True, (
            f"Interactive recipe must produce spec.interactive=True, "
            f"got {support.spec.interactive!r}"
        )

        parser = registry.catalog.get_parser("quickstart-interactive")
        assert isinstance(parser, ClaudeParser), (
            f"Interactive recipe parser must be ClaudeParser, got {type(parser).__name__}"
        )

        strategy = registry.catalog.get_strategy(
            AgentTransport.CLAUDE_INTERACTIVE, command="quickstart-interactive"
        )
        assert isinstance(strategy, BaseExecutionStrategy), (
            f"Interactive recipe strategy must be a BaseExecutionStrategy, "
            f"got {type(strategy).__name__}"
        )

        argv = build_command(support.config, "PROMPT.md", options=_build_default_options())
        assert argv, "build_command returned an empty argv for the interactive recipe"
        assert argv[0] == "quickstart-interactive", (
            f"Interactive recipe argv[0] must be the agent's cmd, got {argv[0]!r}"
        )

    def test_recipe_snippets_call_register_my_agent(self) -> None:
        snippets = _load_recipe_snippets()
        for block_index, snippet in snippets:
            assert "register_my_agent" in snippet, (
                f"Quickstart recipe block {block_index} must call register_my_agent; "
                f"got:\n{snippet}"
            )
            assert "strategy=" not in snippet, (
                f"Quickstart recipe block {block_index} must omit strategy= "
                f"to demonstrate the transport-derived default; got:\n{snippet}"
            )

    def test_recipe_snippets_compile(self) -> None:
        snippets = _load_recipe_snippets()
        for block_index, snippet in snippets:
            try:
                compile(snippet, "<quickstart-recipe-snippet>", "exec")
            except SyntaxError as exc:
                msg = f"Quickstart recipe block {block_index} failed to compile:\n{snippet}\n{exc}"
                raise AssertionError(msg) from exc
