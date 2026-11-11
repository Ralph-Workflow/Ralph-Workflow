from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_typecheck_bypass import (
    _WEAKENING_MYPY_KEYS,
    _WEAKENING_MYPY_VALUES,
    _check_mypy_ini,
    _check_pyproject_mypy,
    audit_codebase,
    main,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "inline suppression scanning": "the audit now checks mypy configuration weakening only; inline suppressions moved to the absolute comment gate",
}
if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.timeout_seconds(5)


def _write_ini(tmp_path: Path, content: str) -> Path:
    ini_path = tmp_path / "mypy.ini"
    ini_path.write_text(content, encoding="utf-8")
    return ini_path


def _write_pyproject(tmp_path: Path, content: str) -> Path:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content, encoding="utf-8")
    return pyproject


def test_strict_mypy_ini_passes(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
python_version = 3.12
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_ignore_missing_imports_true_detected(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
ignore_missing_imports = true
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_follow_imports_silent_detected(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
follow_imports = silent
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("follow_imports =" in v.detail for v in violations)


def test_ignore_errors_true_detected(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
ignore_errors = true
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("ignore_errors" in v.detail for v in violations)


def test_exclude_pattern_detected(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
exclude = legacy/.*
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("exclude" in v.detail for v in violations)


def test_follow_imports_normal_not_flagged(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
follow_imports = normal
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert not any("follow_imports" in v.detail for v in violations)


def test_ignore_missing_imports_false_not_flagged(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
ignore_missing_imports = false
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert not any("ignore_missing_imports" in v.detail for v in violations)


def test_non_mypy_section_skipped(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[other_section]
ignore_missing_imports = true
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_missing_ini_file_returns_empty(tmp_path: Path) -> None:
    ini_path = tmp_path / "nonexistent.ini"
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_mypy_ini_warn_unused_ignores_false_is_violation(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
warn_unused_ignores = False
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("warn_unused_ignores" in v.detail for v in violations)
    assert any(v.category == "mypy-config" for v in violations)


def test_mypy_ini_disallow_untyped_defs_false_is_violation(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
disallow_untyped_defs = False
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("disallow_untyped_defs" in v.detail for v in violations)
    assert any(v.category == "mypy-config" for v in violations)


def test_mypy_ini_disable_error_code_is_violation(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
disable_error_code = assignment
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) >= 1
    assert any("disable_error_code" in v.detail for v in violations)
    assert any(v.category == "mypy-config" for v in violations)


def test_every_mypy_section_is_inspected(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true

[mypy-ralph.*]
ignore_missing_imports = true

[mypy-tests.*]
exclude = tests/.*
""",
    )
    violations = _check_mypy_ini(ini_path)
    details = [violation.detail for violation in violations]
    assert any(detail.startswith("[mypy-ralph.*] ignore_missing_imports") for detail in details)
    assert any(detail.startswith("[mypy-tests.*] exclude") for detail in details)


def test_mypy_ini_warn_unused_ignores_true_no_violation(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
warn_unused_ignores = True
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_mypy_ini_disallow_untyped_defs_true_no_violation(tmp_path: Path) -> None:
    ini_path = _write_ini(
        tmp_path,
        """[mypy]
strict = true
disallow_untyped_defs = True
""",
    )
    violations = _check_mypy_ini(ini_path)
    assert len(violations) == 0


def test_clean_pyproject_tool_mypy_passes(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) == 0


def test_pyproject_ignore_missing_imports_detected(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
ignore_missing_imports = true
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_pyproject_exclude_detected(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
exclude = ["legacy/.*"]
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("exclude" in v.detail for v in violations)


def test_pyproject_disable_error_code_detected(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
disable_error_code = ["assignment"]
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("disable_error_code" in v.detail for v in violations)


def test_pyproject_warn_unused_ignores_false_detected(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
warn_unused_ignores = false
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("warn_unused_ignores" in v.detail for v in violations)


def test_pyproject_disallow_untyped_defs_false_detected(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[tool.mypy]
strict = true
disallow_untyped_defs = false
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) >= 1
    assert any("disallow_untyped_defs" in v.detail for v in violations)


def test_no_tool_mypy_section_passes(tmp_path: Path) -> None:
    pyproject = _write_pyproject(
        tmp_path,
        """[project]
name = "test"
""",
    )
    violations = _check_pyproject_mypy(pyproject)
    assert len(violations) == 0


def test_inline_suppressions_are_not_scanned_by_this_audit(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text("x = external_api()  # type: ignore\n", encoding="utf-8")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) == 0, (
        "the absolute comment gate owns inline suppressions; this audit only "
        "inspects mypy configuration files"
    )


def test_inline_suppressions_in_test_files_are_not_scanned_by_this_audit(
    tmp_path: Path,
) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_bad.py").write_text(
        "x = external_api()  # type: ignore[assignment]\n",
        encoding="utf-8",
    )

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) == 0


def test_clean_codebase_passes(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")

    violations, checked = audit_codebase(tmp_path)
    assert len(violations) == 0
    assert checked == 0


def test_mypy_ini_config_violations_found(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")
    _write_ini(tmp_path, "[mypy]\nignore_missing_imports = true\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_pyproject_config_violations_found(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "[tool.mypy]\nignore_missing_imports = true\n")

    violations, _checked = audit_codebase(tmp_path)
    assert len(violations) >= 1
    assert any("ignore_missing_imports" in v.detail for v in violations)


def test_main_clean_returns_zero(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "clean.py").write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")

    result = main([str(tmp_path)])
    assert result == 0


def test_main_violations_return_one(tmp_path: Path) -> None:
    _write_ini(tmp_path, "[mypy]\nignore_missing_imports = true\n")

    result = main([str(tmp_path)])
    assert result == 1


def test_main_reports_a_missing_root() -> None:
    assert main(["/nonexistent/ralph-workflow-root"]) == 2


def test_every_weakening_key_is_detected_in_every_mypy_section(tmp_path: Path) -> None:
    for key, pattern in _WEAKENING_MYPY_KEYS.items():
        value = next(iter(_WEAKENING_MYPY_VALUES[key]))
        ini_path = _write_ini(
            tmp_path,
            f"[mypy]\n{key} = {value}\n\n[mypy-ralph.*]\n{key} = {value}\n",
        )
        violations = _check_mypy_ini(ini_path)
        details = [violation.detail for violation in violations]
        assert any(pattern in detail for detail in details), key
        assert any(detail.startswith("[mypy-ralph.*]") for detail in details), key
