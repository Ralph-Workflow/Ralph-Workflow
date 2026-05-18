"""Typed canonical activity event for parser and display integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.activity_visibility_hint import ActivityVisibilityHint


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
