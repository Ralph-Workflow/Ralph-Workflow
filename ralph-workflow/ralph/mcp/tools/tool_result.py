
from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal.artifacts import (
    AudioContent,
    DocumentContent,
    ImageContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
)

from .tool_content import ToolContent

type ContentBlock = (
    ToolContent
    | ImageContent
    | PdfContent
    | DocumentContent
    | AudioContent
    | VideoContent
    | ResourceReferenceContent
)


@dataclass(frozen=True)
class ToolResult:

    content: list[ContentBlock]
    is_error: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "content": [item.to_dict() for item in self.content],
            "isError": self.is_error,
        }
