"""Inline image content block."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageContent:
    """Inline image content block delivered as base64-encoded bytes."""

    data: str
    mime_type: str
    type: str = "image"
    delivery: str = "inline_image"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {"type": self.type, "data": self.data, "mimeType": self.mime_type}
