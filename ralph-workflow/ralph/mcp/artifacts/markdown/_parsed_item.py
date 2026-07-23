"""One stable-ID list item in a parsed markdown artifact."""

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
