
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class ToolContent:

    type: str
    text: str

    @classmethod
    def text_content(cls, text: str) -> ToolContent:
        return cls(type="text", text=text)

    @classmethod
    def json_content(cls, payload: Mapping[str, object] | list[object]) -> ToolContent:
        serialized = json.dumps(payload, default=str)
        return cls(type="text", text=serialized)

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}
