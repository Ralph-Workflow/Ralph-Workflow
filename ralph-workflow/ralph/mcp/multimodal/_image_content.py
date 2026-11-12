from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageContent:
    data: str
    mime_type: str
    type: str = "image"
    delivery: str = "inline_image"

    def to_dict(self) -> dict[str, object]:
        return {"type": self.type, "data": self.data, "mimeType": self.mime_type}
