"""Immutable parse result type for the markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


@dataclass(frozen=True)
class ParsedDocument:
    """The complete pure parse result, including source line positions."""

    frontmatter: dict[str, str]
    frontmatter_lines: dict[str, int]
    sections: tuple[ParsedSection, ...]

    def section(self, name: str) -> ParsedSection | None:
        """Return the first section with ``name``, if present."""
        return next((section for section in self.sections if section.name == name), None)

    def sections_named(self, name: str) -> tuple[ParsedSection, ...]:
        """Return every section with ``name`` in document order (repeatable sections)."""
        return tuple(section for section in self.sections if section.name == name)


__all__ = ["ParsedDocument"]
