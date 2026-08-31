"""Declarative registry of the nine built-in agent CLIs.

This module is the single source of truth for the agents that Ralph Workflow
ships with out of the box. Each entry is a :class:`~ralph.agents.builtin_spec.BuiltinAgentSpec`
declaratively describing one CLI: the transport, the parser/strategy pair,
the JSON parsing mode, the executable, the flags used for unattended
("yolo") invocation, resume/session support, and whether the agent is allowed
to author commits.

The nine built-in agents are:

- ``claude`` (Claude Code interactive / PTY transport)
- ``claude-headless`` (Claude Code headless JSON-stream transport)
- ``codex`` (Codex CLI)
- ``opencode`` (OpenCode CLI)
- ``nanocoder`` (Nanocoder CLI)
- ``agy`` (AGY CLI; binary overridable via ``RALPH_AGY_BINARY``)
- ``pi`` (Pi.dev CLI)
- ``cursor`` (Cursor Agent CLI; binary overridable via ``RALPH_CURSOR_BINARY``)
- ``kimi`` (Kimi Code CLI; binary overridable via ``RALPH_KIMI_BINARY``)

Adding a new built-in agent requires editing this module only; the catalog
picks the entries up via :func:`builtin_supports`. Custom agents configured
via ``.agent/agents.toml`` are layered on top by the catalog and do not need
to be declared here.

Side effects: none at import time. The agent supports are returned as a
fresh tuple on each call to :func:`builtin_supports` so callers can iterate
without sharing state.
"""

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

# Imported at module level (NOT lazily): ``ralph.agents.registry`` imports
# this module lazily via ``_builtin_supports_lazy``, so the
# builtin->registry edge keeps the module-level dependency graph acyclic.
from ralph.agents.registry import agy_alias_help
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents.support import AgentSupport


#: Capability stance set used for the four built-in agents whose
#: smoke path is currently exercised end-to-end through committed
#: wire-format fixtures and whose parser produces a metadata
#: envelope the canonical ``payload_from_tool_event`` recognizes
#: for read, write, and edit tool calls. The four agents using this
#: constant are claude, claude-headless, agy, and cursor. codex and
#: pi carry their own ``display_capabilities`` tuples because their
#: respective parsers do not (yet) surface the full set; see the
#: inline declarations below for the per-agent reasons.
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

#: Codex ships its own JSON envelope that the shared
#: ``payload_from_tool_event`` does not yet recognize. The capability
#: is ``UNIMPLEMENTED`` with a measurable reason so a future fix can
#: promote it to SUPPORTED once the parser is extended.
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

#: OpenCode is the agent whose parsing defect the S-1/S-3 plan exists to
#: repair. The parser now normalizes the live 1.18.14 ``ralph_*`` tool
#: names at the transport boundary so the canonical preview payload
#: builder recognizes ``read_file`` / ``write_file`` / ``edit_file`` -- the
#: three capabilities exercised by the captured fixture
#: (``tests/display/_fixtures/opencode_wire_provenance.md``). The
#: ``tests/test_opencode_display_fidelity.py`` regression tests pin each
#: surface against the captured frame shape.
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

#: Nanocoder is a local-only TUI; its plain-text parser does not produce
#: a structured tool envelope the preview builder recognizes. The agent
#: is structurally capable of file operations through Ralph's local
#: filesystem primitives but its parser surfaces no metadata that
#: ``payload_from_tool_event`` can route, hence ``UNIMPLEMENTED`` with a
#: specific reason rather than ``NOT_APPLICABLE``.
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

#: Pi.dev emits a JSON envelope whose parser produces tool metadata
#: with file paths, but the parser does not currently route through
#: ``payload_from_tool_event``. ``UNIMPLEMENTED`` is the honest
#: stance until a measured run confirms the renderer side.
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


#: Kimi Code's parser preserves the upstream tool name verbatim in
#: ``metadata["tool"]`` (the measured v0.36.1 wire carries kimi's native
#: tool vocabulary with JSON-string ``arguments``), and does not yet
#: normalize those names onto the canonical ``payload_from_tool_event``
#: vocabulary. ``UNIMPLEMENTED`` with a measurable reason is the honest
#: stance until a captured fixture proves the renderer side.
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
        can_commit=True,
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
        can_commit=True,
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
        can_commit=True,
        display_capabilities=_CODEX_CAPABILITIES,
    ).to_support("codex"),
    BuiltinAgentSpec(
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=OpenCodeExecutionStrategy,
        json_parser=JsonParserType.OPENCODE,
        cmd="opencode",
        # No output_flag: opencode 1.18.25 exposes only
        # ``--format default|json`` (``opencode run --help``) and has no
        # ``--json-stream``. The command builder declares
        # ``honors_output_flag=False`` for this transport, so any operator
        # ``[agents.opencode].output_flag`` override is dropped by
        # declaration rather than by accident.
        # Unattended runs have nobody to answer a permission prompt, and
        # OpenCode auto-REJECTS anything it cannot match. ``--auto`` approves
        # what is not explicitly denied, so operator denies still win.
        yolo_flag="--auto",
        can_commit=False,
        session_flag="--session {}",
        display_capabilities=_OPENCODE_CAPABILITIES,
    ).to_support("opencode"),
    BuiltinAgentSpec(
        transport=AgentTransport.NANOCODER,
        parser_factory=NanocoderParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        cmd="nanocoder",
        can_commit=False,
        interactive=True,
        no_default_session_flag=True,
        # S-6 (Evidence Provenance G6 / DoD 20): the one documented False
        # case. Upstream documentation review (see
        # docs/superpowers/specs/2026-06-07-nanocoder-support-design.md's
        # "Session and Retry Policy" section) found no documented
        # unattended `run`-mode session/resume output of any kind, and
        # NanocoderParser (plain-text PTY redraw, no JSON session
        # protocol) confirms there is no mechanism to observe one --
        # unlike AGY, which also has no_default_session_flag=True but
        # DOES emit an observable conversation_id via its JSON init
        # frame and is therefore NOT exempted from the smoke gate's
        # session-id check.
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
        can_commit=True,
        interactive=True,
        no_default_session_flag=True,
        # Generic data-driven seams (no name-typed control flow downstream):
        # ``lookup_dynamic_alias_help`` serves this callable for unknown
        # ``agy/<model>`` aliases, and ``lookup_empty_output_diagnostic_factory``
        # routes empty-output diagnostics through it — any future agent
        # registers the same kwargs to get the same behaviour.
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
        can_commit=True,
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
        can_commit=True,
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
        can_commit=True,
        display_name="Kimi",
        display_capabilities=_KIMI_CAPABILITIES,
    ).to_support("kimi"),
)


def builtin_supports() -> tuple[AgentSupport, ...]:
    """Return a fresh copy of the built-in agent supports."""
    return tuple(_BUILTIN_AGENT_SUPPORTS)
