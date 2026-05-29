"""Tests for ralph.testing.audit_typecheck_bypass."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_typecheck_bypass import (
    _VALID_REASON_MARKERS,
    _check_mypy_ini,
    _check_pyproject_mypy,
    _find_type_ignore_violations,
    audit_codebase,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helper to get the approved reason suffix
# ---------------------------------------------------------------------------

_EXTERNAL_REASON = _VALID_REASON_MARKERS[0]


# ---------------------------------------------------------------------------
# _find_type_ignore_violations tests
# ---------------------------------------------------------------------------

def test_clean_code_passes() -> None:
    """No violations for code without type: ignore comments."""
    lines = [
        "def hello() -> str:",
        "    return 'world'",
    ]
    violations = _find_type_ignore_violations(lines, "src/clean.py")
    assert len(violations) == 0


def test_blanket_type_ignore_detected() -> None:
    """Blanket '# type: ignore' without error code is detected."""
    lines = [
        "x = external_api()  # type: ignore",
    ]
    violations = _find_type_ignore_violations(lines, "src/nonstrict.py")
    assert len(violations) == 1
    assert violations[0].category == "blanket-type-ignore"
    assert violations[0].line == 1


def test_coded_type_ignore_without_reason_detected() -> None:
    """# type: ignore[CODE] without reason is flagged."""
    lines = [
        "x = external_api()  # type: ignore[assignment]",
    ]
    violations = _find_type_ignore_violations(lines, "src/nonstrict.py")
    assert len(violations) == 1
    assert violations[0].category == "missing-reason"


def test_coded_type_ignore_with_reason_passes() -> None:
    """# type: ignore[CODE] with valid reason is flagged if not allowlisted."""
    lines = [
        f"x = external_api()  # type: ignore[assignment]  {_EXTERNAL_REASON}",
    ]
    violations = _find_type_ignore_violations(lines, "src/nonstrict.py")
    # assignment is not in allowlist, so it should be flagged as unknown
    assert len(violations) == 1
    assert violations[0].category == "unknown-type-ignore"


def test_allowlisted_type_ignore_with_reason_passes() -> None:
    """Allowlisted type: ignore with valid reason passes."""
    lines = [
        f"Repo.init(repo_root)  # type: ignore[misc]  {_EXTERNAL_REASON}",
    ]
    violations = _find_type_ignore_violations(lines, "ralph/git/commit_cleanup.py")
    assert len(violations) == 0


def test_allowlisted_type_ignore_without_reason_flagged() -> None:
    """Allowlisted type: ignore WITHOUT reason is flagged."""
    lines = [
        "Repo.init(repo_root)  # type: ignore[misc]",
    ]
    violations = _find_type_ignore_violations(lines, "ralph/git/commit_cleanup.py")
    assert len(violations) == 1
    assert violations[0].category == "missing-reason"


def test_type_ignore_inside_triple_quoted_string_skipped() -> None:
    """# type: ignore inside triple-quoted string is skipped."""
    lines = [
        '"""',
        "A docstring with # type: ignore in it.",
        '"""',
        "def my_func() -> None: pass",
    ]
    violations = _find_type_ignore_violations(lines, "src/docstring.py")
    assert len(violations) == 0


def test_no_type_ignore_comment_returns_nothing() -> None:
    """Line without # type: comment returns no violations."""
    lines = [
        "# This is just a regular comment",
        "x: int = 5",
    ]
    violations = _find_type_ignore_violations(lines, "src/clean.py")
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# Test file detection tests
# ---------------------------------------------------------------------------

def test_blanket_type_ignore_in_test_file_detected() -> None:
    """Blanket type: ignore in test file is flagged."""
    lines = [
        "x = external_api()  # type: ignore",
    ]
    violations = _find_type_ignore_violations(lines, "tests/test_something.py")
    assert len(violations) >= 1
    assert any("test" in v.category for v in violations)


def test_coded_type_ignore_in_test_file_detected() -> None:
    """# type: ignore[CODE] in test file is flagged."""
    lines = [
        "x = external_api()  # type: ignore[assignment]",
    ]
    violations = _find_type_ignore_violations(lines, "tests/test_something.py")
    assert len(violations) >= 1
    assert any("test" in v.category for v in violations)


# ---------------------------------------------------------------------------
# _check_mypy_ini tests
# ---------------------------------------------------------------------------

def test_strict_mypy_ini_passes(tmp_path: Path) -> None:
    """A strict mypy.ini has no violations."""
    content = """[mypy]
strict = true
python_version = 3.12
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_ignore_missing_imports_true_detected(tmp_path: Path) -> None:
    """ignore_missing_imports = true is a violation."""
    content = """[mypy]
strict = true
ignore_missing_imports = true
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_follow_imports_silent_detected(tmp_path: Path) -> None:
    """follow_imports = silent is a violation."""
    content = """[mypy]
strict = true
follow_imports = silent
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("follow_imports =" in v.detail for v in violations)


def test_ignore_errors_true_detected(tmp_path: Path) -> None:
    """ignore_errors = true is a violation."""
    content = """[mypy]
ignore_errors = true
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("ignore_errors" in v.detail for v in violations)


def test_exclude_pattern_detected(tmp_path: Path) -> None:
    """exclude pattern in mypy config is a violation."""
    content = """[mypy]
strict = true
exclude = legacy/.*
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("exclude" in v.detail for v in violations)


def test_follow_imports_normal_not_flagged(tmp_path: Path) -> None:
    """follow_imports = normal is NOT a violation."""
    content = """[mypy]
strict = true
follow_imports = normal
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    # follow_imports = normal should not be flagged
    assert not any("follow_imports" in v.detail for v in violations)


def test_ignore_missing_imports_false_not_flagged(tmp_path: Path) -> None:
    """ignore_missing_imports = false is NOT a violation."""
    content = """[mypy]
ignore_missing_imports = false
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert not any("ignore_missing_imports" in v.detail for v in violations)


def test_non_mypy_section_skipped(tmp_path: Path) -> None:
    """Sections that don't start with mypy are skipped."""
    content = """[other_section]
ignore_missing_imports = true
"""
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content)
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_missing_ini_file_returns_empty(tmp_path: Path) -> None:
    """Missing mypy.ini returns empty violations."""
    ini_path = tmp_path / "nonexistent.ini"
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# _check_pyproject_mypy tests
# ---------------------------------------------------------------------------

def test_clean_pyproject_tool_mypy_passes(tmp_path: Path) -> None:
    """pyproject.toml without weakening mypy settings passes."""
    content = """[tool.mypy]
strict = true
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) == 0


def test_pyproject_ignore_missing_imports_detected(tmp_path: Path) -> None:
    """ignore_missing_imports = true in [tool.mypy] is a violation."""
    content = """[tool.mypy]
strict = true
ignore_missing_imports = true
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_no_tool_mypy_section_passes(tmp_path: Path) -> None:
    """pyproject.toml without [tool.mypy] passes."""
    content = """[project]
name = "test"
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# audit_codebase integration tests
# ---------------------------------------------------------------------------

def test_clean_codebase_passes(tmp_path: Path) -> None:
    """A clean codebase with no violations passes."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n")

    violations, checked = audit_codebase(tmp_path)
    assert len(violations) == 0
    assert checked >= 1


def test_blanket_type_ignore_violation_found(tmp_path: Path) -> None:
    """Blanket type: ignore in codebase is found."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("x = external_api()  # type: ignore\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) >= 1
    assert any("blanket" in v.category for v in violations)


def test_mypy_ini_config_violations_found(tmp_path: Path) -> None:
    """Mypy config violations are detected by full audit."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n")
    (tmp_path / "mypy.ini").write_text("[mypy]\nignore_missing_imports = true\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


# ---------------------------------------------------------------------------
# main() entry point tests
# ---------------------------------------------------------------------------

def test_main_clean_returns_zero(tmp_path: Path) -> None:
    """main() returns 0 when no violations found."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n")

    result = main([str(tmp_path)])
    assert result == 0


def test_main_violations_return_one(tmp_path: Path) -> None:
    """main() returns 1 when violations found."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("x = external_api()  # type: ignore\n")

    result = main([str(tmp_path)])
    assert result == 1


def test_main_missing_directory_returns_two() -> None:
    """main() returns 2 when directory doesn't exist."""
    result = main(["/nonexistent/path/12345"])
    assert result == 2


# ---------------------------------------------------------------------------
# Skip dirs test
# ---------------------------------------------------------------------------

def test_skipped_dirs_excluded(tmp_path: Path) -> None:
    """Files in __pycache__ are skipped."""
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "cached.py").write_text("# type: ignore\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) == 0
