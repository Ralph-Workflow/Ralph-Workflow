
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic


class MarkdownArtifactError(ValueError):

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        super().__init__("\n".join(diagnostic.message for diagnostic in diagnostics))


__all__ = ["MarkdownArtifactError"]
