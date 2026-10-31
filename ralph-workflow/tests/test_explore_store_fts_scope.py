"""Regression tests: indexed FTS search scopes rows BEFORE the result limit.

``fts_search`` / ``fts_search_grep`` used to rank every hit, cut the list at
``LIMIT``, and only then drop rows outside ``path_prefix`` or the
include/exclude globs. When the best-ranked hits were all out of scope, the
in-scope matches were already gone and the indexed grep answered with zero
matches. These tests rank many out-of-scope chunks above one in-scope chunk.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.explore.store import ChunkRow, ExploreStore, FileRow, derive_chunk_id

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(5)

#: Out-of-scope files that outrank the in-scope one on bm25.
_NOISE_FILES = 40
_LIMIT = 5


def _add(store: ExploreStore, path: str, content: str) -> None:
    digest = hashlib.sha256(content.encode()).hexdigest()
    end_line = content.count("\n") + 1
    store.upsert_file(
        FileRow(
            path=path,
            content_hash=digest,
            size_bytes=len(content),
            mtime_ns=1,
            language="python",
            indexed_generation=1,
            indexed_at=0.0,
            is_deleted=False,
        )
    )
    chunk_id = derive_chunk_id(
        path=path, start_line=1, end_line=end_line, text_hash=digest, extractor_version="v1"
    )
    store.upsert_chunk(
        ChunkRow(
            chunk_id=chunk_id,
            path=path,
            start_line=1,
            end_line=end_line,
            text_hash=digest,
            role="body",
            generation=1,
        ),
        content,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ExploreStore]:
    explore_store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    for index in range(_NOISE_FILES):
        _add(explore_store, f"vendor/lib{index}.py", "frobnicate frobnicate frobnicate\n")
    # Look-alike paths the prefix must not reach: a LIKE wildcard in the
    # prefix, and a different-case directory.
    _add(explore_store, "srcXdir/near.py", "frobnicate frobnicate\n")
    _add(explore_store, "SRC/upper.py", "frobnicate frobnicate\n")
    # The single in-scope hit, ranked below all of the above.
    _add(explore_store, "src/target.py", "x = 1\n" * 50 + "frobnicate()\n")
    _add(explore_store, "src_dir/target.py", "x = 2\n" * 50 + "frobnicate()\n")
    try:
        yield explore_store
    finally:
        explore_store.close()


def _paths(rows: object) -> list[str]:
    assert isinstance(rows, list)
    return sorted(str(row["path"]) for row in rows)


@pytest.mark.parametrize("method", ["fts_search", "fts_search_grep"])
def test_path_prefix_survives_out_of_scope_hits_ranked_first(
    store: ExploreStore, method: str
) -> None:
    search = store.fts_search if method == "fts_search" else store.fts_search_grep
    assert _paths(search("frobnicate", limit=_LIMIT, path_prefix="src")) == ["src/target.py"]


@pytest.mark.parametrize("method", ["fts_search", "fts_search_grep"])
def test_path_prefix_is_literal_and_case_sensitive(store: ExploreStore, method: str) -> None:
    search = store.fts_search if method == "fts_search" else store.fts_search_grep
    # ``_`` is a LIKE wildcard; the prefix must still match only ``src_dir``.
    assert _paths(search("frobnicate", limit=50, path_prefix="src_dir")) == ["src_dir/target.py"]
    assert _paths(search("frobnicate", limit=50, path_prefix="SRC")) == ["SRC/upper.py"]


@pytest.mark.parametrize("method", ["fts_search", "fts_search_grep"])
def test_globs_survive_out_of_scope_hits_ranked_first(store: ExploreStore, method: str) -> None:
    search = store.fts_search if method == "fts_search" else store.fts_search_grep
    included = search("frobnicate", limit=_LIMIT, include_globs=["src/**"])
    assert _paths(included) == ["src/target.py"]
    excluded = search(
        "frobnicate",
        limit=_LIMIT,
        exclude_globs=["vendor/**", "srcXdir/**", "SRC/**", "src_dir/**"],
    )
    assert _paths(excluded) == ["src/target.py"]


@pytest.mark.parametrize("method", ["fts_search", "fts_search_grep"])
def test_glob_filtered_search_still_honours_limit(store: ExploreStore, method: str) -> None:
    search = store.fts_search if method == "fts_search" else store.fts_search_grep
    rows = search("frobnicate", limit=_LIMIT, include_globs=["vendor/**"])
    assert isinstance(rows, list)
    assert len(rows) == _LIMIT
    assert all(str(row["path"]).startswith("vendor/") for row in rows)
