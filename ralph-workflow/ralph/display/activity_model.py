"""Typed cross-layer activity contract for parser and display integration."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from rich.cells import cell_len
from rich.markup import escape

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.activity_visibility_hint import ActivityVisibilityHint
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.event_options import EventOptions
from ralph.display.line_sanitizer import strip_terminal_control

_module_sequence_lock = threading.Lock()


class _ModuleSequence:
    __slots__ = ("_counter",)

    def __init__(self) -> None:
        self._counter = 0

    def next(self) -> int:
        with _module_sequence_lock:
            self._counter += 1
            return self._counter


module_sequence = _ModuleSequence()


def make_event(
    *,
    provider: ActivityProvider,
    kind: ActivityEventKind,
    options: EventOptions | None = None,
) -> AgentActivityEvent:
    """Construct an AgentActivityEvent with an auto-incremented sequence and UTC timestamp."""
    opts = options or EventOptions()
    return AgentActivityEvent(
        provider=provider,
        kind=kind,
        content=opts.content,
        metadata=opts.metadata or {},
        visibility=opts.visibility,
        source=opts.source,
        sequence=module_sequence.next(),
        timestamp=datetime.now(UTC).isoformat(),
    )


_ICON_BY_KIND: dict[ActivityEventKind, str] = {
    ActivityEventKind.TEXT: "│",
    ActivityEventKind.THINKING: "∴",
    ActivityEventKind.STATUS: "▸",
    ActivityEventKind.TOOL_USE: "▸",
    ActivityEventKind.TOOL_RESULT: "✓",
    ActivityEventKind.ERROR: "✗",
    ActivityEventKind.LIFECYCLE: "◆",
    ActivityEventKind.HEARTBEAT: "·",
    ActivityEventKind.PROGRESS: "⏵",
    ActivityEventKind.SUBAGENT_PROGRESS: "⏵",
    ActivityEventKind.UNKNOWN: "?",
}


def _icon_for_kind(kind: ActivityEventKind) -> str:
    """Return the canonical icon for a kind, falling back to STATUS_STYLES.

    After the wt-028-display consolidation, agent-event iconography
    lives in the single registry (:mod:`ralph.display.agent_event_renderer`).
    :func:`render_event_line` keeps a local copy so the
    ring-buffer/activity-router path (which doesn't carry a
    ``DisplayContext``) can still produce a non-color carrier without
    importing the registry and pulling in its rich dependencies. New
    code should call :func:`ralph.display.agent_event_renderer.render_event_kind_text`
    or build a :class:`DisplayContext` and call
    :func:`ralph.display.agent_event_renderer.render_event`.
    """
    if kind in _ICON_BY_KIND:
        return _ICON_BY_KIND[kind]
    return "?"


def _truncate_to_cells(content: str, max_cells: int = 200) -> str:
    if cell_len(content) <= max_cells:
        return content

    truncated: list[str] = []
    used = 0
    for char in content:
        char_cells = cell_len(char)
        if used + char_cells > max_cells:
            break
        truncated.append(char)
        used += char_cells
    return "".join(truncated) + "…"


def render_event_line(
    kind: ActivityEventKind,
    content: str | None,
    *,
    timestamp: str | None = None,
) -> str:
    """Format a single activity event as a rich-markup string for terminal display.

    The activity_router pipeline feeds raw agent activity strings here, so
    ``content`` is sanitized through :func:`strip_terminal_control`
    BEFORE the cell-width truncation (so an escape sequence can never be
    split in half) and BEFORE rich ``escape`` (so rich markup like
    ``[red]...[/red]`` is still neutralised to ``\\[red]...\\[/red]``).
    """
    icon = _ICON_BY_KIND.get(kind, "?")
    raw_timestamp = timestamp or datetime.now(UTC).isoformat()
    parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    safe_content = strip_terminal_control(content or "")
    truncated_content = _truncate_to_cells(safe_content)
    escaped_content = escape(truncated_content)
    return f"{icon} [theme.text.muted]{parsed_timestamp:%H:%M:%S}[/] {escaped_content}"


__all__ = [
    "ActivityEventKind",
    "ActivityProvider",
    "ActivityVisibilityHint",
    "AgentActivityEvent",
    "EventOptions",
    "make_event",
    "module_sequence",
    "render_event_line",
]

SequenceCounter = _ModuleSequence
