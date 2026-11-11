
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents._agy_upstream_diagnostic import agy_empty_output_reason
from ralph.agents.builtin_spec import BuiltinAgentSpec
from ralph.agents.display_capabilities import DisplayCapability
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.agents.execution_state._factory import (
    _make_agy_strategy,
    _make_cursor_strategy,
    _make_kimi_strategy,
    _make_pi_strategy,
)
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
from ralph.agents.parsers.cursor import CursorParser
from ralph.agents.parsers.kimi import KimiParser
from ralph.agents.parsers.nanocoder import NanocoderParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.parsers.pi import PiParser
from ralph.agents.registry import agy_alias_help
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.support import AgentSupport


_CLAUDE_LEVEL_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.supported(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        detail="read/write tool names normalized by parser match payload_from_tool_event",
    ),
    DisplayCapabilityStance.supported(
        DisplayCapability.FILE_PREVIEW,
        detail="read/write tool names normalized by parser match payload_from_tool_event",
    ),
    DisplayCapabilityStance.supported(
        DisplayCapability.EDIT_DIFF,
        detail="edit/MultiEdit tool names normalized by parser match payload_from_tool_event",
    ),
)

_CODEX_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        reason="codex wire format uses distinct tool names not yet mapped to payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW,
        reason="codex wire format uses distinct tool names not yet mapped to payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.EDIT_DIFF,
        reason="codex wire format uses distinct tool names not yet mapped to payload_from_tool_event",
    ),
)

_OPENCODE_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.supported(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        detail="ralph_write_file tool_use normalized to write_file; payload_from_tool_event returns a write-shape preview (fixture:tests/display/_fixtures/opencode_wire_provenance.md)",
    ),
    DisplayCapabilityStance.supported(
        DisplayCapability.FILE_PREVIEW,
        detail="ralph_read_file tool_use normalized to read_file; payload_from_tool_event returns a read-shape preview (fixture:tests/display/_fixtures/opencode_wire_provenance.md)",
    ),
    DisplayCapabilityStance.supported(
        DisplayCapability.EDIT_DIFF,
        detail="ralph_edit_file tool_use normalized to edit_file; payload_from_tool_event returns a replace-shape preview (fixture:tests/display/_fixtures/opencode_wire_provenance.md)",
    ),
)

_NANOCODER_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        reason="nanocoder plain-text parser does not emit a structured tool_use envelope that payload_from_tool_event routes to syntax_preview",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW,
        reason="nanocoder plain-text parser does not emit a structured tool_use envelope that payload_from_tool_event routes to file_preview",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.EDIT_DIFF,
        reason="nanocoder plain-text parser does not emit a structured tool_use envelope that payload_from_tool_event routes to diff_preview",
    ),
)

_PI_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        reason="pi parser emits tool_use metadata that is not yet routed through payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW,
        reason="pi parser emits tool_use metadata that is not yet routed through payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.EDIT_DIFF,
        reason="pi parser emits tool_use metadata that is not yet routed through payload_from_tool_event",
    ),
)


_KIMI_CAPABILITIES: tuple[DisplayCapabilityStance, ...] = (
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.SYNTAX_HIGHLIGHTING,
        reason="kimi parser preserves upstream tool names that are not yet mapped to payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.FILE_PREVIEW,
        reason="kimi parser preserves upstream tool names that are not yet mapped to payload_from_tool_event",
    ),
    DisplayCapabilityStance.unimplemented(
        DisplayCapability.EDIT_DIFF,
        reason="kimi parser preserves upstream tool names that are not yet mapped to payload_from_tool_event",
    ),
)


_BUILTIN_AGENT_SUPPORTS: tuple[AgentSupport, ...] = (
    BuiltinAgentSpec(
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=ClaudeInteractiveParser,
        strategy_factory=ClaudeInteractiveExecutionStrategy,
        json_parser=JsonParserType.CLAUDE,
        cmd="claude",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        session_flag="--resume {}",
        interactive=True,
        display_capabilities=_CLAUDE_LEVEL_CAPABILITIES,
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
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        display_capabilities=_CLAUDE_LEVEL_CAPABILITIES,
    ).to_support("claude-headless"),
    BuiltinAgentSpec(
        transport=AgentTransport.CODEX,
        parser_factory=CodexParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.CODEX,
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--dangerously-bypass-approvals-and-sandbox",
        display_capabilities=_CODEX_CAPABILITIES,
    ).to_support("codex"),
    BuiltinAgentSpec(
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=OpenCodeExecutionStrategy,
        json_parser=JsonParserType.OPENCODE,
        cmd="opencode",
        yolo_flag="--auto",
        session_flag="--session {}",
        display_capabilities=_OPENCODE_CAPABILITIES,
    ).to_support("opencode"),
    BuiltinAgentSpec(
        transport=AgentTransport.NANOCODER,
        parser_factory=NanocoderParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        cmd="nanocoder",
        interactive=True,
        no_default_session_flag=True,
        session_identifier_observable=False,
        display_capabilities=_NANOCODER_CAPABILITIES,
    ).to_support("nanocoder"),
    BuiltinAgentSpec(
        transport=AgentTransport.AGY,
        parser_factory=AgyParser,
        strategy_factory=_make_agy_strategy,
        json_parser=JsonParserType.GENERIC,
        cmd="agy",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        interactive=True,
        no_default_session_flag=True,
        dynamic_alias_help=agy_alias_help,
        dynamic_alias_help_prefix=("agy",),
        empty_output_diagnostic_factory=agy_empty_output_reason,
        empty_output_diagnostic_prefix=("agy",),
        display_capabilities=_CLAUDE_LEVEL_CAPABILITIES,
    ).to_support("agy"),
    BuiltinAgentSpec(
        transport=AgentTransport.PI,
        parser_factory=PiParser,
        strategy_factory=_make_pi_strategy,
        json_parser=JsonParserType.PI,
        cmd="pi",
        output_flag="--mode json",
        yolo_flag="--approve",
        session_flag="--session {}",
        display_name="Pi",
        display_capabilities=_PI_CAPABILITIES,
    ).to_support("pi"),
    BuiltinAgentSpec(
        transport=AgentTransport.CURSOR,
        parser_factory=CursorParser,
        strategy_factory=_make_cursor_strategy,
        json_parser=JsonParserType.GENERIC,
        cmd="agent",
        output_flag="--output-format stream-json",
        yolo_flag="--yolo",
        print_flag="--print",
        streaming_flag="--stream-partial-output",
        session_flag="--resume {}",
        display_name="Cursor",
        display_capabilities=_CLAUDE_LEVEL_CAPABILITIES,
    ).to_support("cursor"),
    BuiltinAgentSpec(
        transport=AgentTransport.KIMI,
        parser_factory=KimiParser,
        strategy_factory=_make_kimi_strategy,
        json_parser=JsonParserType.GENERIC,
        cmd="kimi",
        output_flag="--output-format=stream-json",
        yolo_flag=None,
        print_flag="-p",
        session_flag="-S {}",
        display_name="Kimi",
        display_capabilities=_KIMI_CAPABILITIES,
    ).to_support("kimi"),
)


def builtin_supports() -> tuple[AgentSupport, ...]:
    return tuple(_BUILTIN_AGENT_SUPPORTS)
