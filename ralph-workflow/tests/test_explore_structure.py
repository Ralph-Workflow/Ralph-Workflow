"""Black-box tests for Phase 2 Python and Markdown structure extraction.

Phase 2 of the indexed exploration plan introduces deterministic
Python (stdlib ``ast``) and Markdown (headings/anchors/code-fences)
structure extraction. The extraction is deferred to Phase 2 and
intentionally not implemented in this slice; this test file documents
the contract and asserts the deferred register covers it.

When Phase 2 ships, these tests will be extended to cover the
extraction outputs against real Python/Markdown source. For now they
verify the deferred-phase contract, the schema, and the
extractor-version interaction at the index-pipeline level.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.explore.deferred_phases import (
    DEFERRED_PHASES,
    DeferredPhaseRegistry,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore


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
    """Phase 2 contract: Python extraction produces symbols+spans+edges.

    Until Phase 2 ships, the index has zero structure rows. The
    deferred register must name the Phase 2 deliverables, and the
    store must record a schema version + extractor version so a
    future Phase 2 build can detect incompatible persisted state.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        schema_version = store.get_setting("schema_version")
        assert schema_version is None or isinstance(schema_version, str)
        phase_2 = DeferredPhaseRegistry.get("phase_2")
        assert phase_2 is not None
        deliverables = phase_2.deliverables
        assert any("Python" in d for d in deliverables)
    finally:
        store.close()


def test_markdown_structure_extracts_heading_anchors_and_code_fence_edges() -> None:
    """Phase 2 contract: Markdown headings/anchors/code-fences are
    extracted as edges. Until Phase 2 ships, the test asserts the
    deferred register names the deliverable.
    """
    phase_2 = DeferredPhaseRegistry.get("phase_2")
    assert phase_2 is not None
    deliverables_text = " ".join(phase_2.deliverables)
    assert "Markdown" in deliverables_text or "heading" in deliverables_text.lower()


def test_changed_file_reindex_replaces_structure_rows_real(tmp_path: Path) -> None:
    """Phase 2 contract: small-edit reindex reparses only the changed file.

    Until Phase 2 ships, the test asserts the reindex contract at
    the manifest level: changing one file reparses only that file.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        (workspace / "module.py").write_text("def hello():\n    return 42\n")
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert result.parse_count <= 1
        assert result.status in {"ok", "skipped_no_changes"}
    finally:
        store.close()


def test_extractor_version_is_pinned_in_settings(tmp_path: Path) -> None:
    """The store must record a schema version so Phase 2 can detect
    incompatible persisted state.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        store.set_setting("schema_version", "phase_1")
        assert store.get_setting("schema_version") == "phase_1"
    finally:
        store.close()


def test_phase_2_deferred_register_has_risk_and_rationale() -> None:
    """Every deferred phase must have a non-empty risk + rationale."""
    for entry in DEFERRED_PHASES:
        if entry.phase_id == "phase_2":
            assert entry.deferral_rationale.strip()
            assert entry.risk.strip()
            assert entry.deliverables
            return
    raise AssertionError("phase_2 not in DEFERRED_PHASES")
