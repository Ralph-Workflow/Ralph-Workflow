"""Content-cache payload (de)serialization for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore._store_types` so the hub
module stays under the per-file line ceiling. This module owns
the path-independent ``ContentCachePayload`` / ``ContentCacheChunk``
dataclasses and the deterministic JSON BLOB codec.

The BLOB is JSON for human inspectability and deterministic
round-trip; a binary codec (msgpack/protobuf) is unnecessary for
Ralph's payload sizes and would require an extra dependency for
no measurable savings. The keys are stable across reindex passes
so a future cold rebuild of an older payload table succeeds
without an explicit codec version bump.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, cast

# AC-05: content-cache payload (de)serialization. The keys are
# stable across reindex passes so a future cold rebuild of an
# older payload table succeeds without an explicit codec version
# bump. The format version is a string sentinel so a future
# migration can detect (and reject) older payloads.
_CACHE_PAYLOAD_FORMAT_VERSION: Final[str] = "v1"


@dataclass(frozen=True, slots=True)
class ContentCacheChunk:
    """Single chunk row stored inside a content-cache payload.

    ``text_hash`` is the SHA-256 of the chunk text, used by the
    pipeline to detect tiny edits without re-chunking; ``text`` is
    the full chunk body so an evicted source file can be
    recovered from the cache.
    """

    start_line: int
    end_line: int
    text_hash: str
    text: str
    role: str = "body"


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


def serialize_content_cache_payload(payload: ContentCachePayload) -> bytes:
    """Encode ``payload`` into a deterministic JSON BLOB.

    Args:
        payload: path-independent extraction payload built by the
            reindex pipeline.

    Returns:
        UTF-8 encoded JSON bytes. The encoder sets
        ``separators=(",", ":")`` and ``sort_keys=True`` so a
        stable round-trip is reproducible on any platform.

    Raises:
        TypeError: when ``payload`` contains a non-JSON-serializable
            value.
    """
    raw_chunks: list[dict[str, object]] = []
    for chunk in payload.chunks:
        raw_chunks.append(
            {
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "text_hash": chunk.text_hash,
                "text": chunk.text,
                "role": chunk.role,
            }
        )
    envelope = {
        "format_version": _CACHE_PAYLOAD_FORMAT_VERSION,
        "content_hash": payload.content_hash,
        "extractor_version": payload.extractor_version,
        "chunks": raw_chunks,
    }
    return json.dumps(
        envelope,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def deserialize_content_cache_payload(blob: bytes) -> ContentCachePayload:
    """Decode ``blob`` back into a ``ContentCachePayload``.

    Args:
        blob: bytes previously produced by
            :func:`serialize_content_cache_payload`.

    Returns:
        The decoded payload.

    Raises:
        ValueError: when the blob is malformed or carries an
            unknown ``format_version``. Callers must fail closed so
            an unknown schema cannot accidentally corrupt the live
            index.
    """
    try:
        envelope_obj: object = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"content_cache_payload not valid UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(envelope_obj, dict):
        raise ValueError("content_cache_payload must decode to a JSON object")
    envelope = cast("dict[str, object]", envelope_obj)
    format_version_obj: object = envelope.get("format_version")
    if format_version_obj != _CACHE_PAYLOAD_FORMAT_VERSION:
        raise ValueError(
            f"unsupported content_cache_payload format_version={format_version_obj!r}; "
            f"expected {_CACHE_PAYLOAD_FORMAT_VERSION!r}"
        )
    content_hash_obj: object = envelope.get("content_hash")
    extractor_version_obj: object = envelope.get("extractor_version")
    chunks_obj: object = envelope.get("chunks")
    if not isinstance(content_hash_obj, str):
        raise ValueError("content_cache_payload.content_hash must be a string")
    if not isinstance(extractor_version_obj, str):
        raise ValueError(
            "content_cache_payload.extractor_version must be a string"
        )
    if not isinstance(chunks_obj, list):
        raise ValueError("content_cache_payload.chunks must be a list")
    decoded_chunks: list[ContentCacheChunk] = []
    for raw in chunks_obj:
        if not isinstance(raw, dict):
            raise ValueError(
                "content_cache_payload.chunk entry must be an object"
            )
        raw_obj = cast("dict[str, object]", raw)
        start_line_obj: object = raw_obj.get("start_line")
        end_line_obj: object = raw_obj.get("end_line")
        text_hash_obj: object = raw_obj.get("text_hash")
        text_obj: object = raw_obj.get("text")
        role_obj: object = raw_obj.get("role", "body")
        if not isinstance(start_line_obj, int) or not isinstance(end_line_obj, int):
            raise ValueError("chunk start/end_line must be integers")
        if not isinstance(text_hash_obj, str) or not isinstance(text_obj, str):
            raise ValueError("chunk text_hash/text must be strings")
        if not isinstance(role_obj, str):
            raise ValueError("chunk role must be a string")
        decoded_chunks.append(
            ContentCacheChunk(
                start_line=start_line_obj,
                end_line=end_line_obj,
                text_hash=text_hash_obj,
                text=text_obj,
                role=role_obj,
            )
        )
    return ContentCachePayload(
        content_hash=content_hash_obj,
        extractor_version=extractor_version_obj,
        chunks=tuple(decoded_chunks),
    )


__all__ = [
    "ContentCacheChunk",
    "ContentCachePayload",
    "deserialize_content_cache_payload",
    "serialize_content_cache_payload",
]
