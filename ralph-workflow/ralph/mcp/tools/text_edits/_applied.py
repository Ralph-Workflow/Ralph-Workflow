
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.tools.text_edits._diff import unified_text_diff

if TYPE_CHECKING:
    from ralph.mcp.tools.text_edits._text_edit import TextEdit


@dataclass(frozen=True)
class AppliedTextEdits:

    original: str
    content: str
    applied: tuple[TextEdit, ...]
    label: str

    @property
    def diff(self) -> str:
        return unified_text_diff(self.original, self.content, label=self.label)
