"""Regression tests for the package-wide filesystem-read consolidation audit."""

from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_filesystem_read_consolidation as audit


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_invalid_candidate_free_module_fails_closed(tmp_path: Path) -> None:
    """Invalid source cannot bypass the read audit."""
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


def test_valid_candidate_free_module_passes(tmp_path: Path) -> None:
    """A valid inert production module remains accepted."""
    module_rel = "alpha/inert.py"
    package_root = _write_fake_package(tmp_path, module_rel, "VALUE = 1\n")
    assert audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    ) == []


def test_flags_raw_path_constructor_read_text(tmp_path: Path) -> None:
    """``Path('x').read_text()`` is flagged when ``Path`` is the constructor."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}
    assert violations[0].file_path == module_rel
    assert violations[0].line == 3


def test_flags_raw_path_open_read(tmp_path: Path) -> None:
    """Raw ``Path.open`` read handles cannot evade the R1/R3 boundary."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> bytes:\n"
        "    with Path('x').open('rb') as handle:\n"
        "        return handle.read()\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_open"
    assert violations[0].line == 3


def test_regression_flags_raw_builtin_open_read(tmp_path: Path) -> None:
    """S-6: builtin ``open`` reads cannot evade the R1/R3 boundary."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def load() -> str:\n"
        "    with open('x', encoding='utf-8') as handle:\n"
        "        return handle.read()\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_open"
    assert violations[0].line == 2


def test_flags_raw_pathlib_module_read_text(tmp_path: Path) -> None:
    """``pathlib.Path(...).read_text()`` is flagged."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import pathlib\n"
        "def load() -> str:\n"
        '    return pathlib.Path("x").read_text(encoding="utf-8")\n',
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}


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
        '    return Path("x").read_text(encoding="utf-8")  # filesystem-read-ok: shipped data fixture\n',
    )
    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_marker_without_reason_does_not_exempt(tmp_path: Path) -> None:
    """A bare ``# filesystem-read-ok:`` does NOT exempt the call (D3)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")  # filesystem-read-ok:\n',
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}


def test_marker_on_preceding_line_exempts(tmp_path: Path) -> None:
    """A marker on the immediately preceding line exempts the call."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    # filesystem-read-ok: shipped data fixture\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_recovery_controller_is_not_exempt_from_read_enforcement(tmp_path: Path) -> None:
    """S-6 regression: recovery code must use a local reason, not a module exemption."""
    module_rel = "recovery/controller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def inspect_source() -> str:\n"
        "    return Path('controller.py').read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}


def test_exempt_paths_are_skipped(tmp_path: Path) -> None:
    """The default exempt set keeps the canonical primitives clean."""
    module_rel = "mcp/artifacts/file_backend.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "class FileBackend:\n"
        "    def read_text(self, path, *, encoding='utf-8'):\n"
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
        "import os\nos.path.exists('x')\n",
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


def test_flags_raw_path_traversal_methods(tmp_path: Path) -> None:
    """Path traversal bypasses the bounded Workspace enumeration seam (S-6)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def enumerate_files() -> object:\n"
        "    return Path('x').iterdir(), Path('x').glob('*.py'), Path('x').rglob('*.py')\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert [violation.kind for violation in violations] == [
        "raw_path_iterdir",
        "raw_path_glob",
        "raw_path_rglob",
    ]


def test_flags_raw_os_and_glob_traversal_aliases(tmp_path: Path) -> None:
    """Aliased filesystem-module traversals cannot evade the audit (S-6)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import glob as patterns\n"
        "import os as filesystem\n"
        "def enumerate_files(root: str) -> object:\n"
        "    return filesystem.walk(root), filesystem.scandir(root), patterns.glob('*.py'), patterns.iglob('*.py')\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert [violation.kind for violation in violations] == [
        "raw_walk",
        "raw_scandir",
        "raw_glob",
        "raw_iglob",
    ]


def test_direct_imported_read_and_traversal_functions_cannot_evade_audit(tmp_path: Path) -> None:
    """S-1/S-6 regression: direct filesystem imports remain fail-closed."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from glob import glob as matching_paths, iglob\n"
        "from os import scandir as scan, walk\n"
        "from os.path import exists as path_exists, stat as path_stat\n"
        "def inspect(root: str) -> object:\n"
        "    return path_exists(root), path_stat(root), walk(root), scan(root), matching_paths('*.py'), iglob('*.py')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == [
        "raw_exists",
        "raw_stat",
        "raw_walk",
        "raw_scandir",
        "raw_glob",
        "raw_iglob",
    ]


def test_marked_traversal_is_exempt_but_unmarked_traversal_is_not(tmp_path: Path) -> None:
    """Traversal exceptions remain local, reasoned, and fail closed when empty (S-6)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def allowed(root: str) -> object:\n"
        "    # filesystem-read-ok: canonical workspace implementation owns bounded traversal\n"
        "    return os.walk(root)\n"
        "def rejected(root: str) -> object:\n"
        "    return os.scandir(root)  # filesystem-read-ok:\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert [violation.kind for violation in violations] == ["raw_scandir"]


def test_unrelated_method_chain_passes(tmp_path: Path) -> None:
    """Non-filesystem lookups and arbitrary similarly named methods remain accepted."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def getenv(name: str) -> str | None:\n"
        "    return os.getenv(name)\n"
        "class Catalog:\n"
        "    def walk(self, root: str) -> tuple[str, ...]:\n"
        "        return (root,)\n"
        "def enumerate_catalog(catalog: Catalog) -> tuple[str, ...]:\n"
        "    return catalog.walk('x')\n",
    )
    assert audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    ) == []
