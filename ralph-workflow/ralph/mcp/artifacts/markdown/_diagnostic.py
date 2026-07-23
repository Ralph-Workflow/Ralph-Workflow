"""Line-anchored diagnostics for the closed markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Diagnostic:
    """One validation finding, anchored to the source document when possible."""

    line: int
    section: str | None
    rule_id: str
    message: str
    severity: Literal["error", "warning"] = "error"


class MarkdownArtifactError(ValueError):
    """Raised by callers that choose to reject error diagnostics as an exception."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        super().__init__("\n".join(diagnostic.message for diagnostic in diagnostics))


__all__ = ["Diagnostic", "MarkdownArtifactError"]
