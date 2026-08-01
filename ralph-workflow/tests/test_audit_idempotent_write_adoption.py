"""Regression tests for the package-wide idempotent-write audit entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_idempotent_write_adoption as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


@pytest.mark.timeout_seconds(10)
def test_audit_idempotent_write_adoption_regression_passes_real_production_tree() -> None:
    """S-8: all existing production mutations are consolidated or locally justified."""
    assert audit.audit_idempotent_write_adoption(PRODUCTION_ROOT) == []


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
    """The compatibility CLI preserves clean, violation, and bad-root exit codes."""
    violating_root = _write_fake_package(
        tmp_path,
        "writer.py",
        "def persist(path, content):\n    path.write_text(content)\n",
    )

    assert audit.main([str(PRODUCTION_ROOT)]) == 0
    assert audit.main([str(violating_root)]) == 1
    assert audit.main([str(tmp_path / "missing")]) == 2
