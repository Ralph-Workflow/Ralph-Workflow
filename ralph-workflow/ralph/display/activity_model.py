
from __future__ import annotations

import threading
from datetime import UTC, datetime

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


def render_event_line(
    kind: ActivityEventKind,
    content: str | None,
    *,
    timestamp: str | None = None,
) -> str:
    safe_content = strip_terminal_control(content or "")
    from ralph.display.agent_event_renderer import (
        _truncate_to_cells,
        make_event_for_emit,
        render_event,
    )

    event = make_event_for_emit(
        kind,
        safe_content,
        timestamp=timestamp,
    )
    return _truncate_to_cells(render_event(event, escape_body=False).plain, 200)


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
