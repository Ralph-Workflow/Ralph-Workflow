"""Typed cross-layer activity contract for parser and display integration."""

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

    After the wt-028-display consolidation this function delegates to
    :func:`ralph.display.agent_event_renderer.render_event_kind_text`
    so every event kind routes through the single registry. The
    pre-render sanitization call is kept here so the
    :mod:`ralph.testing.audit_terminal_escape_containment` literal-string
    invariant that pins this function as a containment sink stays
    satisfied: a regression that drops the strip is detected as a
    verify-gate failure. The literal also stays here so the body keeps
    a defence-in-depth sanitization pass for callers that build
    ``content`` outside the registry's normalizer (e.g. raw router
    lines).
    """
    # Defence-in-depth strip (audit_terminal_escape_containment pinned).
    safe_content = strip_terminal_control(content or "")
    from ralph.display.agent_event_renderer import render_event_kind_text

    return render_event_kind_text(
        kind,
        safe_content,
        timestamp=timestamp,
    )


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
