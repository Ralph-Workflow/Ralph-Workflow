"""Public-surface black-box test for the Pi (pi.dev) agent path.

Drives the full public surface for the ``pi`` built-in WITHOUT touching
any private internals (no monkey-patching of ``_dispatch_json_object``,
no private catalog state, no real subprocess, no ``time.sleep``, no
network).  Mirrors the recipe in
``tests/agents/test_add_a_new_agent_blackbox_recipe.py`` and the
assertion style in ``tests/agents/test_quickstart_recipe_blackbox.py``.

The test pins:

  (1) ``AgentRegistry.from_config`` -> ``catalog.get('pi')`` returns the
      built-in support with the documented BuiltinAgentSpec row
      (cmd='pi', output_flag='--mode json', yolo_flag='--approve',
      session_flag='--session {}', can_commit=True, display_name='Pi',
      transport=AgentTransport.PI, json_parser=JsonParserType.PI,
      parser_factory=PiParser, strategy_factory=_make_pi_strategy).
  (2) ``catalog.get_parser('pi')`` returns a ``PiParser`` instance.
  (3) ``catalog.get_strategy(AgentTransport.PI, command='pi')`` returns
      a ``BaseExecutionStrategy`` instance.
  (4) ``build_command(support.config, prompt_file, options=...)`` from
      ``ralph.agents.invoke`` returns an argv that STARTS with
      ``('pi', '--mode', 'json')`` and ENDS with the actual prompt TEXT
      loaded from a ``tmp_path`` fixture (NOT the literal string
      ``'PROMPT.md'``).  Per the current public contract in
      ``ralph-workflow/ralph/agents/invoke/_command_builders/__init__.py:_load_prompt_text``,
      ``positional_prompt=True`` loads the prompt file CONTENT and emits
      it as the positional argv element.
  (5) ``catalog.get('pi/anthropic/claude-sonnet-4-20250514')`` returns a
      support with ``--model anthropic/claude-sonnet-4-20250514`` set
      (per the documented ``provider/id`` model id pattern).
  (6) ``PiParser().parse()`` yields a non-empty ``AgentOutputLine``
      stream for every non-silent event type from the committed fixture
      at ``tests/agents/parsers/fixtures/pi_dev_documented_events.json``
      (NOT the transient ``tmp/pi-dev-docs/inventory.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH, _make_pi_strategy
from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.parsers import _CUSTOM_COMMAND_REGISTRY, _PARSER_REGISTRY
from ralph.agents.parsers.pi import PiParser
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

# Snapshot the default catalog at import time so the override tests
# below (which install a configured [agents.pi] override) cannot leak
# into sibling tests in the same pytest session.  The catalog is a
# module-level singleton, so without this fixture an installed
# override would survive across test modules.
_GOLDEN_PARSERS: dict[str, object] = dict(_PARSER_REGISTRY)
_GOLDEN_CUSTOM: dict[str, object] = dict(_CUSTOM_COMMAND_REGISTRY)
_GOLDEN_STRATEGIES: dict[AgentTransport, object] = dict(_STRATEGY_DISPATCH)
_GOLDEN_ENTRIES: dict[str, object] = dict(default_catalog()._entries)
_GOLDEN_BY_COMMAND: dict[str, object] = dict(default_catalog()._by_command)


def _restore_golden_catalog() -> None:
    cat = default_catalog()
    cat._entries.clear()
    cat._entries.update(_GOLDEN_ENTRIES)
    cat._by_command.clear()
    cat._by_command.update(_GOLDEN_BY_COMMAND)
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(cast("dict", _GOLDEN_STRATEGIES))


@pytest.fixture(autouse=True)
def _reset_default_catalog() -> object:
    _restore_golden_catalog()
    yield
    _restore_golden_catalog()


_PROMPT_TEXT = "hello world"
_FIXTURE_PATH = (
    Path(__file__).parent / "parsers" / "fixtures" / "pi_dev_documented_events.json"
)


def _make_prompt(tmp_path: Path) -> str:
    """Write a prompt file and return its absolute path."""
    p = tmp_path / "PROMPT.md"
    p.write_text(_PROMPT_TEXT, encoding="utf-8")
    return str(p)


def _load_documented_event_lines() -> list[str]:
    """Load the committed wire-format fixture (NOT the transient inventory)."""
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestPiDevBlackboxPublicSurface:
    """Drive the full public surface for the pi built-in end-to-end."""

    def test_from_config_seeds_pi_in_default_catalog(self) -> None:
        """AgentRegistry.from_config(UnifiedConfig()) must seed the pi built-in."""
        registry = AgentRegistry.from_config(UnifiedConfig())

        support = registry.catalog.get("pi")
        assert support is not None, (
            "AgentRegistry.from_config must seed the 'pi' BuiltinAgentSpec"
        )
        assert support.transport is AgentTransport.PI, (
            f"pi transport must be AgentTransport.PI, got {support.transport!r}"
        )
        assert support.config.json_parser == JsonParserType.PI, (
            f"pi config.json_parser must be JsonParserType.PI, got "
            f"{support.config.json_parser!r}"
        )
        assert support.parser_factory is PiParser, (
            f"pi parser_factory must be PiParser, got {support.parser_factory!r}"
        )
        assert support.strategy_factory is _make_pi_strategy, (
            f"pi strategy_factory must be _make_pi_strategy, "
            f"got {support.strategy_factory!r}"
        )

    def test_pi_builtin_spec_row_matches_documented_cli(self) -> None:
        """The 7th BuiltinAgentSpec row must match the documented pi.dev CLI surface."""
        registry = AgentRegistry.from_config(UnifiedConfig())
        support = registry.catalog.get("pi")
        assert support is not None

        config = support.config
        assert config.cmd == "pi", f"pi cmd must be 'pi', got {config.cmd!r}"
        assert config.output_flag == "--mode json", (
            f"pi output_flag must be '--mode json', got {config.output_flag!r}"
        )
        assert config.yolo_flag == "--approve", (
            f"pi yolo_flag must be '--approve', got {config.yolo_flag!r}"
        )
        assert config.session_flag == "--session {}", (
            f"pi session_flag must be '--session {{}}', got {config.session_flag!r}"
        )
        assert config.can_commit is True, (
            f"pi can_commit must be True, got {config.can_commit!r}"
        )
        assert config.display_name == "Pi", (
            f"pi config.display_name must be 'Pi', got {config.display_name!r}"
        )

    def test_catalog_get_parser_returns_pi_parser_instance(self) -> None:
        """``catalog.get_parser('pi')`` must return a PiParser instance."""
        registry = AgentRegistry.from_config(UnifiedConfig())

        parser = registry.catalog.get_parser("pi")
        assert isinstance(parser, PiParser), (
            f"catalog.get_parser('pi') must return a PiParser, "
            f"got {type(parser).__name__}"
        )

    def test_catalog_get_strategy_returns_base_execution_strategy(self) -> None:
        """``catalog.get_strategy(AgentTransport.PI, command='pi')`` must
        return a BaseExecutionStrategy instance.
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        strategy = registry.catalog.get_strategy(
            AgentTransport.PI, command="pi"
        )
        assert isinstance(strategy, BaseExecutionStrategy), (
            f"catalog.get_strategy(PI, 'pi') must return a BaseExecutionStrategy, "
            f"got {type(strategy).__name__}"
        )

    def test_build_command_argv_starts_with_pi_mode_json_ends_with_prompt_text(
        self, tmp_path: Path
    ) -> None:
        """``build_command`` must emit ``pi --mode json --approve <prompt>`` and
        the prompt argv element must be the actual prompt TEXT (not 'PROMPT.md').
        """
        registry = AgentRegistry.from_config(UnifiedConfig())
        support = registry.catalog.get("pi")
        assert support is not None

        prompt_file = _make_prompt(tmp_path)
        options = BuildCommandOptions(workspace_path=tmp_path)

        argv = build_command(support.config, prompt_file, options=options)

        # argv must start with the documented ('pi', '--mode', 'json') tokens
        assert argv[:3] == ["pi", "--mode", "json"], (
            f"argv must start with ('pi', '--mode', 'json'), got {argv[:3]!r}"
        )
        # The yolo flag must be present
        assert "--approve" in argv, (
            f"argv must include the documented --approve yolo flag, got {argv!r}"
        )
        # The prompt FILE PATH must NOT appear as an argv element; the
        # actual prompt TEXT must.  Per positional_prompt=True contract in
        # _load_prompt_text: prompt file CONTENT is the positional argv
        # element, NOT the file path.
        assert "PROMPT.md" not in argv, (
            f"argv must not contain the literal 'PROMPT.md' (file path); "
            f"per positional_prompt=True, the file CONTENT must be the "
            f"positional element.  argv={argv!r}"
        )
        # The actual prompt TEXT must be the last argv element.
        assert argv[-1] == _PROMPT_TEXT, (
            f"argv must end with the actual prompt TEXT loaded from the "
            f"tmp_path fixture ({_PROMPT_TEXT!r}), got argv[-1]={argv[-1]!r} "
            f"(full argv={argv!r})"
        )

    def test_build_command_with_session_id_layout(self, tmp_path: Path) -> None:
        """Documented ``pi --mode json --session ID --approve <prompt>`` layout."""
        registry = AgentRegistry.from_config(UnifiedConfig())
        support = registry.catalog.get("pi")
        assert support is not None

        prompt_file = _make_prompt(tmp_path)
        options = BuildCommandOptions(
            session_id="sess-1", workspace_path=tmp_path
        )

        argv = build_command(support.config, prompt_file, options=options)

        # Documented layout per https://pi.dev/docs/latest/usage.
        assert argv == [
            "pi",
            "--mode",
            "json",
            "--session",
            "sess-1",
            "--approve",
            _PROMPT_TEXT,
        ], f"Unexpected argv layout: {argv!r}"

    def test_pi_model_shorthand_resolves_with_documented_model_flag(
        self, tmp_path: Path
    ) -> None:
        """``pi/anthropic/claude-sonnet-4-20250514`` must resolve through
        ``catalog.get`` to a support with
        ``--model anthropic/claude-sonnet-4-20250514`` set, per the
        documented ``provider/id`` model id pattern in
        https://pi.dev/docs/latest/usage.  This exercises the same public
        surface (``AgentCatalog.get`` -> dynamic alias resolver) that
        ``AgentRegistry.get`` uses internally so docs and runtime cannot
        drift.
        """
        registry = AgentRegistry.from_config(UnifiedConfig())

        support = registry.catalog.get("pi/anthropic/claude-sonnet-4-20250514")
        assert support is not None, (
            "registry.catalog.get('pi/anthropic/claude-sonnet-4-20250514') "
            "must resolve to an AgentSupport via the pi/<model> dynamic "
            "alias, matching the registry.get() public contract"
        )
        # The synthesized support must keep the pi built-in's parser and
        # strategy factories (PiParser + _make_pi_strategy).
        assert support.parser_factory is PiParser, (
            f"pi/<model> support parser_factory must be PiParser, "
            f"got {support.parser_factory!r}"
        )
        assert support.strategy_factory is _make_pi_strategy, (
            f"pi/<model> support strategy_factory must be "
            f"_make_pi_strategy, got {support.strategy_factory!r}"
        )
        # The model flag must preserve the full provider/id suffix.
        config = support.config
        assert config.model_flag is not None, "pi/<model> must set model_flag"
        assert "anthropic/claude-sonnet-4-20250514" in config.model_flag, (
            f"pi/<model> model_flag must contain the full provider/id suffix, "
            f"got {config.model_flag!r}"
        )

    def test_pi_parser_documented_event_vocabulary_yields_lines(self) -> None:
        """``PiParser().parse()`` must yield a non-empty stream of
        ``AgentOutputLine`` for every non-silent event type from the
        committed wire-format fixture.
        """
        fixture_lines = _load_documented_event_lines()
        assert fixture_lines, (
            f"Committed fixture at {_FIXTURE_PATH} is empty; the wire-format "
            f"spec test would silently skip the documented vocabulary"
        )

        # Parse the committed fixture end-to-end; the parser must not
        # raise on any documented event type.
        parser = PiParser()
        results = list(parser.parse(iter(fixture_lines)))

        # The parser must yield a non-empty stream for the documented
        # event vocabulary (per the inventory: session header, message
        # content, tool executions, and the documented stop events).
        assert results, (
            "PiParser must yield at least one AgentOutputLine for the "
            "committed documented event vocabulary"
        )
        # isError semantics: a tool_execution_end with isError=true must
        # produce a type='error' line (NOT type='tool_result').
        error_results = [r for r in results if r.type == "error"]
        assert error_results, (
            "PiParser must produce at least one type='error' line for the "
            "documented tool_execution_end(isError=true) event"
        )
        # Documented stop events: agent_end and turn_end must each produce
        # a type='stop' line.
        stop_results = [r for r in results if r.type == "stop"]
        assert len(stop_results) >= 2, (
            f"PiParser must produce at least two type='stop' lines (one "
            f"for agent_end and one for turn_end), got {len(stop_results)}"
        )


class TestPiDevBlackboxIndividualEvents:
    """Parametrized per-event-type coverage of the documented vocabulary."""

    def test_session_header_yields_session_line(self, tmp_path: Path) -> None:
        registry = AgentRegistry.from_config(UnifiedConfig())
        parser = registry.catalog.get_parser("pi")
        line = json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "abc-123-uuid",
                "timestamp": "2025-01-01T00:00:00Z",
                "cwd": "/tmp/work",
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "session"
        assert results[0].metadata.get("id") == "abc-123-uuid"

    def test_agent_end_yields_stop_line(self) -> None:
        parser = PiParser()
        line = json.dumps({"type": "agent_end", "messages": []})
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_turn_end_yields_stop_line(self) -> None:
        parser = PiParser()
        line = json.dumps(
            {
                "type": "turn_end",
                "message": {"role": "assistant"},
                "toolResults": [],
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_tool_execution_end_is_error_true_yields_error(self) -> None:
        parser = PiParser()
        line = json.dumps(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "fail"}]},
                "isError": True,
            }
        )
        results = list(parser.parse(_lines(line)))
        assert any(r.type == "error" for r in results)
        assert not any(r.type == "tool_result" for r in results)

    def test_tool_execution_end_is_error_false_yields_tool_result(self) -> None:
        parser = PiParser()
        line = json.dumps(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "ok"}]},
                "isError": False,
            }
        )
        results = list(parser.parse(_lines(line)))
        assert any(r.type == "tool_result" for r in results)
        assert not any(r.type == "error" for r in results)


class TestPiDevBlackboxConfigOverride:
    """End-to-end coverage of configured ``[agents.pi]`` overrides.

    Pins the D92 catalog-sync contract: a configured ``[agents.pi]``
    override must propagate to BOTH ``registry.get`` and
    ``registry.catalog.get`` so downstream consumers
    (``catalog.get_parser``, ``catalog.get_strategy``,
    ``build_command``, the ``pi/<model>`` dynamic alias) all see the
    configured command, not the built-in ``pi`` binary.
    """

    def test_override_propagates_to_registry_and_catalog(self) -> None:
        """Both ``registry.get('pi')`` and ``registry.catalog.get('pi')``
        must reflect the configured ``[agents.pi]`` override.
        """
        config = UnifiedConfig(
            agents={
                "pi": AgentConfig(
                    cmd="pi-custom",
                    transport=AgentTransport.PI,
                    session_flag="--session {}",
                    yolo_flag="--approve",
                )
            }
        )
        registry = AgentRegistry.from_config(config)

        # registry.get path (already worked pre-fix).
        direct = registry.get("pi")
        assert direct is not None
        assert direct.cmd == "pi-custom"

        # catalog.get path (this is the D92 gap being closed).
        catalog_pi = registry.catalog.get("pi")
        assert catalog_pi is not None, (
            "registry.catalog.get('pi') must resolve the configured "
            "override; the D92 gap left it pinned to the built-in"
        )
        assert catalog_pi.config.cmd == "pi-custom"
        # The override must preserve the built-in's parser factory and
        # strategy factory (they are structural to the pi transport).
        assert catalog_pi.parser_factory is PiParser
        assert catalog_pi.strategy_factory is _make_pi_strategy

    def test_override_propagates_to_dynamic_alias_endpoints(self) -> None:
        """The ``pi/<model>`` dynamic alias must use the override base.

        Both ``registry.get('pi/<model>')`` and
        ``registry.catalog.get('pi/<model>')`` must derive the
        synthesized support from the override's ``cmd``/``flags``,
        not from the built-in.  Regression coverage for the D92
        dynamic-alias-sync gap.
        """
        config = UnifiedConfig(
            agents={
                "pi": AgentConfig(
                    cmd="pi-custom",
                    transport=AgentTransport.PI,
                    session_flag="--session {}",
                    yolo_flag="--approve",
                )
            }
        )
        registry = AgentRegistry.from_config(config)

        # registry.get path
        alias = registry.get("pi/anthropic/claude-sonnet-4-20250514")
        assert alias is not None
        assert alias.cmd == "pi-custom", (
            f"pi/<model> must carry the override cmd, got {alias.cmd!r}"
        )
        assert alias.model_flag == "--model anthropic/claude-sonnet-4-20250514"

        # catalog.get path
        catalog_alias = registry.catalog.get(
            "pi/anthropic/claude-sonnet-4-20250514"
        )
        assert catalog_alias is not None, (
            "registry.catalog.get('pi/<model>') must resolve through the "
            "override base config"
        )
        assert catalog_alias.config.cmd == "pi-custom"
        assert (
            catalog_alias.config.model_flag
            == "--model anthropic/claude-sonnet-4-20250514"
        )
        # The synthesized dynamic-alias support must keep the built-in's
        # parser factory and strategy factory.
        assert catalog_alias.parser_factory is PiParser
        assert catalog_alias.strategy_factory is _make_pi_strategy
