"""Lenient frontmatter vocabulary rule."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LenientEnum:
    """A frontmatter token that may be safely coerced to a documented default."""

    allowed: frozenset[str]
    default: str


__all__ = ["LenientEnum"]
