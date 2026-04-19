"""Activity router: parser → ActivityModel → RingBuffer."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.parsers import (
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    OpenCodeParser,
)
from ralph.display.activity_model import (
    ActivityEventKind,
    ActivityProvider,
    make_event,
    render_event_line,
)
from ralph.display.ring_buffer import PARALLEL_DISPLAY_BUFFER_SIZE, RingBuffer

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.parsers.base import AgentParser

PARSERS: dict[ActivityProvider, type[AgentParser]] = {
    ActivityProvider.CLAUDE: ClaudeParser,
    ActivityProvider.OPENCODE: OpenCodeParser,
    ActivityProvider.CODEX: CodexParser,
    ActivityProvider.GEMINI: GeminiParser,
    ActivityProvider.GENERIC: cast("type[AgentParser]", GenericParser),
}


def _default_parser_factory(provider: ActivityProvider) -> AgentParser:
    parser_cls = PARSERS.get(provider, cast("type[AgentParser]", GenericParser))
    return parser_cls()


def detect_provider_from_command(command: list[str]) -> ActivityProvider:
    if not command:
        return ActivityProvider.GENERIC
    argv0 = command[0]
    if "claude" in argv0:
        return ActivityProvider.CLAUDE
    if "opencode" in argv0:
        return ActivityProvider.OPENCODE
    if "codex" in argv0 or "aider" in argv0:
        return ActivityProvider.CODEX
    if "gemini" in argv0:
        return ActivityProvider.GEMINI
    return ActivityProvider.GENERIC


def _map_kind(parser_type: str) -> ActivityEventKind:
    mapping: dict[str, ActivityEventKind] = {
        "text": ActivityEventKind.TEXT,
        "tool_use": ActivityEventKind.TOOL_USE,
        "tool_result": ActivityEventKind.TOOL_RESULT,
        "error": ActivityEventKind.ERROR,
        "status": ActivityEventKind.STATUS,
        "lifecycle": ActivityEventKind.LIFECYCLE,
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
    ) -> None:
        self._parser_factory = parser_factory or _default_parser_factory
        self._buffer_factory = buffer_factory or (
            lambda: RingBuffer(maxsize=PARALLEL_DISPLAY_BUFFER_SIZE)
        )
        self._parsers: dict[str, AgentParser] = {}
        self._buffers: dict[str, RingBuffer] = {}

    def get_buffer(self, unit_id: str) -> RingBuffer:
        if unit_id not in self._buffers:
            self._buffers[unit_id] = self._buffer_factory()
        return self._buffers[unit_id]

    def push_raw_line(
        self,
        unit_id: str,
        raw_line: str,
        *,
        provider: ActivityProvider = ActivityProvider.GENERIC,
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
                event = make_event(
                    provider=provider,
                    kind=_map_kind(out.type),
                    content=out.content,
                    metadata=out.metadata or {},
                    source=unit_id,
                )
                rendered = render_event_line(event.kind, event.content, timestamp=event.timestamp)
                buffer.enqueue(rendered)
        except Exception as exc:
            error_event = make_event(
                provider=provider,
                kind=ActivityEventKind.ERROR,
                content=f"parser error: {exc}",
                source=unit_id,
            )
            rendered = render_event_line(
                error_event.kind, error_event.content, timestamp=error_event.timestamp
            )
            buffer.enqueue(rendered)


__all__ = [
    "PARSERS",
    "ActivityRouter",
    "detect_provider_from_command",
]
