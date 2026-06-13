"""MCP tool text content block."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class ToolContent:
    """Single text tool response content block."""

    type: str
    text: str

    @classmethod
    def text_content(cls, text: str) -> ToolContent:
        """Create a text content block."""
        return cls(type="text", text=text)

    @classmethod
    def json_content(cls, payload: Mapping[str, object] | list[object]) -> ToolContent:
        """Create a text content block carrying a JSON-serialized payload.

        The payload is ``json.dumps``-serialized with ``sort_keys=False`` so
        dict insertion order is preserved (helps the agent read the response
        in the same order the handler built it). The text content type is
        reused so downstream consumers that only know how to parse ``text``
        fields keep working; agents that need a typed object can
        ``json.loads(content[0].text)``.
        """
        serialized = json.dumps(payload, default=str)
        return cls(type="text", text=serialized)

    def to_dict(self) -> dict[str, str]:
        """Serialize the content block to a dictionary."""
        return {"type": self.type, "text": self.text}
