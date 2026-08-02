"""Fail-closed regression coverage for filesystem-write consolidation."""

from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_filesystem_write_consolidation as audit


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    """S-6: a missing default root cannot make the write audit silently pass."""
    violations = audit.audit_filesystem_write_consolidation(tmp_path)

    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"
    assert "fail closed" in violations[0].message


def test_synthetic_unknown_writer_in_production_would_fail(tmp_path: Path) -> None:
    """S-6: an unrecognized production ``os.replace`` must be rejected."""
    module_rel = "sneaky/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def install(tmp: str, final: str) -> None:\n"
        "    os.replace(tmp, final)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_replace"]
    assert "idempotent_write" in violations[0].message


def test_pathlib_path_unlink_detected(tmp_path: Path) -> None:
    """S-6: chained pathlib deletion cannot bypass mutation enforcement."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import pathlib\n"
        "def drop(path: str) -> None:\n"
        "    pathlib.Path(path).unlink()\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_unlink"]
