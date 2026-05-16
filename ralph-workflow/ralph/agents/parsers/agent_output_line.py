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
    """

    type: str
    content: str = ""
    raw: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
