"""Typed cross-layer activity contract for parser and display integration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rich.cells import cell_len
from rich.markup import escape

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.activity_visibility_hint import ActivityVisibilityHint


@dataclass(frozen=True, slots=True)
class EventOptions:
    """Options for constructing an AgentActivityEvent."""

    content: str | None = None
    metadata: dict[str, object] | None = None
    visibility: ActivityVisibilityHint = ActivityVisibilityHint.VISIBLE
    source: str = ""


@dataclass(frozen=True, slots=True)
class AgentActivityEvent:
    """Typed canonical activity event for future parser normalization work."""

    provider: ActivityProvider
    kind: ActivityEventKind
    content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    visibility: ActivityVisibilityHint = ActivityVisibilityHint.VISIBLE
    source: str = ""
    sequence: int | None = None
    timestamp: str | None = None


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
    """Construct an ``AgentActivityEvent`` with an auto-incremented sequence and UTC timestamp."""
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
    ActivityEventKind.UNKNOWN: "?",
}


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
    """Format a single activity event as a rich-markup string for terminal display."""
    icon = _ICON_BY_KIND.get(kind, "?")
    raw_timestamp = timestamp or datetime.now(UTC).isoformat()
    parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    escaped_content = escape(_truncate_to_cells(content or ""))
    return f"{icon} [theme.text.muted]{parsed_timestamp:%H:%M:%S}[/] {escaped_content}"


__all__ = [
    "ActivityEventKind",
    "ActivityProvider",
    "ActivityVisibilityHint",
    "AgentActivityEvent",
    "make_event",
    "module_sequence",
    "render_event_line",
]

# Backward compatibility alias
_SequenceCounter = _ModuleSequence
