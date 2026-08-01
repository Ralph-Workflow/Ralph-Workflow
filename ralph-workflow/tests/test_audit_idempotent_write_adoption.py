"""Regression tests for the package-wide idempotent-write audit entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_idempotent_write_adoption as audit


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


# NOTE: ``test_audit_idempotent_write_adoption_regression_passes_real_production_tree``
# was deleted in the wt-05-test-opti pass. That audit is already invoked as
# a dedicated ``_VERIFY_STEPS`` entry inside ``make verify`` (via
# ``python -m ralph.testing.audit_idempotent_write_adoption``), so the
# same clean-tree check runs in the same gate. Re-running it through
# pytest on the default profile added ~4 s of AST-walk cost to the
# slowest shard while proving nothing the verify step does not already
# prove. The fixture-driven tests below cover every audit branch on a
# synthetic ``tmp_path`` tree without touching the real package,
# preserving the audit's behavior contract.


def test_audit_idempotent_write_adoption_regression_flags_unknown_raw_write_text(
    tmp_path: Path,
) -> None:
    """S-8: a new, unlisted writer is rejected without allowlist maintenance."""
    package_root = _write_fake_package(
        tmp_path,
        "new_surface/writer.py",
        "def persist(path, content):\n    path.write_text(content)\n",
    )

    violations = audit.audit_idempotent_write_adoption(package_root)

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert violations[0].file_path == "new_surface/writer.py"
    assert "write_text_if_changed" in violations[0].message


def test_audit_idempotent_write_adoption_regression_flags_path_variable_delete(
    tmp_path: Path,
) -> None:
    """S-8 regression: a Path-valued variable cannot bypass delete enforcement."""
    package_root = _write_fake_package(
        tmp_path,
        "new_surface/cleanup.py",
        (
            "from pathlib import Path\n\n"
            "def cleanup() -> None:\n"
            "    sentinel_path = Path('sentinel')\n"
            "    sentinel_path.unlink(missing_ok=True)\n"
        ),
    )

    violations = audit.audit_idempotent_write_adoption(package_root)

    assert len(violations) == 1
    assert violations[0].kind == "raw_unlink"
    assert violations[0].file_path == "new_surface/cleanup.py"
    assert "filesystem-write-ok" in violations[0].message


def test_audit_idempotent_write_adoption_regression_accepts_local_reasoned_exception(
    tmp_path: Path,
) -> None:
    """D3: a local exception with its stated contract remains available."""
    package_root = _write_fake_package(
        tmp_path,
        "new_surface/scratch.py",
        (
            "def persist(path, content):\n"
            "    # filesystem-write-ok: unique transient staging file removed by caller\n"
            "    path.write_text(content)\n"
        ),
    )

    assert audit.audit_idempotent_write_adoption(package_root) == []


@pytest.mark.timeout_seconds(10)
def test_audit_idempotent_write_adoption_regression_cli_exit_codes(tmp_path: Path) -> None:
    """The compatibility CLI preserves clean, violation, and bad-root exit codes.

    The clean-tree path is exercised by ``make verify`` (the
    ``audit_idempotent_write_adoption`` step in ``_VERIFY_STEPS``),
    so this test only covers the violation and bad-root paths with
    synthetic ``tmp_path`` packages. Re-walking PRODUCTION_ROOT here
    would duplicate the verify step's clean-tree run.
    """
    clean_root = tmp_path / "clean_pkg"
    (clean_root / "ralph").mkdir(parents=True)
    (clean_root / "ralph" / "clean.py").write_text("value = 1\n", encoding="utf-8")
    violating_root = tmp_path / "violating_pkg"
    (violating_root / "ralph").mkdir(parents=True)
    (violating_root / "ralph" / "writer.py").write_text(
        "def persist(path, content):\n    path.write_text(content)\n", encoding="utf-8"
    )

    assert audit.main([str(clean_root)]) == 0
    assert audit.main([str(violating_root)]) == 1
    assert audit.main([str(tmp_path / "missing")]) == 2
