"""Tests for the package-wide filesystem-write consolidation audit.

The audit replaces the curated allowlist of
``audit_idempotent_write_adoption.py`` with a package-wide AST walk
that rejects every raw ``write_text`` / ``write_bytes`` /
``Path.write_text`` / ``Path.write_bytes`` call outside the
sanctioned shared primitives, except where the call site carries an
explicit ``# filesystem-write-ok: <reason>`` marker naming the
behavioral contract (transient scratch, deliberately timestamped,
genuine append-only stream, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_write_consolidation as audit

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "ralph"


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


@pytest.mark.timeout_seconds(10)
def test_real_production_tree_audit_passes_or_summarises_with_actionable_diagnostics() -> None:
    """Step 8: package-wide walk must not crash on the committed tree.

    The walk either passes clean (every raw ``write_text`` is
    sanctioned via marker or lives in the explicit exception list)
    or reports at least one actionable violation. Either outcome is
    acceptable for this smoke test — the audit's *behavior* is
    what we're proving; the production-tree migration is the work
    for later iterations and may legitimately surface violations
    that this test does not fail on.
    """
    violations = audit.audit_filesystem_write_consolidation(PRODUCTION_ROOT)
    # No crash + bounded result; the content of violations is
    # whatever the current production tree contains.
    assert isinstance(violations, list)
    for violation in violations:
        assert violation.kind
        assert violation.file_path
        assert violation.line >= 0
        assert violation.message


def test_flags_unknown_raw_write_text(tmp_path: Path) -> None:
    """A new raw ``write_text`` call in a previously-clean module fails closed."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 2
    # Diagnostic must cite the approved primitive so the reader knows
    # what to do instead of just that the check failed (D2).
    assert (
        "write_text_if_changed" in violations[0].message or "atomic_write" in violations[0].message
    )


def test_ignores_guarded_write_via_canonical_helper(tmp_path: Path) -> None:
    """A call to the canonical ``write_text_if_changed`` helper passes."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\n"
            "def persist(backend, path, content):\n"
            "    write_text_if_changed(backend, path, content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_explicit_marker_suppresses_violation(tmp_path: Path) -> None:
    """A raw ``write_text`` with a ``# filesystem-write-ok:`` marker passes.

    The marker must name the contract; the audit treats every raw
    call as a contract violation by default, so an empty or
    missing marker fails closed (D1 + D3).
    """
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "def write_scratch(path, content):\n"
            "    # filesystem-write-ok: transient scratch file under tempfile.gettempdir(), deleted in finally\n"
            "    path.write_text(content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_empty_marker_fails_closed(tmp_path: Path) -> None:
    """A bare ``# filesystem-write-ok:`` without a reason still fails.

    D3 requires the marker to name a contract — an empty marker
    does not meet that bar and is treated as drift.
    """
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("def persist(path, content):\n    # filesystem-write-ok:\n    path.write_text(content)\n"),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_raw_write_bytes_also_flagged(tmp_path: Path) -> None:
    """A raw ``write_bytes`` call is treated like ``write_text`` (both are raw)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, data):\n    path.write_bytes(data)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_whitespace_around_attr_does_not_evade(tmp_path: Path) -> None:
    """Whitespace before the call must not evade AST-based detection."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text (content)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1


def test_marker_on_prior_line_suppresses(tmp_path: Path) -> None:
    """Marker may appear on the immediately preceding source line."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "def persist(path, content):\n"
            "    # filesystem-write-ok: timestamped marker file, content always changes\n"
            "    path.write_text(content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_marker_on_same_line_suppresses(tmp_path: Path) -> None:
    """Marker may appear as a trailing comment on the same line."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)  # filesystem-write-ok: timestamped\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_cli_returns_clean_when_no_violations(tmp_path: Path) -> None:
    """CLI returns 0 when the scanned tree is clean."""
    clean_root = tmp_path / "ralph"
    clean_root.mkdir()

    # Add a single compliant module so the walk has at least one
    # file to scan.
    (clean_root / "good.py").write_text(
        "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\n",
        encoding="utf-8",
    )

    # The audit defaults to walking package_root itself when no
    # explicit package_roots are supplied.
    assert audit.main([str(clean_root)]) == 0


def test_cli_returns_violation_count_when_dirty(tmp_path: Path) -> None:
    """CLI returns 1 when the scanned tree contains at least one violation."""
    dirty_root = tmp_path / "ralph"
    dirty_root.mkdir()
    (dirty_root / "bad.py").write_text(
        "def persist(path, content):\n    path.write_text(content)\n",
        encoding="utf-8",
    )

    assert audit.main([str(dirty_root)]) == 1


def test_cli_returns_bad_root_when_missing(tmp_path: Path) -> None:
    """CLI returns 2 when the package root does not exist."""
    assert audit.main([str(tmp_path / "missing")]) == 2
