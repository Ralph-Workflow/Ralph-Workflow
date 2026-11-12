from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

import ralph.agents
from ralph.agents import AgentCatalog, AgentRegistry, default_catalog, register_agent_support
from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.builtin_spec import BuiltinAgentSpec
from ralph.agents.execution_state import (
    BaseExecutionStrategy,
    strategy_for_command,
)
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.execution_state.generic_execution_strategy import (
    GenericExecutionStrategy,
)
from ralph.agents.parsers import (
    _CUSTOM_COMMAND_REGISTRY,
    _PARSER_REGISTRY,
    AgentOutputLine,
    ClaudeInteractiveParser,
    ClaudeParser,
    get_parser,
    resolve_parser_key,
)
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.registration import (
    get_registered_agent_support,
)
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.cli.commands.commit import collect_commit_agent_output
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.plumbing.smoke_plumbing import (
    _count_parsed_events,
    _parser_key_for_config,
    _tool_activity_seen,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_default_strategies_covers_every_transport": (
        "AgentCatalog._DEFAULT_STRATEGIES was renamed to _default_strategies when the "
        "uppercase property lost its banned noqa suppression; the transport-coverage "
        "expectation is unchanged and now reads the live symbol"
    ),
}

_GOLDEN_PARSERS: dict[str, object] = dict(_PARSER_REGISTRY)
_GOLDEN_CUSTOM: dict[str, object] = dict(_CUSTOM_COMMAND_REGISTRY)
_GOLDEN_STRATEGIES: dict[AgentTransport, object] = dict(_STRATEGY_DISPATCH)
_GOLDEN_ENTRIES: dict[str, object] = dict(default_catalog()._entries)
_GOLDEN_BY_COMMAND: dict[str, object] = dict(default_catalog()._by_command)


@pytest.fixture(autouse=True)
def _reset_catalog() -> object:
    cat = default_catalog()
    cat._entries.clear()
    cat._by_command.clear()
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(_GOLDEN_STRATEGIES)
    yield
    cat._entries.clear()
    cat._entries.update(_GOLDEN_ENTRIES)
    cat._by_command.clear()
    cat._by_command.update(_GOLDEN_BY_COMMAND)
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(_GOLDEN_STRATEGIES)


class FakeAgentParser:
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class FakeTextParser:
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="text", content=line, raw=line)


class FakeAgentStrategy(BaseExecutionStrategy):
    pass


class FakeInteractiveAgentStrategy(BaseExecutionStrategy):
    pass


class KwargsAwareStrategy(BaseExecutionStrategy):
    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: object | None = None,
        subagent_pid_source: object = None,
    ) -> None:
        self.received_label_scope = label_scope
        self.received_registry = registry
        self.received_subagent_pid_source = subagent_pid_source


class TestRegisterAgentSupport:
    def test_registers_headless_agent_round_trip(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "fake",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        assert isinstance(config, AgentConfig)
        assert config.transport == AgentTransport.GENERIC
        parser = get_parser("fake")
        assert isinstance(parser, FakeAgentParser)

        strategy = strategy_for_command("fake", AgentTransport.GENERIC)
        assert isinstance(strategy, FakeAgentStrategy)

        pair = get_registered_agent_support("fake")
        assert pair is not None
        assert isinstance(pair[0], FakeAgentParser)
        assert isinstance(pair[1], FakeAgentStrategy)
        assert "fake" in registry.agents
        assert registry.agents["fake"].transport == AgentTransport.GENERIC

    def test_fake_parser_yields_expected_output_lines(self) -> None:
        registry = AgentRegistry()

        register_agent_support(
            "fake",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        parser = get_parser("fake")
        parsed = list(parser.parse(iter(["hello", "world"])))
        assert len(parsed) == 2
        assert all(line.type == "output" for line in parsed)
        assert parsed[0].content == "hello"
        assert parsed[1].content == "world"

    def test_runtime_parser_selection_uses_registered_config(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "fake",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        assert _parser_key_for_config(config) == "fake"
        assert isinstance(get_parser(_parser_key_for_config(config)), FakeAgentParser)
        assert _count_parsed_events(config, list(iter(["hello", "world"]))) == 2

    def test_registers_interactive_agent_round_trip(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "fake-interactive",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry,
            interactive=True,
        )

        assert isinstance(config, AgentConfig)
        assert config.transport == AgentTransport.CLAUDE_INTERACTIVE
        assert config.session_flag is not None
        parser = get_parser("fake-interactive")
        assert isinstance(parser, FakeAgentParser)

        strategy = strategy_for_command("fake-interactive", AgentTransport.CLAUDE_INTERACTIVE)
        assert isinstance(strategy, FakeInteractiveAgentStrategy)

        pair = get_registered_agent_support("fake-interactive")
        assert pair is not None
        assert isinstance(pair[1], FakeInteractiveAgentStrategy)
        assert "fake-interactive" in registry.agents

    def test_independent_registrations_do_not_cross_contaminate(self) -> None:
        registry_one = AgentRegistry()
        registry_two = AgentRegistry()

        register_agent_support(
            "fake-one",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry_one,
        )
        register_agent_support(
            "fake-two",
            transport=AgentTransport.CODEX,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry_two,
        )

        pair_one = get_registered_agent_support("fake-one")
        pair_two = get_registered_agent_support("fake-two")
        assert pair_one is not None
        assert pair_two is not None
        assert isinstance(pair_one[1], FakeAgentStrategy)
        assert isinstance(pair_two[1], FakeInteractiveAgentStrategy)
        assert "fake-one" in registry_one.agents
        assert "fake-two" not in registry_one.agents
        assert "fake-two" in registry_two.agents
        assert "fake-one" not in registry_two.agents

    def test_same_transport_registration_allows_coexistence(self) -> None:
        registry = AgentRegistry()

        register_agent_support(
            "fake-one",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )
        register_agent_support(
            "fake-two",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry,
        )

        pair_one = get_registered_agent_support("fake-one")
        pair_two = get_registered_agent_support("fake-two")
        assert pair_one is not None
        assert pair_two is not None
        assert isinstance(pair_one[1], FakeAgentStrategy)
        assert isinstance(pair_two[1], FakeInteractiveAgentStrategy)
        assert "fake-one" in registry.agents
        assert "fake-two" in registry.agents

    def test_returns_registered_config(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "fake",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        assert config is registry.agents["fake"]

    def test_strategy_factory_kwargs_are_preserved_when_accepted(self) -> None:
        registry = AgentRegistry()
        fake_registry = object()

        register_agent_support(
            "fake-kwargs",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=KwargsAwareStrategy,
            agent_registry=registry,
        )

        pair = get_registered_agent_support("fake-kwargs")
        assert pair is not None
        strategy = pair[1]
        assert isinstance(strategy, KwargsAwareStrategy)
        assert strategy.received_label_scope is None
        assert strategy.received_registry is None

        strategy = strategy_for_command(
            "fake-kwargs",
            AgentTransport.GENERIC,
            label_scope="scope-x",
            registry=fake_registry,
        )
        assert isinstance(strategy, KwargsAwareStrategy)
        assert strategy.received_label_scope == "scope-x"
        assert strategy.received_registry == fake_registry

    def test_strategy_factory_without_kwargs_still_works(self) -> None:
        registry = AgentRegistry()

        register_agent_support(
            "fake-no-kwargs",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        strategy = strategy_for_command(
            "fake-no-kwargs",
            AgentTransport.GENERIC,
            label_scope="scope-x",
            registry=object(),
        )
        assert isinstance(strategy, FakeAgentStrategy)

    def test_commit_plumbing_uses_registered_parser(self) -> None:
        registry = AgentRegistry()
        config = register_agent_support(
            "fake-commit",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        _parsed_output, raw_output, resume_session_id = collect_commit_agent_output(
            ["hello"],
            parser_type=_parser_key_for_config(config),
            agent_name="fake-commit",
            verbose=False,
            display_context=make_display_context(),
        )

        assert "hello" in raw_output
        assert resume_session_id is None

    def test_stream_parsed_agent_activity_uses_registered_parser(self) -> None:
        registry = AgentRegistry()
        config = register_agent_support(
            "fake-stream",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeTextParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        rendered: list[str] = []
        stream_parsed_agent_activity(
            ["hello"],
            parser_type=str(config.json_parser),
            agent_name="fake-stream",
            rendered_output_sink=rendered,
            agent_config=config,
        )

        assert len(rendered) == 1
        assert "fake-stream" in rendered[0]
        assert "hello" in rendered[0]
        assert "\u2139" in rendered[0]

    def test_custom_cmd_and_session_flag_override_defaults(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "my-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="my-agent-cli",
            session_flag="--continue {}",
            output_flag="--json",
        )

        assert config.cmd == "my-agent-cli"
        assert config.session_flag == "--continue {}"
        assert config.output_flag == "--json"

    def test_interactive_default_session_flag_when_session_flag_omitted(self) -> None:
        registry = AgentRegistry()

        config = register_agent_support(
            "my-interactive",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry,
            interactive=True,
        )

        assert config.session_flag == "--resume {}"


class TestResolveParserKey:
    def test_registered_agent_command_wins_over_generic_parser(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "fake-cmd",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="fake-cmd --verbose",
        )

        key = resolve_parser_key(
            "fake-cmd --verbose", JsonParserType.GENERIC, AgentTransport.GENERIC
        )
        assert key == "fake-cmd --verbose"
        assert isinstance(get_parser(key), FakeAgentParser)

    def test_name_differs_from_cmd_registers_parser_under_full_command(self) -> None:
        registry = AgentRegistry()
        config = register_agent_support(
            "my-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="my-agent-cli --json",
        )

        assert _parser_key_for_config(config) == "my-agent-cli --json"
        assert isinstance(get_parser("my-agent-cli --json"), FakeAgentParser)
        assert isinstance(get_parser("my-agent"), FakeAgentParser)
        assert _count_parsed_events(config, ["hello"]) == 1

    def test_registered_interactive_agent_command_wins_over_builtin_interactive_parser(
        self,
    ) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "fake-interactive",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry,
            interactive=True,
        )

        key = resolve_parser_key(
            "fake-interactive",
            JsonParserType.GENERIC,
            AgentTransport.CLAUDE_INTERACTIVE,
        )
        assert key == "fake-interactive"
        assert isinstance(get_parser(key), FakeAgentParser)

    def test_builtin_claude_interactive_transport_uses_claude_interactive_parser(
        self,
    ) -> None:
        key = resolve_parser_key(
            "claude", JsonParserType.GENERIC, AgentTransport.CLAUDE_INTERACTIVE
        )
        assert key == "claude_interactive"

    def test_configured_claude_interactive_override_keeps_interactive_parser(self) -> None:
        config = AgentConfig(
            cmd="claude",
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        )
        AgentRegistry.from_config(UnifiedConfig(agents={"claude": config}))

        key = resolve_parser_key(config.cmd, config.json_parser, config.transport)

        assert key == "claude_interactive"
        assert isinstance(get_parser(key), ClaudeInteractiveParser)


class TestStrategyForCommand:
    def test_same_transport_agents_use_distinct_strategies(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "agent-a",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )
        register_agent_support(
            "agent-b",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeInteractiveAgentStrategy,
            agent_registry=registry,
        )

        strategy_a = strategy_for_command("agent-a", AgentTransport.GENERIC)
        strategy_b = strategy_for_command("agent-b", AgentTransport.GENERIC)
        assert isinstance(strategy_a, FakeAgentStrategy)
        assert isinstance(strategy_b, FakeInteractiveAgentStrategy)

    def test_strategy_for_command_falls_back_to_transport(self) -> None:
        strategy = strategy_for_command("unknown", AgentTransport.GENERIC)
        assert type(strategy).__name__ == "GenericExecutionStrategy"

    def test_smoke_tool_activity_uses_command_specific_strategy(self) -> None:

        class LineYieldingStrategy(BaseExecutionStrategy):
            def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
                if line == "tool: hammer":
                    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
                return super().classify_activity_line(line)

        registry = AgentRegistry()
        config = register_agent_support(
            "tool-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=LineYieldingStrategy,
            agent_registry=registry,
        )

        assert _tool_activity_seen(config, ["tool: hammer"]) is True
        assert _tool_activity_seen(config, ["plain output"]) is False


class _AnyKwargsStrategy(BaseExecutionStrategy):
    def __init__(self, **kwargs: object) -> None:
        self.received_kwargs = kwargs


class TestRegistrationRegressionCases:
    def test_kwargs_forwarded_through_var_keyword_factory(self) -> None:
        registry = AgentRegistry()
        fake_registry = object()

        register_agent_support(
            "kwargs-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=_AnyKwargsStrategy,
            agent_registry=registry,
        )

        strategy = strategy_for_command(
            "kwargs-agent",
            AgentTransport.GENERIC,
            label_scope="scope-x",
            registry=fake_registry,
        )
        assert isinstance(strategy, _AnyKwargsStrategy)
        assert strategy.received_kwargs == {
            "label_scope": "scope-x",
            "registry": fake_registry,
            "subagent_pid_source": None,
        }

    def test_custom_command_does_not_collide_with_builtin_claude_family(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "claude-wrapper",
            transport=AgentTransport.CLAUDE,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="claude wrapper",
        )

        key = resolve_parser_key("claude -p", JsonParserType.CLAUDE, AgentTransport.CLAUDE)
        assert key == "claude"
        assert isinstance(get_parser(key), ClaudeParser)

        custom_key = resolve_parser_key(
            "claude wrapper", JsonParserType.GENERIC, AgentTransport.CLAUDE
        )
        assert custom_key == "claude wrapper"
        assert isinstance(get_parser(custom_key), FakeAgentParser)
        assert isinstance(
            strategy_for_command("claude wrapper", AgentTransport.CLAUDE),
            FakeAgentStrategy,
        )

    def test_unknown_command_on_same_transport_uses_transport_strategy(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "custom-generic",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        strategy = strategy_for_command("totally-unknown-binary", AgentTransport.GENERIC)
        assert isinstance(strategy, GenericExecutionStrategy)

    def test_transport_strategy_is_overwritten_by_custom_registration(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "custom-generic",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
        )

        strategy = strategy_for_command("custom-generic", AgentTransport.GENERIC)
        assert isinstance(strategy, FakeAgentStrategy)


class TestRegistrationCollisionGuard:
    def test_registering_reserved_parser_name_raises(self) -> None:
        registry = AgentRegistry()
        with pytest.raises(ValueError, match="reserved built-in parser name"):
            register_agent_support(
                "claude",
                transport=AgentTransport.CLAUDE,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

        assert isinstance(get_parser("claude"), ClaudeParser)

    def test_custom_cmd_does_not_replace_builtin_parser_key(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "claude-wrapper",
            transport=AgentTransport.CLAUDE,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="claude wrapper",
        )

        assert isinstance(get_parser("claude"), ClaudeParser)
        assert isinstance(get_parser("claude wrapper"), FakeAgentParser)
        assert isinstance(
            strategy_for_command("claude wrapper", AgentTransport.CLAUDE),
            FakeAgentStrategy,
        )

    def test_duplicate_command_registration_is_rejected(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "agent-one",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="shared-cmd",
        )

        with pytest.raises(ValueError, match="already registered"):
            register_agent_support(
                "agent-two",
                transport=AgentTransport.CLAUDE,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeInteractiveAgentStrategy,
                agent_registry=registry,
                cmd="shared-cmd",
            )

    def test_duplicate_command_same_transport_is_rejected(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            "agent-one",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            agent_registry=registry,
            cmd="shared-cmd",
        )

        with pytest.raises(ValueError, match="already registered"):
            register_agent_support(
                "agent-two",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeInteractiveAgentStrategy,
                agent_registry=registry,
                cmd="shared-cmd",
            )


class TestCatalogBackedRegistration:
    def test_round_trip_through_default_catalog(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "fake-catalog",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
        )
        default_catalog().add(support)

        found = default_catalog().get("fake-catalog")
        assert found is not None
        assert found.name == "fake-catalog"

    def test_parser_resolvable_from_default_catalog(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "fake-catalog-parser",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
        )
        default_catalog().add(support)

        parser = default_catalog().get_parser("fake-catalog-parser")
        assert isinstance(parser, FakeAgentParser)

    def test_strategy_resolvable_from_default_catalog(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "fake-catalog-strategy",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
        )
        default_catalog().add(support)

        strategy = default_catalog().get_strategy(
            AgentTransport.GENERIC, command="fake-catalog-strategy"
        )
        assert isinstance(strategy, FakeAgentStrategy)

    def test_name_lookup_via_default_catalog(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "fake-catalog-lookup",
            transport=AgentTransport.GENERIC,
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
        )
        default_catalog().add(support)

        found = default_catalog().get("fake-catalog-lookup")
        assert found is not None
        assert isinstance(found.parser_factory(), FakeAgentParser)
        parser = default_catalog().get_parser("fake-catalog-lookup")
        assert isinstance(parser, FakeAgentParser)


def test_register_agent_support_headless_and_interactive_lockstep() -> None:
    assert AgentCatalog is ralph.agents.AgentCatalog
    assert AgentRegistry is ralph.agents.AgentRegistry
    assert register_agent_support is ralph.agents.register_agent_support
    assert default_catalog is ralph.agents.default_catalog

    headless_name = "lockstep-headless"
    interactive_name = "lockstep-interactive"

    catalog = AgentCatalog()
    registry = AgentRegistry(catalog=catalog)

    register_agent_support(
        headless_name,
        transport=AgentTransport.GENERIC,
        parser_factory=FakeAgentParser,
        strategy_factory=FakeAgentStrategy,
        agent_registry=registry,
        json_parser=JsonParserType.GENERIC,
        interactive=False,
    )
    register_agent_support(
        interactive_name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=FakeAgentParser,
        strategy_factory=FakeInteractiveAgentStrategy,
        agent_registry=registry,
        json_parser=JsonParserType.GENERIC,
        interactive=True,
    )

    headless_catalog_entry = catalog.get(headless_name)
    interactive_catalog_entry = catalog.get(interactive_name)
    assert headless_catalog_entry is not None
    assert interactive_catalog_entry is not None
    assert headless_catalog_entry.name == headless_name
    assert interactive_catalog_entry.name == interactive_name
    assert headless_catalog_entry.transport == AgentTransport.GENERIC
    assert interactive_catalog_entry.transport == AgentTransport.CLAUDE_INTERACTIVE

    headless_config = registry.get(headless_name)
    interactive_config = registry.get(interactive_name)
    assert headless_config is not None
    assert interactive_config is not None
    assert headless_config.transport == AgentTransport.GENERIC
    assert interactive_config.transport == AgentTransport.CLAUDE_INTERACTIVE

    assert default_catalog().get(headless_name) is None
    assert default_catalog().get(interactive_name) is None

    registry.unregister(headless_name)

    assert catalog.get(headless_name) is None
    assert registry.get(headless_name) is None
    assert catalog.get(interactive_name) is not None
    interactive_still = registry.get(interactive_name)
    assert interactive_still is not None
    assert interactive_still.transport == AgentTransport.CLAUDE_INTERACTIVE

    assert default_catalog().get(headless_name) is None
    assert default_catalog().get(interactive_name) is None


class TestPostRefactorContract:
    def test_default_strategies_covers_every_transport(self) -> None:
        default_strategies = default_catalog()._default_strategies
        assert set(default_strategies) == set(AgentTransport), (
            f"AgentCatalog._default_strategies missing transport(s); "
            f"expected {sorted(AgentTransport)}, got {sorted(default_strategies)}"
        )
        for transport, factory in default_strategies.items():
            assert callable(factory), (
                f"AgentCatalog._default_strategies[{transport.name!r}] must be callable"
            )

    def test_agent_support_shape_parity_builtin_vs_kwargs(self) -> None:

        class _TemplateParser(ParserTemplateBase):
            _STOP_EVENT_TYPES = frozenset()

            def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
                stripped = line.strip()
                result = self.parse_json_line(stripped)
                if result is not None:
                    yield result
                else:
                    yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

        class _Strategy(BaseExecutionStrategy):
            pass

        from_kwargs = AgentSupport.from_registration_kwargs(
            "kw-args-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=_TemplateParser,
            strategy_factory=_Strategy,
            interactive=False,
        )
        from ralph.agents.display_capabilities import all_display_capabilities
        from ralph.agents.display_capability_stance import DisplayCapabilityStance

        complete_capabilities = tuple(
            DisplayCapabilityStance.supported(c) for c in all_display_capabilities()
        )
        spec_row = BuiltinAgentSpec(
            transport=AgentTransport.GENERIC,
            parser_factory=_TemplateParser,
            strategy_factory=_Strategy,
            display_capabilities=complete_capabilities,
        )
        from_builtin = spec_row.to_support("builtin-agent")

        expected_fields = {
            "name",
            "spec",
            "parser_factory",
            "strategy_factory",
            "config",
            "is_builtin",
            "no_default_session_flag",
            "session_identifier_observable",
            "display_capabilities",
            "_name_lower",
            "dynamic_alias_help",
            "dynamic_alias_help_prefix",
            "empty_output_diagnostic_factory",
            "empty_output_diagnostic_prefix",
        }
        assert set(from_kwargs.__dataclass_fields__) == expected_fields, (
            f"AgentSupport field set changed; expected {expected_fields}, "
            f"got {set(from_kwargs.__dataclass_fields__)}"
        )
        assert set(from_builtin.__dataclass_fields__) == expected_fields, (
            f"AgentSupport field set changed; expected {expected_fields}, "
            f"got {set(from_builtin.__dataclass_fields__)}"
        )
        assert from_kwargs.__dataclass_params__.frozen is True
        assert from_builtin.__dataclass_params__.frozen is True
        assert from_kwargs.is_builtin is False
        assert from_builtin.is_builtin is True

    def test_agent_support_is_frozen_cannot_mutate(self) -> None:

        class _TemplateParser(ParserTemplateBase):
            _STOP_EVENT_TYPES = frozenset()

            def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
                stripped = line.strip()
                result = self.parse_json_line(stripped)
                if result is not None:
                    yield result
                else:
                    yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

        class _Strategy(BaseExecutionStrategy):
            pass

        support = AgentSupport.from_registration_kwargs(
            "frozen-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=_TemplateParser,
            strategy_factory=_Strategy,
        )
        with pytest.raises(FrozenInstanceError):
            support.__setattr__("name", "renamed")

    def test_agent_spec_consistency_validators_still_reject_inconsistent_state(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="requires_pty=True requires interactive=True"):
            AgentSpec(
                name="bad",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                interactive=False,
                requires_pty=True,
            )
