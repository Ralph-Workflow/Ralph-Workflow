"""Single declarative source of truth for the six built-in agents."""

from __future__ import annotations

from ralph.agents.execution_state._factory import _make_agy_strategy
from ralph.agents.execution_state.claude_execution_strategy import ClaudeExecutionStrategy
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

_BUILTIN_AGENT_SUPPORTS: tuple[AgentSupport, ...] = (
    AgentSupport.from_registration_kwargs(
        "claude",
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        can_commit=True,
        json_parser=JsonParserType.CLAUDE,
        session_flag="--resume {}",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=ClaudeInteractiveParser,
        strategy_factory=ClaudeInteractiveExecutionStrategy,
        interactive=True,
        is_builtin=True,
    ),
    AgentSupport.from_registration_kwargs(
        "claude-headless",
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        verbose_flag="--verbose",
        can_commit=True,
        json_parser=JsonParserType.CLAUDE,
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        transport=AgentTransport.CLAUDE,
        parser_factory=ClaudeParser,
        strategy_factory=ClaudeExecutionStrategy,
        interactive=False,
        is_builtin=True,
    ),
    AgentSupport.from_registration_kwargs(
        "codex",
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        can_commit=True,
        json_parser=JsonParserType.CODEX,
        transport=AgentTransport.CODEX,
        parser_factory=CodexParser,
        strategy_factory=GenericExecutionStrategy,
        interactive=False,
        is_builtin=True,
    ),
    AgentSupport.from_registration_kwargs(
        "opencode",
        cmd="opencode",
        output_flag="--json-stream",
        can_commit=False,
        json_parser=JsonParserType.OPENCODE,
        session_flag="--session {}",
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=OpenCodeExecutionStrategy,
        interactive=False,
        is_builtin=True,
    ),
    AgentSupport.from_registration_kwargs(
        "nanocoder",
        cmd="nanocoder",
        output_flag=None,
        can_commit=False,
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.NANOCODER,
        parser_factory=GenericParser,
        strategy_factory=GenericExecutionStrategy,
        interactive=False,
        is_builtin=True,
    ),
    AgentSupport.from_registration_kwargs(
        "agy",
        cmd="agy",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        can_commit=False,
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.AGY,
        parser_factory=GenericParser,
        strategy_factory=_make_agy_strategy,
        interactive=True,
        is_builtin=True,
    ),
)


def builtin_supports() -> tuple[AgentSupport, ...]:
    """Return a fresh copy of the built-in agent supports."""
    return tuple(_BUILTIN_AGENT_SUPPORTS)
