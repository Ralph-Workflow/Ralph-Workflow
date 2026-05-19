"""Options for constructing an AgentActivityEvent."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.display.activity_visibility_hint import ActivityVisibilityHint


@dataclass(frozen=True, slots=True)
class EventOptions:
    """Options for constructing an AgentActivityEvent."""

    content: str | None = None
    metadata: dict[str, object] | None = None
    visibility: ActivityVisibilityHint = ActivityVisibilityHint.VISIBLE
    source: str = ""
