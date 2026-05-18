"""Typed document content block."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentContent:
    """Typed document content block referencing a manifest artifact."""

    uri: str
    mime_type: str
    title: str
    type: str = "document"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        """Serialize to MCP-compatible content block dictionary."""
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }
