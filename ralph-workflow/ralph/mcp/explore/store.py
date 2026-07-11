"""Indexed exploration substrate store hub.

Re-exports :class:`ExploreStore` and the surrounding store
helpers from their per-concern sub-modules so the hub stays under
the per-file line ceiling. Each sub-module owns a focused slice of
the SQLite + workspace contract:

* :mod:`ralph.mcp.explore._store_types` -- row dataclasses,
  Clock + SystemClock, row-converter helpers, chunk/evidence-id
  helpers, workspace hashing utilities, ``normalize_index_path``.
* :mod:`ralph.mcp.explore._store_class` -- the
  :class:`ExploreStore` class implementation.

The public surface (``ExploreStore``, ``DEFAULT_INDEX_ROOT``,
``DEFAULT_INDEX_DB``, dataclasses, ``Clock``/``SystemClock``,
``row_str``, hashing helpers, ``chunk_text``, ``derive_*``,
``chunk_id``, workspace helpers, ``sha256_text``/``sha256_bytes``)
continues to be importable as ``from ralph.mcp.explore.store
import X`` for backward compatibility.
"""

from __future__ import annotations

from ralph.mcp.explore._store_class import ExploreStore
from ralph.mcp.explore._store_types import (
    DEFAULT_CHUNK_LINES,
    DEFAULT_INDEX_DB,
    DEFAULT_INDEX_ROOT,
    SCHEMA_VERSION,
    ChunkRow,
    Clock,
    ContentCacheChunk,
    ContentCachePayload,
    ContentCacheRow,
    EdgeRow,
    EvidenceRow,
    FileRow,
    SpanRow,
    SymbolRow,
    SystemClock,
    _row_bool,
    _row_float,
    _row_int,
    _row_int_opt,
    _row_optional_str,
    _row_str,
    _row_to_content_cache,
    _row_to_edge,
    _row_to_evidence,
    _row_to_file,
    _row_to_span,
    _row_to_symbol,
    assert_within_workspace,
    chunk_text,
    collect_workspace_files,
    deserialize_content_cache_payload,
    derive_chunk_id,
    derive_evidence_id,
    hash_workspace_file,
    iter_indexable_files,
    normalize_index_path,
    row_str,
    serialize_content_cache_payload,
    sha256_bytes,
    sha256_text,
)

__all__ = [
    "DEFAULT_CHUNK_LINES",
    "DEFAULT_INDEX_DB",
    "DEFAULT_INDEX_ROOT",
    "SCHEMA_VERSION",
    "ChunkRow",
    "Clock",
    "ContentCacheChunk",
    "ContentCachePayload",
    "ContentCacheRow",
    "EdgeRow",
    "EvidenceRow",
    "ExploreStore",
    "FileRow",
    "SpanRow",
    "SymbolRow",
    "SystemClock",
    "_row_bool",
    "_row_float",
    "_row_int",
    "_row_int_opt",
    "_row_optional_str",
    "_row_str",
    "_row_to_content_cache",
    "_row_to_edge",
    "_row_to_evidence",
    "_row_to_file",
    "_row_to_span",
    "_row_to_symbol",
    "assert_within_workspace",
    "chunk_text",
    "collect_workspace_files",
    "derive_chunk_id",
    "derive_evidence_id",
    "hash_workspace_file",
    "iter_indexable_files",
    "normalize_index_path",
    "row_str",
    "serialize_content_cache_payload",
    "sha256_bytes",
    "sha256_text",
]
