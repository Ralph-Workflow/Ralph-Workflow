"""The success outcome of applying a batch of text edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.tools.text_edits._diff import unified_text_diff

if TYPE_CHECKING:
    from ralph.mcp.tools.text_edits._text_edit import TextEdit


@dataclass(frozen=True)
class AppliedTextEdits:
    """Every edit matched; ``content`` is the fully edited text."""

    original: str
    content: str
    applied: tuple[TextEdit, ...]
    label: str

    @property
    def diff(self) -> str:
        """Return the unified diff from the original to the edited text."""
        return unified_text_diff(self.original, self.content, label=self.label)
