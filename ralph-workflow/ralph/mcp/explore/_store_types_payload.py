"""Content-cache payload representation for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore._store_types` so the hub
module stays under the per-file line ceiling. This module owns
the path-independent ``ContentCachePayload`` dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.explore._store_types_chunk import ContentCacheChunk


@dataclass(frozen=True, slots=True)
class ContentCachePayload:
    """Path-independent extraction payload keyed by content hash.

    Multiple workspace files may share the same payload when they
    are exact-content copies or moves; the store deduplicates by
    ``content_hash`` so the disk footprint stays bounded.
    """

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
