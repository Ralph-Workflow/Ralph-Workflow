"""One stable-ID list item parsed from a markdown artifact section."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedItem:
    """A stable-ID list item from one section."""

    identifier: str
    text: str
    line: int
    checked: bool | None


__all__ = ["ParsedItem"]
