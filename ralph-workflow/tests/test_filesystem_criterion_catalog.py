"""Acceptance catalog test for the filesystem-proportional run traceability page.

The maintained criterion map
``docs/agents/filesystem-activity-traceability.md`` records, for every W/R/P/B/D/E
criterion in ``.agent/PRODUCT_CRITERIA.md``, the public boundary, current
evidence, and remaining gap. This test enforces two compact invariants so the
catalog itself cannot silently lose coverage:

* Every ``W1``-``W8``, ``R1``-``R5``, ``P1``-``P4``, ``B1``-``B6``, ``D1``-``D3``,
  and ``E1``-``E4`` row is present in the matrix (no criterion can disappear).
* Every ``COVERED`` row names a test path that actually exists in the test tree
  so a "covered by accident" claim is reverted by deleting the test.

A row that legitimately lacks a test reference must be tagged ``GAP``. The
catalog does not assert on production source — only on the test path strings
the document references.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace

REPO_ROOT = Path(__file__).resolve().parents[1]
# The ``scripts/`` tree lives one level up from the Python package.
WORKSPACE_ROOT = REPO_ROOT.parent
TRACEABILITY_DOC = REPO_ROOT / "docs" / "agents" / "filesystem-activity-traceability.md"
TESTS_ROOT = REPO_ROOT / "tests"
SCRIPTS_ROOT = WORKSPACE_ROOT / "scripts"

_EXPECTED_CRITERIA: tuple[str, ...] = (
    *(f"W{index}" for index in range(1, 9)),
    *(f"R{index}" for index in range(1, 6)),
    *(f"P{index}" for index in range(1, 5)),
    *(f"B{index}" for index in range(1, 7)),
    *(f"D{index}" for index in range(1, 4)),
    *(f"E{index}" for index in range(1, 5)),
)


def _traceability_text() -> str:
    return TRACEABILITY_DOC.read_text(encoding="utf-8")


def _row_for_criterion(text: str, criterion: str) -> str:
    """Return the matrix row whose first column starts with ``criterion``."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first_cell = stripped.split("|", 2)[1].strip()
        if first_cell == criterion:
            return stripped
    raise AssertionError(f"criterion {criterion} missing from traceability matrix")


def test_regression_rejects_noncommittal_matrix_status() -> None:
    """S-2: every criterion is either evidenced or an explicit discovery gap."""
    malformed = "| W1 | FsWorkspace.write | no evidence | pending review |"

    with pytest.raises(AssertionError, match="COVERED or GAP"):
        _assert_explicit_status(malformed, "W1")


def _assert_explicit_status(row: str, criterion: str) -> None:
    """Require each row to claim evidence or retain an explicit discovery gap."""
    assert "COVERED" in row or "GAP" in row, (
        f"{criterion} must be explicitly marked COVERED or GAP, got row: {row!r}"
    )


def _referenced_test_paths(row: str) -> list[str]:
    """Return every path reference that appears in ``row``.

    The matrix may use either ``tests/...`` or the shorter ``test_*.py`` form
    when the reference is unambiguous inside the tests tree. Treat any
    ``test_*.py`` token as an implicit relative path inside ``tests/``. A
    ``::test_name`` suffix selects a particular test within the file but the
    file itself still counts as the canonical reference. ``scripts/...``
    references are also accepted for documentation-only criteria.
    """
    cells = row.split("|")
    collected: list[str] = []
    for cell in cells:
        tokens = cell.replace("`", " ").split()
        for token in tokens:
            stripped = token.rstrip(",.;:)")
            # Drop a `::test_id` selector; the file path is what we need.
            if "::" in stripped:
                stripped = stripped.split("::", 1)[0]
            if (stripped.startswith("tests/") and stripped.endswith(".py")) or (
                stripped.startswith("scripts/")
                and (stripped.endswith(".py") or stripped.endswith(".sh"))
            ):
                collected.append(stripped)
            elif stripped.startswith("./") and stripped.endswith(".py") and "/tests/" in stripped:
                collected.append(stripped[2:])
            elif stripped.startswith("../") and "tests/" in stripped and stripped.endswith(".py"):
                start = stripped.index("tests/")
                collected.append(stripped[start:])
            elif stripped.startswith("test_") and stripped.endswith(".py") and "/" not in stripped:
                collected.append(f"tests/{stripped}")
    return collected


@pytest.mark.parametrize("criterion", _EXPECTED_CRITERIA)
def test_matrix_lists_every_criterion(criterion: str) -> None:
    """The traceability matrix must enumerate every product criterion."""
    row = _row_for_criterion(_traceability_text(), criterion)
    assert "|" in row, f"criterion {criterion} row malformed: {row!r}"


def test_matrix_rows_use_explicit_coverage_or_gap_status() -> None:
    """Keep each criterion an evidenced claim or an explicit implementation backlog item."""
    text = _traceability_text()
    for criterion in _EXPECTED_CRITERIA:
        _assert_explicit_status(_row_for_criterion(text, criterion), criterion)


def test_covered_rows_reference_real_test_files() -> None:
    """``COVERED`` claims must point to tests that exist on disk.

    The catalog accepts either ``tests/...`` references (behavioral proof) or
    ``scripts/...`` references for documentation-only criteria (E3) where the
    proof is a static guard rather than a behavioral test. The path must
    resolve inside the repository either way.
    """
    text = _traceability_text()
    for criterion in _EXPECTED_CRITERIA:
        row = _row_for_criterion(text, criterion)
        if "COVERED" not in row:
            continue
        references = _referenced_test_paths(row)
        assert references, (
            f"{criterion} marked COVERED without naming a path under tests/ or scripts/"
        )
        for relative_path in references:
            absolute = (
                REPO_ROOT / relative_path
                if relative_path.startswith("tests/")
                else WORKSPACE_ROOT / relative_path
            )
            assert absolute.exists(), f"{criterion} references missing file: {relative_path}"


def test_covered_rows_resolve_inside_tests_tree() -> None:
    """Every reference must live inside the repository's test or scripts tree."""
    text = _traceability_text()
    for criterion in _EXPECTED_CRITERIA:
        row = _row_for_criterion(text, criterion)
        for relative_path in _referenced_test_paths(row):
            assert relative_path.startswith(("tests/", "scripts/")), (
                f"{criterion} reference {relative_path} escapes the tests/scripts tree"
            )
            if relative_path.startswith("tests/"):
                relative_to_tests = relative_path[len("tests/") :]
                assert (TESTS_ROOT / relative_to_tests).exists(), (
                    f"{criterion} reference {relative_path} missing on disk"
                )
            else:
                assert (WORKSPACE_ROOT / relative_path).exists(), (
                    f"{criterion} reference {relative_path} missing on disk"
                )


def test_covered_rows_cover_canonical_boundaries() -> None:
    """Sanity check that the canonical W1/W2/R1/P1 boundaries have evidence."""
    text = _traceability_text()
    for criterion, expected_suffix in (
        ("W1", "test_fs_workspace_idempotent_write.py"),
        ("W2", "test_filesystem_activity_baseline.py"),
        ("R1", "test_tool_workspace_handle_read_file.py"),
        ("P1", "test_workspace_watch_scoping.py"),
    ):
        row = _row_for_criterion(text, criterion)
        assert "COVERED" in row, f"{criterion} should be COVERED for canonical boundary"
        assert expected_suffix in row, (
            f"{criterion} expected to reference a path containing {expected_suffix}, "
            f"got row: {row!r}"
        )


def test_w8_b1_e2_b6_row_outcomes() -> None:
    """S-6: the four planned matrix outcomes cannot silently drift."""
    text = _traceability_text()
    expected = (
        ("W8", "COVERED", "tests/agents/test_workspace_watch_scoping.py"),
        ("B1", "COVERED", "tests/test_filesystem_activity_baseline.py"),
        ("E2", "COVERED", "tests/test_audit_regression_test_elimination.py"),
    )
    for criterion, status, reference in expected:
        row = _row_for_criterion(text, criterion)
        assert status in row
        assert reference in row
    b6 = _row_for_criterion(text, "B6")
    assert "GAP" in b6
    assert not _referenced_test_paths(b6)


def test_canonical_seam_imports_still_resolve() -> None:
    """The public seams named in the traceability matrix must still import."""
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.invoke._workspace_change_classifier import (
        WorkspaceChangeClassifier,
    )
    from ralph.mcp.artifacts.idempotent_write import (
        write_text_if_changed,
    )

    assert FsWorkspace is not None
    assert WorkspaceMonitor is not None
    assert WorkspaceChangeClassifier is not None
    assert write_text_if_changed is not None
