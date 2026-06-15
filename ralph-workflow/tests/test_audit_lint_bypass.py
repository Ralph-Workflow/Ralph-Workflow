"""Tests for ralph.testing.audit_lint_bypass."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_lint_bypass import (
    _check_pyproject_config,
    _find_noqa_violations,
    audit_codebase,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# _find_noqa_violations tests
# ---------------------------------------------------------------------------


def test_clean_code_passes() -> None:
    """No violations for code without noqa comments."""
    lines = [
        "def hello() -> None:",
        '    print("world")',
        "    return None",
    ]
    violations = _find_noqa_violations(lines, "src/clean.py")
    assert len(violations) == 0


def test_bare_noqa_detected() -> None:
    """Bare '# noqa' without error code is detected."""
    lines = [
        "def complex_func():  # noqa",
        "    pass",
    ]
    violations = _find_noqa_violations(lines, "src/complex.py")
    assert len(violations) == 1
    assert violations[0].category == "bare-noqa"
    assert violations[0].line == 1
    assert "bare '# noqa'" in violations[0].detail


def test_bare_noqa_with_trailing_text_detected() -> None:
    """Bare '# noqa' with extra trailing text is detected."""
    lines = [
        "def complex_func():  # noqa some comment about why",
        "    pass",
    ]
    violations = _find_noqa_violations(lines, "src/complex.py")
    assert len(violations) == 1
    assert violations[0].category == "bare-noqa"


def test_forbidden_noqa_code_detected() -> None:
    """# noqa with a code not in acceptable set is flagged."""
    lines = [
        "x = 1  # noqa: F841",
    ]
    violations = _find_noqa_violations(lines, "src/vars.py")
    assert len(violations) == 1
    assert violations[0].category == "forbidden-noqa"
    assert "F841" in violations[0].detail


def test_legitimate_noqa_not_flagged() -> None:
    """Allowlisted noqa codes for allowlisted files pass."""
    lines = [
        "global _CACHE  # noqa: PLW0603",
    ]
    violations = _find_noqa_violations(lines, "ralph/testing/audit_test_policy.py")
    assert len(violations) == 0


def test_unauthorized_noqa_code_for_wrong_file() -> None:
    """Allowlisted code used in a non-allowlisted file is flagged."""
    # PLR0911 is allowlisted for audit_test_policy, not for other_file.
    lines = [
        "def my_func(): pass  # noqa: PLR0911",
    ]
    violations = _find_noqa_violations(lines, "src/other_file.py")
    assert len(violations) == 1
    assert violations[0].category == "unauthorized-noqa"


def test_multiple_noqa_on_same_line() -> None:
    """Multiple codes in one # noqa comment are each checked."""
    lines = [
        "def my_func(): pass  # noqa: PLR0911, F841",
    ]
    violations = _find_noqa_violations(lines, "src/other_file.py")
    # PLR0911 unauthorized for other_file, F841 forbidden
    assert len(violations) >= 1


def test_noqa_inside_triple_quoted_string_skipped() -> None:
    """# noqa inside a triple-quoted string is not flagged."""
    lines = [
        '"""',
        "A docstring with # noqa in it.",
        '"""',
        "def my_func(): pass",
    ]
    violations = _find_noqa_violations(lines, "src/docstring.py")
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# _check_pyproject_config tests
# ---------------------------------------------------------------------------


def test_clean_pyproject_passes(tmp_path: Path) -> None:
    """pyproject.toml without per-file-ignores passes."""
    content = """[tool.ruff.lint]
select = ["E", "F"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) == 0


def test_per_file_ignores_detected(tmp_path: Path) -> None:
    """per-file-ignores in pyproject.toml is detected."""
    content = """[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["F841"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) >= 1
    assert any("per-file-ignores" in v.category for v in violations)


def test_extend_per_file_ignores_detected(tmp_path: Path) -> None:
    """extend-per-file-ignores in pyproject.toml is detected."""
    content = """[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.extend-per-file-ignores]
"legacy/*.py" = ["ALL"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) >= 1
    assert any("extend-per-file-ignores" in v.category for v in violations)


def test_missing_pyproject_no_error(tmp_path: Path) -> None:
    """Missing pyproject.toml returns empty violations."""
    pyproject = tmp_path / "nonexistent.toml"
    violations = _check_pyproject_config(pyproject)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# audit_codebase integration tests
# ---------------------------------------------------------------------------


def test_clean_codebase_passes(tmp_path: Path) -> None:
    """A clean codebase with no violations passes."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> None:\n    return None\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

    violations, checked = audit_codebase(tmp_path)
    assert len(violations) == 0
    assert checked >= 1


def test_bare_noqa_violation_found_in_codebase(tmp_path: Path) -> None:
    """Bare noqa in codebase is detected by full audit."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("def f(): pass  # noqa\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) >= 1
    assert any("bare-noqa" in v.category for v in violations)


def test_unreadable_file_skipped(tmp_path: Path) -> None:
    """Files that cannot be read are silently skipped."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    bad_file = src_dir / "bad.py"
    bad_file.write_text("def f(): pass\n")
    bad_file.chmod(0o000)  # make unreadable
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

    try:
        _violations, checked = audit_codebase(tmp_path)
        # Should not crash — unreadable files are skipped.
        assert checked >= 0
    finally:
        bad_file.chmod(0o644)


# ---------------------------------------------------------------------------
# main() entry point tests
# ---------------------------------------------------------------------------


def test_main_clean_returns_zero(tmp_path: Path) -> None:
    """main() returns 0 when no violations found."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello():\n    pass\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

    result = main([str(tmp_path)])
    assert result == 0


def test_main_violations_return_one(tmp_path: Path) -> None:
    """main() returns 1 when violations found."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("def f(): pass  # noqa\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

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
    """Files in __pycache__ and .venv are skipped."""
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "cached.py").write_text("def f(): pass  # noqa\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['E']\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# per-file-ignores allowlist tests
# ---------------------------------------------------------------------------


def test_per_file_ignores_with_allowlisted_codes_are_allowed(tmp_path: Path) -> None:
    """pyproject.toml with allowlisted per-file-ignores on matching patterns — no violation."""
    content = """[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["PLR2004"]
"ralph/cli/**/*.py" = ["PLC0415"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) == 0


def test_per_file_ignores_with_unapproved_codes_is_violation(tmp_path: Path) -> None:
    """pyproject.toml with per-file-ignores of unapproved codes — violation."""
    content = """[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["F841"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) >= 1
    assert any("F841" in v.detail for v in violations)
    assert any("not in the per-file-ignores allowlist" in v.detail for v in violations)


def test_per_file_ignores_allowlisted_code_wrong_pattern_is_violation(tmp_path: Path) -> None:
    """Allowlisted code applied to non-matching file pattern — violation."""
    content = """[tool.ruff.lint]
select = ["E", "F"]

[tool.ruff.lint.per-file-ignores]
"src/**/*.py" = ["PLR2004"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) >= 1
    assert any("non-matching file pattern" in v.detail for v in violations)


# ---------------------------------------------------------------------------
# extend-ignore detection tests
# ---------------------------------------------------------------------------


def test_check_pyproject_extend_ignore_is_violation(tmp_path: Path) -> None:
    """[tool.ruff.lint] extend-ignore in pyproject.toml is detected as a violation."""
    content = """[tool.ruff.lint]
select = ["E", "F"]
extend-ignore = ["E501"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) >= 1
    assert any("extend-ignore" in v.detail for v in violations)


def test_check_pyproject_no_extend_ignore_no_violation(tmp_path: Path) -> None:
    """pyproject.toml without extend-ignore section produces no extend-ignore violation."""
    content = """[tool.ruff.lint]
select = ["E", "F"]
"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content)
    violations = _check_pyproject_config(pyproject)
    assert len(violations) == 0
