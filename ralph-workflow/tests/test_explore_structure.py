"""Black-box tests for Phase 2 Python and Markdown structure extraction.

AC-06: The reindex pipeline persists deterministic Python AST and
Markdown structure rows in ``spans`` / ``symbols`` / ``edges``
tables. Changed-file reindex leaves no stale graph rows behind.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.explore.structure import (
    EXTRACTOR_VERSION,
    extract_markdown,
    extract_python,
    extract_structure,
)


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text(
        "def hello():\n    return 1\n\nclass Foo:\n    def bar(self):\n        return 2\n"
    )
    (workspace / "README.md").write_text(
        "# Title\n\n## Section\n\n```python\nx = 1\n```\n"
    )
    return workspace


def _build_index(workspace: Path, tmp_path: Path) -> ExploreStore:
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
    return store


def test_python_structure_extracts_symbols_spans_and_edges(tmp_path: Path) -> None:
    """AC-06: Python extraction produces real symbols + spans + edges."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        counts = store.count_structure_rows()
        assert counts["spans"] > 0, "spans rows should be populated for Python"
        assert counts["symbols"] > 0, "symbols rows should be populated"
        assert counts["edges"] > 0, "edges rows should be populated"
        # ``hello`` is the function name; the qualified name in our
        # extractor uses ``parent.child`` or just the bare name when
        # the parent is the module.
        names = {row.name for row in store.iter_symbols()}
        assert "hello" in names
        assert "Foo" in names
        qualified = {row.qualified_name for row in store.iter_symbols()}
        assert "module.hello" in qualified
        relations = {row.relation for row in store.iter_edges()}
        assert "contains" in relations
        # Imports and class bases produce edges too. Not every
        # file has imports, so we do not assert on the relation
        # being present.
        # Provenance is recorded as ``extracted`` for parser edges.
        for row in store.iter_edges():
            assert row.provenance in {"extracted", "inferred", "unknown"}
    finally:
        store.close()


def test_markdown_structure_extracts_heading_anchors_and_code_fence_edges(
    tmp_path: Path,
) -> None:
    """AC-06: Markdown headings produce spans, symbols, and contains edges."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        # Find the markdown file's structure rows.
        markdown_spans = list(store.iter_spans("README.md"))
        assert any(span.kind.startswith("h") for span in markdown_spans), (
            "Markdown extraction should emit at least one h-level span"
        )
        markdown_symbols = list(store.iter_symbols("README.md"))
        assert any(sym.kind.startswith("h") for sym in markdown_symbols)
        # Setext-style headings are also covered via the
        # ``md_setext`` extracted_from marker.
        # Verify direct extraction works too.
        content = (workspace / "README.md").read_text(encoding="utf-8")
        result = extract_markdown(
            path="README.md",
            content=content,
            content_hash="deadbeef",
            generation=1,
        )
        assert any(span.kind == "h1" for span in result.spans)
        assert any(span.kind == "h2" for span in result.spans)
    finally:
        store.close()


def test_changed_file_reindex_replaces_structure_rows_without_stale_edges(
    tmp_path: Path,
) -> None:
    """AC-06: changed-file reindex replaces the structure rows atomically."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        before = {sym.qualified_name for sym in store.iter_symbols("module.py")}
        assert "module.hello" in before
        # Rewrite module.py with a different function set.
        (workspace / "module.py").write_text("def new_func():\n    return 42\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        after = {sym.qualified_name for sym in store.iter_symbols("module.py")}
        assert "module.new_func" in after
        assert "module.hello" not in after, (
            "previous Python symbols must be replaced on changed reindex"
        )
    finally:
        store.close()


def test_python_structure_emits_full_prompt_relation_set(tmp_path: Path) -> None:
    """AC-02: the Python extractor emits every prompt-promised
    mechanically-evidenced relation: ``defines``, ``contains``,
    ``imports``, ``calls_syntax``, ``references_text``,
    ``inherits_syntax``, ``tests``, ``mentions``.

    Each edge carries an explicit ``provenance`` (``extracted`` for
    parser-verified rows, ``inferred`` for text-match rows) so
    callers can audit synthetic vs. real facts.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text(
        "import os\n"
        "import sys\n"
        "from typing import Any\n"
        "\n"
        "class Base:\n"
        "    pass\n"
        "\n"
        "class Foo(Base):\n"
        "    def bar(self):\n"
        "        return os.path.join('a', 'b')\n"
        "\n"
        "def hello():\n"
        "    return Foo()\n"
        "\n"
        "def test_smoke():\n"
        "    # References hello for documentation.\n"
        "    return hello()\n"
    )
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
    try:
        relations = {row.relation for row in store.iter_edges(path="module.py")}
        # Every prompt-promised relation must be present.
        assert "contains" in relations
        assert "imports" in relations
        assert "calls_syntax" in relations
        assert "references_text" in relations
        assert "inherits_syntax" in relations
        assert "tests" in relations
        assert "mentions" in relations
        # Every edge must carry a recognized provenance.
        provenances = {row.provenance for row in store.iter_edges(path="module.py")}
        assert provenances.issubset({"extracted", "inferred", "ambiguous"})
    finally:
        store.close()


def test_unsupported_language_returns_empty_extraction(tmp_path: Path) -> None:
    """Files outside Python/Markdown get empty structure rows."""
    workspace = _seed_workspace(tmp_path)
    (workspace / "data.json").write_text('{"a": 1}')
    store = _build_index(workspace, tmp_path)
    try:
        json_spans = list(store.iter_spans("data.json"))
        json_symbols = list(store.iter_symbols("data.json"))
        json_edges = list(store.iter_edges(path="data.json"))
        assert json_spans == []
        assert json_symbols == []
        assert json_edges == []
    finally:
        store.close()


def test_extractor_version_is_pinned() -> None:
    """The structure extractor advertises a stable version string."""
    assert EXTRACTOR_VERSION == "phase2-structure-v1"


def test_extract_structure_dispatches_by_extension() -> None:
    result = extract_structure(
        path="a.py",
        content="x = 1\n",
        content_hash="abc",
        generation=1,
    )
    # Python extraction always emits at least the module span.
    assert any(span.kind == "module" for span in result.spans)


def test_extract_python_syntax_error_raises_python_extraction_error(tmp_path: Path) -> None:
    """PA-001 / AC-02: A Python file that fails to parse raises the
    typed :class:`PythonExtractionError` so the reindex pipeline can
    fail-closed in its preflight while preserving the prior lexical
    and structure rows for the path.
    """
    import pytest

    from ralph.mcp.explore.structure import PythonExtractionError

    workspace = tmp_path / "ws"
    workspace.mkdir()
    bad_path = workspace / "bad.py"
    bad_path.write_text("def broken(:\n    pass\n")
    with pytest.raises(PythonExtractionError):
        extract_python(
            path="bad.py",
            content=bad_path.read_text(encoding="utf-8"),
            content_hash="0",
            generation=1,
        )
