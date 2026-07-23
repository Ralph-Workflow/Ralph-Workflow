"""Immutable parse result types for the markdown artifact grammar."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedItem:
    """A stable-ID list item from one section."""

    identifier: str
    text: str
    line: int
    checked: bool | None


@dataclass(frozen=True)
class ParsedSection:
    """A named document section and its closed-grammar items."""

    name: str
    line: int
    raw_lines: tuple[str, ...]
    items: tuple[ParsedItem, ...]


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
