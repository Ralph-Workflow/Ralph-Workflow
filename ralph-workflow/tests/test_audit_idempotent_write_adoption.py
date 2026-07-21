"""Regression tests for the idempotent-write adoption drift audit."""

from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_idempotent_write_adoption as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_audit_idempotent_write_adoption_regression_passes_real_production_tree() -> None:
    """Step 5: the committed persistence modules satisfy the adoption invariant."""
    violations = audit.audit_idempotent_write_adoption(PRODUCTION_ROOT)

    assert violations == []


def test_audit_idempotent_write_adoption_regression_flags_raw_write_text(
    tmp_path: Path,
) -> None:
    """Step 5: a raw full-file overwrite reports one actionable violation."""
    module_rel = "pipeline/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )

    violations = audit.audit_idempotent_write_adoption(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 2
    assert "write_text_if_changed" in violations[0].message


def test_audit_idempotent_write_adoption_regression_flags_whitespace_raw_write_text(
    tmp_path: Path,
) -> None:
    """Regression: whitespace between attr and call must not bypass the audit.

    A textual prefilter that keys on the exact ``.write_text(`` substring
    can be evaded by inserting whitespace (e.g. ``path.write_text (...)``).
    The audit must rely on the AST parse, not a syntax-sensitive substring
    check, so every valid raw overwrite is reported regardless of formatting.
    """
    module_rel = "pipeline/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text (content)\n",
    )

    violations = audit.audit_idempotent_write_adoption(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 2
    assert "write_text_if_changed" in violations[0].message


def test_audit_idempotent_write_adoption_regression_ignores_guarded_write(
    tmp_path: Path,
) -> None:
    """Step 5: the canonical idempotent helper is accepted by the audit."""
    module_rel = "pipeline/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(backend, path, content):\n"
        "    write_text_if_changed(backend, path, content)\n",
    )

    violations = audit.audit_idempotent_write_adoption(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_audit_idempotent_write_adoption_regression_flags_missing_module(
    tmp_path: Path,
) -> None:
    """Step 5: removing an allowlisted module fails closed as structural drift."""
    package_root = tmp_path / "ralph"
    package_root.mkdir()

    violations = audit.audit_idempotent_write_adoption(
        package_root,
        module_paths=("pipeline/missing.py",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "missing_allowlisted_module"
    assert violations[0].file_path == "pipeline/missing.py"


def test_audit_idempotent_write_adoption_regression_cli_exit_codes(
    tmp_path: Path,
) -> None:
    """Step 5: the CLI returns clean, violation, and bad-root status codes."""
    violating_root = tmp_path / "ralph"
    violating_root.mkdir()

    assert audit.main([str(PRODUCTION_ROOT)]) == 0
    assert audit.main([str(violating_root)]) == 1
    assert audit.main([str(tmp_path / "missing")]) == 2
