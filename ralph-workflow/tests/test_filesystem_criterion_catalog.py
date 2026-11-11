from __future__ import annotations

from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace

REPO_ROOT = Path(__file__).resolve().parents[1]
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
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first_cell = stripped.split("|", 2)[1].strip()
        if first_cell == criterion:
            return stripped
    raise AssertionError(f"criterion {criterion} missing from traceability matrix")


def test_regression_rejects_noncommittal_matrix_status() -> None:
    malformed = "| W1 | FsWorkspace.write | no evidence | pending review |"

    with pytest.raises(AssertionError, match="COVERED or GAP"):
        _assert_explicit_status(malformed, "W1")


def _assert_explicit_status(row: str, criterion: str) -> None:
    assert "COVERED" in row or "GAP" in row, (
        f"{criterion} must be explicitly marked COVERED or GAP, got row: {row!r}"
    )


def _referenced_test_paths(row: str) -> list[str]:
    cells = row.split("|")
    collected: list[str] = []
    for cell in cells:
        tokens = cell.replace("`", " ").split()
        for token in tokens:
            stripped = token.rstrip(",.;:)")
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
    row = _row_for_criterion(_traceability_text(), criterion)
    assert "|" in row, f"criterion {criterion} row malformed: {row!r}"


def test_matrix_rows_use_explicit_coverage_or_gap_status() -> None:
    text = _traceability_text()
    for criterion in _EXPECTED_CRITERIA:
        _assert_explicit_status(_row_for_criterion(text, criterion), criterion)


def test_covered_rows_reference_real_test_files() -> None:
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
    assert "COVERED" in b6
    assert "tests/unit/test_agent_dir_retention.py" in b6
    assert "test_sweep_consults_shared_active_run_registry" in b6
    assert "test_sweep_consults_ownership_map_for_scratch_and_codex_home" in b6


def test_canonical_seam_imports_still_resolve() -> None:
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
