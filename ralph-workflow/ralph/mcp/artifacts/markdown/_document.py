"""Immutable parse result type for the markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


@dataclass(frozen=True)
class ParsedDocument:
    """The complete pure parse result, including source line positions."""

    frontmatter: dict[str, str]
    frontmatter_lines: dict[str, int]
    sections: tuple[ParsedSection, ...]

    def section(self, name: str) -> ParsedSection | None:
        """Return a named section, if present."""
        return next((section for section in self.sections if section.name == name), None)


__all__ = ["ParsedDocument", "ParsedItem", "ParsedSection"]
