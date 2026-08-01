"""Regression tests for the package-wide filesystem-read consolidation audit.

The audit lives in :mod:`ralph.testing.audit_filesystem_read_consolidation`
and enforces PRODUCT_CRITERIA.md **R1**, **R3**, and **R4**: stable
full-file reads and existence probes in ``ralph/`` must route through
the canonical :class:`~ralph.mcp.artifacts.file_backend.FileBackend`
protocol or carry a local ``# filesystem-read-ok: <reason>`` marker
naming the behavioral contract.

The fixture-driven tests below cover every audit branch on a
synthetic ``tmp_path`` tree without touching the real package,
preserving the audit's behavior contract. The audit is already
invoked as a dedicated ``_VERIFY_STEPS`` entry inside ``make verify``
(via ``python -m ralph.testing.audit_filesystem_read_consolidation``),
so the same clean-tree check runs in the same gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_read_consolidation as audit


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_invalid_candidate_free_module_fails_closed(tmp_path: Path) -> None:
    """S-6 regression: invalid source cannot bypass the read audit."""
    module_rel = "alpha/broken.py"
    package_root = _write_fake_package(tmp_path, module_rel, "def broken(:\n")

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "invalid_module"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 1
    assert "module could not be parsed" in violations[0].message


def test_valid_candidate_free_module_passes(tmp_path: Path) -> None:
    """A valid inert production module remains accepted by the audit."""
    module_rel = "alpha/inert.py"
    package_root = _write_fake_package(tmp_path, module_rel, "VALUE = 1\n")

    assert audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    ) == []


def test_flags_raw_path_constructor_read_text(tmp_path: Path) -> None:
    """``Path(...).read_text()`` is flagged when ``Path`` is the constructor."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    return Path('x').read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 3
    # Diagnostic must cite the approved primitive so the reader knows
    # what to do instead of just that the check failed (D2).
    assert (
        "FileBackend.read_text" in violations[0].message
        or "filesystem-read-ok" in violations[0].message
    )


def test_flags_raw_pathlib_module_read_text(tmp_path: Path) -> None:
    """``pathlib.Path(...).read_text()`` is flagged at module-level path."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import pathlib\n"
        "def load() -> str:\n"
        "    return pathlib.Path('x').read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"


def test_flags_raw_os_path_exists(tmp_path: Path) -> None:
    """Existence probes via ``os.path.exists`` are flagged (R4)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def has(p):\n"
        "    return os.path.exists(p)\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_exists"
    assert violations[0].line == 3


def test_flags_raw_os_stat(tmp_path: Path) -> None:
    """Stat probes via ``os.stat`` are flagged (R4)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def probe(p):\n"
        "    return os.stat(p)\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_stat"


def test_ignores_marked_read_via_local_marker(tmp_path: Path) -> None:
    """A marker carrying a non-empty reason exempts the call."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    return Path('x').read_text(encoding='utf-8')  # filesystem-read-ok: shipped data fixture\n",
    )

    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_marker_without_reason_does_not_exempt(tmp_path: Path) -> None:
    """A bare ``# filesystem-read-ok:`` (empty reason) does NOT exempt the call (D3)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    return Path('x').read_text(encoding='utf-8')  # filesystem-read-ok:\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"


def test_marker_on_preceding_line_exempts(tmp_path: Path) -> None:
    """A marker on the immediately preceding line exempts the call."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    # filesystem-read-ok: shipped data fixture\n"
        "    return Path('x').read_text(encoding='utf-8')\n",
    )

    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_exempt_paths_are_skipped(tmp_path: Path) -> None:
    """The default exempt set keeps the canonical primitives clean."""
    module_rel = "mcp/artifacts/file_backend.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "class FileBackend:\n"
        "    def read_text(self, path: Path, *, encoding: str = 'utf-8') -> str:\n"
        "        return path.read_text(encoding=encoding)\n",
    )

    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_audit_module_is_exempt(tmp_path: Path) -> None:
    """The audit module itself is in the default exempt set."""
    module_rel = "testing/audit_filesystem_read_consolidation.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "os.path.exists('x')\n",
    )

    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_missing_package_root_returns_missing_root_violation(tmp_path: Path) -> None:
    """An absent package root fails closed with a diagnostic."""
    missing = tmp_path / "does-not-exist"
    violations = audit.audit_filesystem_read_consolidation(missing)
    assert len(violations) == 1
    assert violations[0].kind == "missing_package_root"


def test_main_module_entry_point_clean_returns_zero(tmp_path: Path) -> None:
    """``main()`` returns 0 when the audit is clean."""
    package_root = tmp_path / "pkg"
    (package_root / "ralph").mkdir(parents=True)
    (package_root / "ralph" / "inert.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert audit.main([str(package_root)]) == 0


def test_main_module_entry_point_violations_returns_one(tmp_path: Path) -> None:
    """``main()`` returns 1 when violations are present."""
    package_root = tmp_path / "pkg"
    (package_root / "ralph").mkdir(parents=True)
    (package_root / "ralph" / "bad.py").write_text(
        "import os\nos.path.exists('x')\n",
        encoding="utf-8",
    )
    assert audit.main([str(package_root)]) == 1


def test_unrelated_method_chain_passes(tmp_path: Path) -> None:
    """Attribute calls that are NOT in the raw-read set are not flagged."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def getenv(name):\n"
        "    return os.getenv(name)\n"
        "def walk(root):\n"
        "    return list(os.walk(root))\n",
    )

    assert audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    ) == []
