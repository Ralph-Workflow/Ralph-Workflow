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


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root



def test_invalid_candidate_free_module_fails_closed(tmp_path: Path) -> None:
    """S-8 regression: invalid source cannot bypass the package-wide audit."""
    module_rel = "alpha/broken.py"
    package_root = _write_fake_package(tmp_path, module_rel, "def broken(:\n")

    violations = audit.audit_filesystem_write_consolidation(
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

    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


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


def test_ignores_byte_write_via_canonical_helper(tmp_path: Path) -> None:
    """S-3: the audited byte-specific persistence boundary is available to new writers."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from ralph.mcp.artifacts.idempotent_write import write_bytes_if_changed\n"
            "def persist(backend, path, content):\n"
            "    write_bytes_if_changed(backend, path, content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_raw_byte_write_diagnostic_names_existing_atomic_byte_primitive(tmp_path: Path) -> None:
    """D2: rejected byte writes name a real direct and atomic replacement path."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_bytes(content)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert "write_bytes_if_changed" in violations[0].message
    assert "atomic_write_bytes_if_changed" in violations[0].message


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


def test_backend_write_text_is_flagged_outside_the_canonical_primitive(tmp_path: Path) -> None:
    """S-8: a new backend-named raw writer cannot bypass D1 by convention alone."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "def archive(backend, dest, src):\n"
            "    backend.write_text(dest, backend.read_text(src))\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert "write_text_if_changed" in violations[0].message


def test_self_write_text_is_flagged_outside_the_canonical_primitive(tmp_path: Path) -> None:
    """S-8: receiver spelling cannot silently exempt an unknown writer."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "class WrappedBackend:\n"
            "    def persist(self, path, content):\n"
            "        self.write_text(path, content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_arbitrary_name_write_text_is_flagged(tmp_path: Path) -> None:
    """Any raw receiver name is rejected outside the canonical primitive."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(fs, path, content):\n    fs.write_text(path, content)\n",
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


def test_default_scope_walks_only_production_package(tmp_path: Path) -> None:
    """Default audit scope finds ralph/ but ignores unrelated test fixtures."""
    repo_root = tmp_path / "repo"
    production = repo_root / "ralph"
    production.mkdir(parents=True)
    (production / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = repo_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "fixture.py").write_text(
        "def persist(path):\n    path.write_text('fixture')\n", encoding="utf-8"
    )
    assert audit.audit_filesystem_write_consolidation(repo_root) == []


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


def test_missing_package_root_fails_closed(tmp_path: Path) -> None:
    """S-8 regression: an unavailable requested root cannot silently pass."""
    missing_root = tmp_path / "missing"

    violations = audit.audit_filesystem_write_consolidation(missing_root)

    assert len(violations) == 1
    assert violations[0].kind == "missing_package_root"
    assert violations[0].file_path == str(missing_root)
    assert "could not be walked" in violations[0].message


# ---------------------------------------------------------------------------
# Raw qualified-mutation detection (DA-001: open/write/rename/replace/...).
# Each test exercises one entry in the audit's qualified-mutation table.
# ---------------------------------------------------------------------------


def test_flags_raw_os_replace(tmp_path: Path) -> None:
    """``os.replace(src, dst)`` is a raw atomic move outside the canonical primitive."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef swap(src, dst):\n    os.replace(src, dst)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_replace"


def test_flags_import_aliased_os_replace(tmp_path: Path) -> None:
    """An import alias cannot evade the package-wide raw-replace audit (S-8)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os as operating_system\ndef swap(src, dst):\n    operating_system.replace(src, dst)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_replace"]
    assert "idempotent_write" in violations[0].message


def test_flags_directly_imported_os_replace(tmp_path: Path) -> None:
    """A directly imported mutation function cannot bypass the audit (S-8)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from os import replace as publish\ndef swap(src, dst):\n    publish(src, dst)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_replace"]
    assert "idempotent_write" in violations[0].message


def test_flags_import_aliased_pathlib_mutation(tmp_path: Path) -> None:
    """A renamed pathlib class cannot bypass the package-wide delete audit (S-8)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path as ProjectPath\ndef drop(path):\n    ProjectPath(path).unlink()\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_unlink"]


def test_flags_raw_os_rename(tmp_path: Path) -> None:
    """``os.rename(src, dst)`` is a raw atomic move."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef swap(src, dst):\n    os.rename(src, dst)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_rename"


def test_flags_raw_path_unlink(tmp_path: Path) -> None:
    """``Path.unlink()`` is a raw delete."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("from pathlib import Path\ndef drop(p):\n    Path(p).unlink()\n"),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_unlink"


def test_flags_raw_os_remove(tmp_path: Path) -> None:
    """``os.remove(path)`` is a raw delete."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef drop(p):\n    os.remove(p)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_remove"


def test_flags_raw_path_mkdir(tmp_path: Path) -> None:
    """``Path.mkdir()`` is a raw directory creation."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("from pathlib import Path\ndef make(p):\n    Path(p).mkdir()\n"),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_mkdir"


def test_flags_raw_os_makedirs(tmp_path: Path) -> None:
    """``os.makedirs(path)`` is a raw directory creation."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef make(p):\n    os.makedirs(p)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_makedirs"


def test_flags_raw_shutil_rmtree(tmp_path: Path) -> None:
    """``shutil.rmtree(path)`` is a raw tree delete."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef drop_tree(p):\n    shutil.rmtree(p)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_rmtree"


def test_flags_raw_shutil_copy2(tmp_path: Path) -> None:
    """``shutil.copy2(src, dst)`` is a raw copy."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef duplicate(src, dst):\n    shutil.copy2(src, dst)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_copy2"


def test_flags_raw_shutil_move(tmp_path: Path) -> None:
    """``shutil.move(src, dst)`` is a raw move."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef relocate(src, dst):\n    shutil.move(src, dst)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_move"


def test_flags_raw_os_open_with_write_flags(tmp_path: Path) -> None:
    """A raw ``os.open`` creation path cannot bypass the mutation audit."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "import os\n"
            "def create(path):\n"
            "    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_open"]
    assert "filesystem-write-ok" in violations[0].message


def test_flags_raw_os_fsync(tmp_path: Path) -> None:
    """``os.fsync(fd)`` outside the canonical primitive is a raw durability barrier."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef flush(fd):\n    os.fsync(fd)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_fsync"


def test_flags_raw_path_touch(tmp_path: Path) -> None:
    """``Path.touch()`` is a raw mtime bump."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("from pathlib import Path\ndef bump(p):\n    Path(p).touch()\n"),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_touch"


def test_flags_raw_builtin_open_write_mode(tmp_path: Path) -> None:
    """``open(path, "w")`` is a raw write that bypasses the canonical primitive."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'def persist(path, content):\n    f = open(path, "w")\n    f.write(content)\n    f.close()\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(v.kind == "raw_open_write" for v in violations)


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_flags_direct_imported_open_alias_in_write_mode(tmp_path: Path, module: str) -> None:
    """S-8 regression: direct ``open`` aliases cannot evade write enforcement."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            f"from {module} import open as persist\n"
            "def write(path, content):\n"
            '    with persist(path, "w") as stream:\n'
            "        stream.write(content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "idempotent_write" in violations[0].message


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_flags_module_import_open_alias_in_write_mode(tmp_path: Path, module: str) -> None:
    """S-8 regression: renamed ``io``/``builtins`` modules cannot evade enforcement."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            f"import {module} as file_api\n"
            "def write(path, content):\n"
            '    with file_api.open(path, "w") as stream:\n'
            "        stream.write(content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "idempotent_write" in violations[0].message


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_does_not_flag_direct_imported_open_alias_in_read_mode(
    tmp_path: Path, module: str
) -> None:
    """S-8 regression: direct ``open`` aliases preserve read-only access."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        f"from {module} import open as load\ndef read(path):\n    return load(path, \"rb\").read()\n",
    )

    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


def test_flags_raw_path_open_write_mode(tmp_path: Path) -> None:
    """``Path.open(\"a\")`` is a raw append outside an approved stream boundary."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from pathlib import Path\n"
            "def append(path, content):\n"
            '    with Path(path).open("a") as stream:\n'
            "        stream.write(content)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "filesystem-write-ok" in violations[0].message


def test_does_not_flag_builtin_open_read_mode(tmp_path: Path) -> None:
    """``open(path, "r")`` is a read, not a write, and must not be flagged."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'def load(path):\n    return open(path, "r").read()\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert violations == []


def test_does_not_flag_builtin_open_binary_read(tmp_path: Path) -> None:
    """``open(path, "rb")`` is a binary read, not a write."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'def load(path):\n    return open(path, "rb").read()\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert violations == []


def test_does_not_flag_builtin_open_default_or_keyword_read_mode(tmp_path: Path) -> None:
    """Default and keyword read modes are reads, never false-positive mutations."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'def load(path):\n    return open(path).read() + open(path, mode="r").read()\n',
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


@pytest.mark.parametrize("mode", ["rb+", "r+b", "w+b"])
def test_flags_all_plus_open_modes(tmp_path: Path, mode: str) -> None:
    """Every update mode mutates and must be rejected (DA-001)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        f'def persist(path):\n    return open(path, "{mode}")\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_open_write"]


def test_marker_suppresses_raw_os_replace(tmp_path: Path) -> None:
    """A ``# filesystem-write-ok:`` marker suppresses ``os.replace`` too."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "import os\n"
            "def swap(src, dst):\n"
            "    # filesystem-write-ok: atomic-replace boundary for the canonical primitive\n"
            "    os.replace(src, dst)\n"
        ),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert violations == []


def test_marker_suppresses_raw_shutil_rmtree(tmp_path: Path) -> None:
    """A ``# filesystem-write-ok:`` marker suppresses ``shutil.rmtree``."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "import shutil\n"
            "def drop(p):\n"
            "    # filesystem-write-ok: bounded retention cleanup of agent home directory\n"
            "    shutil.rmtree(p)\n"
        ),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert violations == []


def test_synthetic_unknown_writer_in_production_would_fail(tmp_path: Path) -> None:
    """DA-001 invariant: a synthetic unknown ``os.replace`` call must be flagged.

    This is the regression test that proves the audit cannot be
    silently bypassed by introducing a new writer that uses a
    mutation form not previously seen by the audit.
    """
    module_rel = "sneaky/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("import os\ndef install(tmp, final):\n    os.replace(tmp, final)\n"),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(v.kind == "raw_replace" for v in violations)
    # D2: diagnostic names the sanctioned primitive.
    assert any("idempotent_write" in v.message for v in violations)


def test_pathlib_path_unlink_detected(tmp_path: Path) -> None:
    """``pathlib.Path.unlink`` (chained attribute) is detected."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        ("import pathlib\ndef drop(p):\n    pathlib.Path(p).unlink()\n"),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(v.kind == "raw_unlink" for v in violations)
