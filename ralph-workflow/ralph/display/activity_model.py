"""Typed cross-layer activity contract for parser and display integration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from rich.cells import cell_len
from rich.markup import escape


class ActivityProvider(StrEnum):
    """Canonical provider identity for agent activity events."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    GENERIC = "generic"
    UNKNOWN = "unknown"


class ActivityEventKind(StrEnum):
    """Canonical event kinds emitted across providers."""

    TEXT = "text"
    THINKING = "thinking"
    STATUS = "status"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    LIFECYCLE = "lifecycle"
    HEARTBEAT = "heartbeat"
    PROGRESS = "progress"
    UNKNOWN = "unknown"


class ActivityVisibilityHint(StrEnum):
    """Visibility intent used by later presenter and display layers."""

    VISIBLE = "visible"
    HIDDEN = "hidden"
    FALLBACK_ONLY = "fallback_only"


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


class _SequenceCounter:
    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n


module_sequence = _SequenceCounter()


def make_event(  # noqa: PLR0913
    *,
    provider: ActivityProvider,
    kind: ActivityEventKind,
    content: str | None = None,
    metadata: dict[str, object] | None = None,
    visibility: ActivityVisibilityHint = ActivityVisibilityHint.VISIBLE,
    source: str = "",
) -> AgentActivityEvent:
    return AgentActivityEvent(
        provider=provider,
        kind=kind,
        content=content,
        metadata=metadata or {},
        visibility=visibility,
        source=source,
        sequence=module_sequence.next(),
        timestamp=datetime.now(UTC).isoformat(),
    )


_ICON_BY_KIND: dict[ActivityEventKind, str] = {
    ActivityEventKind.TEXT: "│",
    ActivityEventKind.THINKING: "∴",
    ActivityEventKind.STATUS: "\u203a",
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
