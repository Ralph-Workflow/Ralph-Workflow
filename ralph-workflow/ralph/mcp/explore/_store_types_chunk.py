from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentCacheChunk:
    start_line: int
    end_line: int
    text_hash: str
    text: str
    role: str = "body"


__all__ = ["ContentCacheChunk"]
