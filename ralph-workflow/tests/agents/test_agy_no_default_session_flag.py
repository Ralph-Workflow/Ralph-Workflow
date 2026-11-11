from __future__ import annotations

from ralph.agents.builtin import builtin_supports
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport, JsonParserType


class TestAgyNoDefaultSessionFlag:
    def test_agy_config_session_flag_is_none(self) -> None:
        agy = next(s for s in builtin_supports() if s.name == "agy")
        assert agy.config.session_flag is None, (
            f"agy session_flag must be None, got {agy.config.session_flag!r}"
        )

    def test_agy_support_has_no_default_session_flag_true(self) -> None:
        agy = next(s for s in builtin_supports() if s.name == "agy")
        assert agy.no_default_session_flag is True, (
            f"agy must opt out of the default session flag, got {agy.no_default_session_flag!r}"
        )

    def test_agy_keeps_no_session_flag_via_explicit_field(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            name="agy-shaped",
            transport=AgentTransport.AGY,
            parser_factory=ClaudeParser,
            strategy_factory=ClaudeExecutionStrategy,
            agent_registry=registry,
            json_parser=JsonParserType.GENERIC,
            interactive=True,
            no_default_session_flag=True,
        )
        support = registry.catalog.get("agy-shaped")
        assert support is not None
        assert support.config.session_flag is None, (
            f"Expected session_flag=None with no_default_session_flag=True, "
            f"got {support.config.session_flag!r}"
        )
        assert support.no_default_session_flag is True

    def test_interactive_without_flag_still_gets_default(self) -> None:
        registry = AgentRegistry()
        register_agent_support(
            name="claude-shaped",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser_factory=ClaudeParser,
            strategy_factory=ClaudeExecutionStrategy,
            agent_registry=registry,
            json_parser=JsonParserType.CLAUDE,
            interactive=True,
        )
        support = registry.catalog.get("claude-shaped")
        assert support is not None
        assert support.config.session_flag == "--resume {}", (
            f"Expected default --resume {{}} for interactive agent without "
            f"opt-out, got {support.config.session_flag!r}"
        )
        assert support.no_default_session_flag is False
