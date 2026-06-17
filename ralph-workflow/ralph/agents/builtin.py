"""Single declarative source of truth for the seven built-in agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.builtin_spec import BuiltinAgentSpec
from ralph.agents.execution_state._factory import _make_agy_strategy
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.parsers.agy import AgyParser
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.parsers.pi import PiParser
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.support import AgentSupport


_BUILTIN_AGENT_SUPPORTS: tuple[AgentSupport, ...] = (
    BuiltinAgentSpec(
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=ClaudeInteractiveParser,
        strategy_factory=ClaudeInteractiveExecutionStrategy,
        json_parser=JsonParserType.CLAUDE,
        cmd="claude",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        can_commit=True,
        session_flag="--resume {}",
        interactive=True,
    ).to_support("claude"),
    BuiltinAgentSpec(
        transport=AgentTransport.CLAUDE,
        parser_factory=ClaudeParser,
        strategy_factory=ClaudeExecutionStrategy,
        json_parser=JsonParserType.CLAUDE,
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        can_commit=True,
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
    ).to_support("claude-headless"),
    BuiltinAgentSpec(
        transport=AgentTransport.CODEX,
        parser_factory=CodexParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.CODEX,
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        can_commit=True,
    ).to_support("codex"),
    BuiltinAgentSpec(
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=OpenCodeExecutionStrategy,
        json_parser=JsonParserType.OPENCODE,
        cmd="opencode",
        output_flag="--json-stream",
        can_commit=False,
        session_flag="--session {}",
    ).to_support("opencode"),
    BuiltinAgentSpec(
        transport=AgentTransport.NANOCODER,
        parser_factory=GenericParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        cmd="nanocoder",
        can_commit=False,
    ).to_support("nanocoder"),
    BuiltinAgentSpec(
        transport=AgentTransport.AGY,
        parser_factory=AgyParser,
        strategy_factory=_make_agy_strategy,
        json_parser=JsonParserType.GENERIC,
        cmd="agy",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        can_commit=False,
        interactive=True,
        no_default_session_flag=True,
    ).to_support("agy"),
    BuiltinAgentSpec(
        transport=AgentTransport.PI,
        parser_factory=PiParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.PI,
        cmd="pi",
        output_flag="--mode json",
        yolo_flag="--approve",
        session_flag="--session {}",
        can_commit=True,
        display_name="Pi",
    ).to_support("pi"),
)


def builtin_supports() -> tuple[AgentSupport, ...]:
    """Return a fresh copy of the built-in agent supports."""
    return tuple(_BUILTIN_AGENT_SUPPORTS)
