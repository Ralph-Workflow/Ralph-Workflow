from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentContent:
    uri: str
    mime_type: str
    title: str
    type: str = "document"
    delivery: str = "typed_block"

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "uri": self.uri,
            "mimeType": self.mime_type,
            "title": self.title,
            "delivery": self.delivery,
        }
