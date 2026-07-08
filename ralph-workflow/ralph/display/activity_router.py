"""Activity router: parser → ActivityModel → RingBuffer."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from ralph.display.activity_model import (
    ActivityEventKind,
    ActivityProvider,
    EventOptions,
    make_event,
    render_event_line,
)
from ralph.display.ring_buffer import PARALLEL_DISPLAY_BUFFER_SIZE, RingBuffer

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.agents.parsers.base import AgentParser

# Parser imports are deferred: ``ralph.agents.parsers`` pulls in
# ``ralph.display.vt_normalizer`` -> ``ralph.display`` -> ``ralph.display.parallel_display``
# -> this module, so the eager import below would close a cycle.
# bounded-accumulator-ok: fixed dispatch table keyed by ActivityProvider enum
PARSERS: dict[ActivityProvider, type[AgentParser]] = {}  # bounded-accumulator-ok


def _build_parsers() -> dict[ActivityProvider, type[AgentParser]]:
    """Populate ``PARSERS`` on first use to break the parsers/display cycle."""
    from ralph.agents.parsers import (
        AgyParser,
        ClaudeInteractiveParser,
        ClaudeParser,
        CodexParser,
        CursorParser,
        GeminiParser,
        GenericParser,
        NanocoderParser,
        OpenCodeParser,
        PiParser,
    )

    return {
        ActivityProvider.AGY: AgyParser,
        ActivityProvider.CLAUDE: ClaudeParser,
        ActivityProvider.CLAUDE_INTERACTIVE: cast(
            "type[AgentParser]", ClaudeInteractiveParser
        ),
        ActivityProvider.OPENCODE: OpenCodeParser,
        ActivityProvider.CODEX: CodexParser,
        ActivityProvider.CURSOR: CursorParser,
        ActivityProvider.GEMINI: GeminiParser,
        ActivityProvider.NANOCODER: cast("type[AgentParser]", NanocoderParser),
        ActivityProvider.PI: cast("type[AgentParser]", PiParser),
        ActivityProvider.GENERIC: cast("type[AgentParser]", GenericParser),
    }


def _default_parser_factory(provider: ActivityProvider) -> AgentParser:
    if not PARSERS:
        PARSERS.update(_build_parsers())
    parser_cls = PARSERS.get(provider)
    if parser_cls is None:
        from ralph.agents.parsers import GenericParser

        parser_cls = cast("type[AgentParser]", GenericParser)
    return parser_cls()


def detect_provider_from_command(command: list[str]) -> ActivityProvider:
    """Infer the ``ActivityProvider`` from the agent command executable name."""
    if not command:
        return ActivityProvider.GENERIC
    argv0 = command[0].lower() if command[0] else ""

    # Map substrings to providers (checked in order). The ``pi``
    # substring is checked BEFORE ``opencode`` is irrelevant because
    # ``opencode`` is a substring of nothing containing ``pi``, but we
    # still order ``claude_interactive`` before ``claude`` so an
    # explicit ``*-claude-interactive`` wrapper binary is routed to
    # ``CLAUDE_INTERACTIVE`` rather than ``CLAUDE``.
    substrings_to_provider: list[tuple[str, ActivityProvider]] = [
        ("claude_interactive", ActivityProvider.CLAUDE_INTERACTIVE),
        ("claude-interactive", ActivityProvider.CLAUDE_INTERACTIVE),
        ("agy", ActivityProvider.AGY),
        ("claude", ActivityProvider.CLAUDE),
        ("opencode", ActivityProvider.OPENCODE),
        ("nanocoder", ActivityProvider.NANOCODER),
        ("cursor", ActivityProvider.CURSOR),
        ("codex", ActivityProvider.CODEX),
        ("aider", ActivityProvider.CODEX),
        ("gemini", ActivityProvider.GEMINI),
        ("pi", ActivityProvider.PI),
    ]

    for substring, provider in substrings_to_provider:
        if substring in argv0:
            return provider

    return ActivityProvider.GENERIC


def map_parser_type_to_kind(parser_type: str) -> ActivityEventKind:
    """Convert a parser output type string to the canonical ``ActivityEventKind``."""
    mapping: dict[str, ActivityEventKind] = {
        "text": ActivityEventKind.TEXT,
        "output": ActivityEventKind.TEXT,
        "thinking": ActivityEventKind.THINKING,
        "tool_use": ActivityEventKind.TOOL_USE,
        "tool_result": ActivityEventKind.TOOL_RESULT,
        "error": ActivityEventKind.ERROR,
        "status": ActivityEventKind.STATUS,
        "lifecycle": ActivityEventKind.LIFECYCLE,
        "subagent_progress": ActivityEventKind.SUBAGENT_PROGRESS,
    }
    return mapping.get(parser_type, ActivityEventKind.UNKNOWN)


class ActivityRouter:
    """Wire a per-unit parser to its RingBuffer via the typed activity model.

    Each *unit_id* owns an isolated parser instance and an isolated ring buffer
    so output from different workers never interferes.  Parser exceptions are
    caught per-line and recorded as ERROR events — a malformed line must never
    crash the caller.
    """

    def __init__(
        self,
        *,
        parser_factory: Callable[[ActivityProvider], AgentParser] | None = None,
        buffer_factory: Callable[[], RingBuffer] | None = None,
        on_event: (
            Callable[[str, ActivityEventKind, str | None, str | None, dict[str, object]], None]
            | None
        ) = None,
        raw_overflow_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._parser_factory = parser_factory or _default_parser_factory
        self._buffer_factory = buffer_factory or (
            lambda: RingBuffer(maxsize=PARALLEL_DISPLAY_BUFFER_SIZE)
        )
        # bounded-accumulator-ok: per-unit; drained by ActivityRouter.drop_unit(unit_id)
        self._parsers: dict[str, AgentParser] = {}  # bounded-accumulator-ok: drop_unit
        self._buffers: dict[str, RingBuffer] = {}  # bounded-accumulator-ok: drop_unit
        self._on_event = on_event
        self._raw_overflow_callback = raw_overflow_callback

    def get_buffer(self, unit_id: str) -> RingBuffer:
        if unit_id not in self._buffers:
            self._buffers[unit_id] = self._buffer_factory()
        return self._buffers[unit_id]

    def drop_unit(self, unit_id: str) -> None:
        """Release per-unit state so long parallel sessions don't accumulate state across waves.

        Removes the unit's ``RingBuffer`` and ``AgentParser`` entries from
        ``self._buffers`` and ``self._parsers`` so the per-unit memory
        is released when the unit is no longer needed. Safe to call for
        a unit that was never added; it just no-ops.
        """
        self._buffers.pop(unit_id, None)
        self._parsers.pop(unit_id, None)

    def push_raw_line(
        self,
        unit_id: str,
        raw_line: str,
        *,
        provider: ActivityProvider = ActivityProvider.GENERIC,
        raw_reference: str | None = None,
    ) -> None:
        """Never raises — parser failures are converted to ERROR events."""
        buffer = self.get_buffer(unit_id)

        try:
            parser = self._parsers.get(unit_id)
            if parser is None:
                parser = self._parser_factory(provider)
                self._parsers[unit_id] = parser

            lines = list(parser.parse(iter([raw_line])))

            for out in lines:
                kind = map_parser_type_to_kind(out.type)
                event = make_event(
                    provider=provider,
                    kind=kind,
                    options=EventOptions(
                        content=out.content,
                        metadata=out.metadata or {},
                        source=unit_id,
                    ),
                )
                rendered = render_event_line(event.kind, event.content, timestamp=event.timestamp)
                buffer.enqueue(rendered)
                if self._on_event is not None:
                    self._on_event(unit_id, kind, event.content, raw_reference, out.metadata or {})
                self._dispatch_subagent_progress(parser, out, unit_id, raw_reference)
        except Exception as exc:
            if self._raw_overflow_callback is not None:
                with contextlib.suppress(Exception):
                    self._raw_overflow_callback(unit_id, raw_line)
            error_event = make_event(
                provider=provider,
                kind=ActivityEventKind.ERROR,
                options=EventOptions(
                    content=f"parser error: {exc}",
                    source=unit_id,
                ),
            )
            rendered = render_event_line(
                error_event.kind, error_event.content, timestamp=error_event.timestamp
            )
            buffer.enqueue(rendered)
            if self._on_event is not None:
                self._on_event(
                    unit_id, ActivityEventKind.ERROR, error_event.content, raw_reference, {}
                )

    def _dispatch_subagent_progress(
        self,
        parser: AgentParser,
        out: AgentOutputLine,
        unit_id: str,
        raw_reference: str | None,
    ) -> None:
        """Forward parsed lines to the subagent sink and emit SUBAGENT_PROGRESS.

        Mirrors ``stream_parsed_agent_activity`` (in
        ``ralph/pipeline/activity_stream.py``): when the parser
        implements ``emit_subagent_activity`` (the shared hook used by
        Claude/OpenCode/Codex/Gemini/Pi/Agy/Generic), invoke it with a
        capturing sink, then (a) forward the captured summary to the
        per-task subagent sink via ``invoke_subagent_sink`` so the idle
        watchdog's ``record_subagent_work`` channel stays fresh, and
        (b) emit a ``SUBAGENT_PROGRESS`` event through the
        ``_on_event`` callback so the operator sees real-time per-tool
        subagent progress on the console transcript.

        The hook is fail-soft: a buggy hook cannot crash the router
        because the exception is swallowed and the inner ``sink``
        capture re-raises into ``invoke_subagent_sink`` which is
        already exception-swallowing at the helper boundary.
        """
        emit_hook: object = getattr(parser, "emit_subagent_activity", None)
        if not callable(emit_hook):
            return
        captured: list[str] = []
        try:
            cast(
                "Callable[[AgentOutputLine, Callable[[str], None]], None]",
                emit_hook,
            )(out, captured.append)
        except Exception:
            return
        if not captured:
            return
        summary = captured[0]
        try:
            from ralph.mcp.server._activity_sink import invoke_subagent_sink

            invoke_subagent_sink(summary)
        except Exception:
            pass
        if self._on_event is not None:
            with contextlib.suppress(Exception):
                self._on_event(
                    unit_id,
                    ActivityEventKind.SUBAGENT_PROGRESS,
                    summary,
                    raw_reference,
                    out.metadata or {},
                )


__all__ = [
    "PARSERS",
    "ActivityRouter",
    "detect_provider_from_command",
    "map_parser_type_to_kind",
]
