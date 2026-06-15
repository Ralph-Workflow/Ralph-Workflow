"""Black-box tests for the unified register_agent_support API.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.execution_state import BaseExecutionStrategy, strategy_for_command
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import (
    _PARSER_REGISTRY,
    AgentOutputLine,
    get_parser,
    resolve_parser_key,
)
from ralph.agents.registration import get_registered_agent_support, register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.commit import collect_commit_agent_output
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.display.context import make_display_context
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.plumbing.smoke_plumbing import (
    _count_parsed_events,
    _parser_key_for_config,
    _tool_activity_seen,
)

from ._registration_test_utils import _isolated_registries

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.process.child_liveness import ChildLivenessRegistry


class FakeAgentParser:
    """Pass-through parser used to prove registration wiring."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class FakeTextParser:
    """Pass-through parser that yields text lines for display rendering."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="text", content=line, raw=line)


class FakeAgentStrategy(BaseExecutionStrategy):
    """Minimal custom strategy that inherits all defaults."""


class FakeInteractiveAgentStrategy(BaseExecutionStrategy):
    """Minimal interactive strategy that inherits all defaults."""


class KwargsAwareStrategy(BaseExecutionStrategy):
    """Strategy that records the kwargs it received at construction time."""

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: object | None = None,
    ) -> None:
        self.received_label_scope = label_scope
        self.received_registry = registry


class TestRegisterAgentSupport:
    """Unified API writes into parser, strategy, and agent-name registries."""

    def test_registers_headless_agent_round_trip(self) -> None:
        with _isolated_registries():
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
            pair = get_registered_agent_support("fake")
            assert pair is not None
            assert isinstance(pair[0], FakeAgentParser)
            assert isinstance(pair[1], FakeAgentStrategy)
            assert "fake" in registry.agents
            assert registry.agents["fake"].transport == AgentTransport.GENERIC

    def test_fake_parser_yields_expected_output_lines(self) -> None:
        with _isolated_registries():
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
        with _isolated_registries():
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
        with _isolated_registries():
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
            pair = get_registered_agent_support("fake-interactive")
            assert pair is not None
            assert isinstance(pair[1], FakeInteractiveAgentStrategy)
            assert "fake-interactive" in registry.agents

    def test_independent_registrations_do_not_cross_contaminate(self) -> None:
        with _isolated_registries():
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
        with _isolated_registries():
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
        with _isolated_registries():
            registry = AgentRegistry()

            config = register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            assert config is registry.agents["fake"]

    def test_isolation_context_restores_registries(self) -> None:
        baseline_parsers = dict(_PARSER_REGISTRY)
        baseline_strategies = dict(_STRATEGY_DISPATCH)

        with _isolated_registries():
            registry = AgentRegistry()
            register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )
            assert "fake" in _PARSER_REGISTRY
            assert AgentTransport.GENERIC in _STRATEGY_DISPATCH

        assert dict(_PARSER_REGISTRY) == baseline_parsers
        assert dict(_STRATEGY_DISPATCH) == baseline_strategies

    def test_strategy_factory_kwargs_are_preserved_when_accepted(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()
            fake_registry = cast("ChildLivenessRegistry", object())

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

            strategy_factory = _STRATEGY_DISPATCH[AgentTransport.GENERIC]
            strategy = strategy_factory(label_scope="scope-x", registry=fake_registry)
            assert isinstance(strategy, KwargsAwareStrategy)
            assert strategy.received_label_scope == "scope-x"
            assert strategy.received_registry is fake_registry

    def test_strategy_factory_without_kwargs_still_works(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            register_agent_support(
                "fake-no-kwargs",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            strategy_factory = _STRATEGY_DISPATCH[AgentTransport.GENERIC]
            strategy = strategy_factory(
                label_scope="scope-x", registry=cast("ChildLivenessRegistry", object())
            )
            assert isinstance(strategy, FakeAgentStrategy)

    def test_commit_plumbing_uses_registered_parser(self) -> None:
        with _isolated_registries():
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
        with _isolated_registries():
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

            assert rendered == ["fake-stream: hello"]

    def test_custom_cmd_and_session_flag_override_defaults(self) -> None:
        with _isolated_registries():
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
                can_commit=True,
            )

            assert config.cmd == "my-agent-cli"
            assert config.session_flag == "--continue {}"
            assert config.output_flag == "--json"
            assert config.can_commit is True

    def test_interactive_default_session_flag_when_session_flag_omitted(self) -> None:
        with _isolated_registries():
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
    """Runtime parser resolution prefers registered command names."""

    def test_registered_agent_command_wins_over_generic_parser(self) -> None:
        with _isolated_registries():
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
            assert key == "fake-cmd"
            assert isinstance(get_parser(key), FakeAgentParser)

    def test_name_differs_from_cmd_registers_parser_under_command_token(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()
            config = register_agent_support(
                "my-agent",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
                cmd="my-agent-cli --json",
            )

            assert _parser_key_for_config(config) == "my-agent-cli"
            assert isinstance(get_parser("my-agent-cli"), FakeAgentParser)
            assert isinstance(get_parser("my-agent"), FakeAgentParser)
            assert _count_parsed_events(config, ["hello"]) == 1

    def test_registered_interactive_agent_command_wins_over_builtin_interactive_parser(
        self,
    ) -> None:
        with _isolated_registries():
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
        with _isolated_registries():
            key = resolve_parser_key(
                "claude", JsonParserType.GENERIC, AgentTransport.CLAUDE_INTERACTIVE
            )
            assert key == "claude_interactive"


class TestStrategyForCommand:
    """Runtime strategy resolution prefers the agent's command name."""

    def test_same_transport_agents_use_distinct_strategies(self) -> None:
        with _isolated_registries():
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
        with _isolated_registries():
            strategy = strategy_for_command("unknown", AgentTransport.GENERIC)
            assert type(strategy).__name__ == "GenericExecutionStrategy"

    def test_smoke_tool_activity_uses_command_specific_strategy(self) -> None:
        """A strategy registered for a command classifies activity for that command."""

        class LineYieldingStrategy(BaseExecutionStrategy):
            def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
                if line == "tool: hammer":
                    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
                return super().classify_activity_line(line)

        with _isolated_registries():
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
