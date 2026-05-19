"""MCP tool text content block."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContent:
    """Single text tool response content block."""

    type: str
    text: str

    @classmethod
    def text_content(cls, text: str) -> ToolContent:
        """Create a text content block."""
        return cls(type="text", text=text)

    def to_dict(self) -> dict[str, str]:
        """Serialize the content block to a dictionary."""
        return {"type": self.type, "text": self.text}
