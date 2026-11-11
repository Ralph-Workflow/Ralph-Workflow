
from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.explore._store_types_chunk import ContentCacheChunk


@dataclass(frozen=True, slots=True)
class ContentCachePayload:

    content_hash: str
    extractor_version: str
    chunks: tuple[ContentCacheChunk, ...]

    def chunk_count(self) -> int:
        return len(self.chunks)

    def chunk_bytes(self) -> int:
        return sum(len(chunk.text.encode("utf-8")) for chunk in self.chunks)


__all__ = [
    "ContentCacheChunk",
    "ContentCachePayload",
]
