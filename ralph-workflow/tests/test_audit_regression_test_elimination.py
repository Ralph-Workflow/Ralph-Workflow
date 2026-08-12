"""Regression tests for the regression-test-elimination marker audit."""

from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_regression_test_elimination as audit


def _write_source(tmp_path: Path, marker: str) -> Path:
    repo_root = tmp_path / "repo"
    source = repo_root / "ralph" / "boundary.py"
    source.parent.mkdir(parents=True)
    source.write_text(f"# regression-test-elimination: {marker}\n", encoding="utf-8")
    return repo_root


def test_regression_marker_with_existing_test_file_passes(tmp_path: Path) -> None:
    """S-5: a source marker resolves an existing test-module proof."""
    repo_root = _write_source(tmp_path, "tests/test_proof.py::test_regression")
    proof = repo_root / "tests" / "test_proof.py"
    proof.parent.mkdir()
    proof.write_text("def test_regression() -> None:\n    pass\n", encoding="utf-8")

    assert audit.audit_regression_test_elimination(repo_root) == []


def test_regression_marker_missing_test_file_fails_closed(tmp_path: Path) -> None:
    """S-5: a stale proof path produces a line-anchored diagnostic."""
    repo_root = _write_source(tmp_path, "tests/test_missing.py::test_regression")

    violations = audit.audit_regression_test_elimination(repo_root)

    assert [(item.kind, item.file_path, item.line) for item in violations] == [
        ("missing_test_file", "ralph/boundary.py", 1)
    ]
    assert "tests/test_missing.py::test_regression" in violations[0].message


def test_regression_marker_malformed_token_fails_closed(tmp_path: Path) -> None:
    """S-5: markers must name both a test module and proof selector."""
    repo_root = _write_source(tmp_path, "tests/test_proof.py")

    violations = audit.audit_regression_test_elimination(repo_root)

    assert [item.kind for item in violations] == ["malformed_marker"]


def test_regression_marker_path_outside_tests_fails_closed(tmp_path: Path) -> None:
    """S-5: a lexical tests/ prefix cannot escape the test tree."""
    repo_root = _write_source(tmp_path, "tests/../outside.py::test_regression")
    (repo_root / "outside.py").write_text("", encoding="utf-8")

    violations = audit.audit_regression_test_elimination(repo_root)

    assert [item.kind for item in violations] == ["test_path_outside_tests"]
