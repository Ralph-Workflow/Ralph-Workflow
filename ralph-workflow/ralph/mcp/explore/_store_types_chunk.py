"""Path-independent cached chunk value type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentCacheChunk:
    """Single chunk row stored inside a content-cache payload."""

    start_line: int
    end_line: int
    text_hash: str
    text: str
    role: str = "body"


__all__ = ["ContentCacheChunk"]
