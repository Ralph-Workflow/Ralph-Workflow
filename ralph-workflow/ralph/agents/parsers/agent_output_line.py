"""Legacy normalized agent output line type."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AgentOutputLine:
    """Legacy normalised line extracted from agent NDJSON stream.

    This type is preserved for backward compatibility while newer cross-layer
    visibility work adopts the typed activity model.

    Attributes:
        type: Type of the output line (text, tool_use, tool_result, error, etc.).
        content: Text content of the line.
        raw: Raw JSON string from the agent.
        metadata: Additional metadata extracted from the line.
        timestamp: Optional ISO-8601 timestamp the parser extracted from the
            source event (DA-002 / wt-028-display S-2). When present, the
            downstream :func:`ParallelDisplay.emit_parsed_event` uses it as
            the authoritative source-time stamp on the rendered record so a
            fixture or replay preserves the original event's time end-to-end
            instead of falling back to the display clock. ``None`` keeps the
            pre-fix behavior (the display clock stamps the line).
    """

    type: str
    content: str = ""
    raw: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    timestamp: str | None = None
