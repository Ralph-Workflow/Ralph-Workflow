
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

PARSERS: dict[ActivityProvider, type[AgentParser]] = {}


def _build_parsers() -> dict[ActivityProvider, type[AgentParser]]:
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
        ActivityProvider.CLAUDE_INTERACTIVE: cast("type[AgentParser]", ClaudeInteractiveParser),
        ActivityProvider.OPENCODE: OpenCodeParser,
        ActivityProvider.CODEX: CodexParser,
        ActivityProvider.CURSOR: CursorParser,
        ActivityProvider.GEMINI: GeminiParser,
        ActivityProvider.NANOCODER: cast(
            "type[AgentParser]", NanocoderParser
        ),
        ActivityProvider.PI: cast(
            "type[AgentParser]", PiParser
        ),
        ActivityProvider.GENERIC: cast(
            "type[AgentParser]", GenericParser
        ),
    }


def _default_parser_factory(provider: ActivityProvider) -> AgentParser:
    if not PARSERS:
        PARSERS.update(_build_parsers())
    parser_cls = PARSERS.get(provider)
    if parser_cls is None:
        from ralph.agents.parsers import GenericParser

        parser_cls = cast(
            "type[AgentParser]", GenericParser
        )
    return parser_cls()


def detect_provider_from_command(command: list[str]) -> ActivityProvider:
    if not command:
        return ActivityProvider.GENERIC
    argv0 = command[0].lower() if command[0] else ""

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
        "stop": ActivityEventKind.LIFECYCLE,
        "session": ActivityEventKind.LIFECYCLE,
        "auto_retry_end": ActivityEventKind.LIFECYCLE,
        "agent_settled": ActivityEventKind.LIFECYCLE,
        "auto_retry_start": ActivityEventKind.STATUS,
    }
    return mapping.get(parser_type, ActivityEventKind.UNKNOWN)


class ActivityRouter:

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
        self._parsers: dict[str, AgentParser] = {}
        self._buffers: dict[str, RingBuffer] = {}
        self._on_event = on_event
        self._raw_overflow_callback = raw_overflow_callback

    def get_buffer(self, unit_id: str) -> RingBuffer:
        if unit_id not in self._buffers:
            self._buffers[unit_id] = self._buffer_factory()
        return self._buffers[unit_id]

    def drop_unit(self, unit_id: str) -> None:
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
