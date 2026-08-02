"""Open-mode coverage for the filesystem-write consolidation audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_write_consolidation as audit
from tests.test_audit_filesystem_write_consolidation import _write_fake_package


def test_flags_raw_os_open_with_write_flags(tmp_path: Path) -> None:
    """A raw ``os.open`` creation path cannot bypass the mutation audit."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef create(path):\n    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert [violation.kind for violation in violations] == ["raw_open"]
    assert "filesystem-write-ok" in violations[0].message


def test_flags_raw_os_fsync(tmp_path: Path) -> None:
    """``os.fsync(fd)`` outside the canonical primitive is a raw durability barrier."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef flush(fd):\n    os.fsync(fd)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert len(violations) == 1
    assert violations[0].kind == "raw_fsync"


def test_flags_raw_path_touch(tmp_path: Path) -> None:
    """``Path.touch()`` is a raw mtime bump."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "from pathlib import Path\ndef bump(p):\n    Path(p).touch()\n"
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
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
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert any(violation.kind == "raw_open_write" for violation in violations)


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_flags_direct_imported_open_alias_in_write_mode(tmp_path: Path, module: str) -> None:
    """S-8 regression: direct ``open`` aliases cannot evade write enforcement."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        f'from {module} import open as persist\ndef write(path, content):\n    with persist(path, "w") as stream:\n        stream.write(content)\n',
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "idempotent_write" in violations[0].message


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_flags_module_import_open_alias_in_write_mode(tmp_path: Path, module: str) -> None:
    """S-8 regression: renamed ``io``/``builtins`` modules cannot evade enforcement."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        f'import {module} as file_api\ndef write(path, content):\n    with file_api.open(path, "w") as stream:\n        stream.write(content)\n',
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "idempotent_write" in violations[0].message


@pytest.mark.parametrize("module", ["io", "builtins"])
def test_does_not_flag_direct_imported_open_alias_in_read_mode(tmp_path: Path, module: str) -> None:
    """S-8 regression: direct ``open`` aliases preserve read-only access."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, f'from {module} import open as load\ndef read(path):\n    return load(path, "rb").read()\n'
    )
    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


def test_flags_raw_path_open_write_mode(tmp_path: Path) -> None:
    """``Path.open("a")`` is a raw append outside an approved stream boundary."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'from pathlib import Path\ndef append(path, content):\n    with Path(path).open("a") as stream:\n        stream.write(content)\n',
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert [violation.kind for violation in violations] == ["raw_open_write"]
    assert "filesystem-write-ok" in violations[0].message


@pytest.mark.parametrize("mode", ["r", "rb", None])
def test_does_not_flag_builtin_open_read_modes(tmp_path: Path, mode: str | None) -> None:
    """Default and explicit read modes are reads, never false-positive mutations."""
    module_rel = "alpha/example.py"
    call = "open(path)" if mode is None else f'open(path, "{mode}")'
    package_root = _write_fake_package(tmp_path, module_rel, f"def load(path):\n    return {call}.read()\n")
    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


def test_does_not_flag_builtin_open_keyword_read_mode(tmp_path: Path) -> None:
    """Keyword read modes remain read-only."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, 'def load(path):\n    return open(path, mode="r").read()\n'
    )
    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


@pytest.mark.parametrize("mode", ["rb+", "r+b", "w+b"])
def test_flags_all_plus_open_modes(tmp_path: Path, mode: str) -> None:
    """Every update mode mutates and must be rejected (DA-001)."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, f'def persist(path):\n    return open(path, "{mode}")\n'
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert [violation.kind for violation in violations] == ["raw_open_write"]


def test_marker_suppresses_raw_os_replace(tmp_path: Path) -> None:
    """A ``# filesystem-write-ok:`` marker suppresses ``os.replace`` too."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef swap(src, dst):\n    # filesystem-write-ok: atomic-replace boundary for the canonical primitive\n    os.replace(src, dst)\n",
    )
    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


def test_marker_suppresses_raw_shutil_rmtree(tmp_path: Path) -> None:
    """A ``# filesystem-write-ok:`` marker suppresses ``shutil.rmtree``."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef drop(p):\n    # filesystem-write-ok: bounded retention cleanup of agent home directory\n    shutil.rmtree(p)\n",
    )
    assert audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    """S-6: a missing default root cannot make the write audit silently pass."""
    violations = audit.audit_filesystem_write_consolidation(tmp_path)
    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"
    assert "fail closed" in violations[0].message


def test_synthetic_unknown_writer_in_production_would_fail(tmp_path: Path) -> None:
    """DA-001 invariant: a synthetic unknown ``os.replace`` call must be flagged."""
    module_rel = "sneaky/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef install(tmp, final):\n    os.replace(tmp, final)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert any(violation.kind == "raw_replace" for violation in violations)
    assert any("idempotent_write" in violation.message for violation in violations)


def test_pathlib_path_unlink_detected(tmp_path: Path) -> None:
    """``pathlib.Path.unlink`` (chained attribute) is detected."""
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import pathlib\ndef drop(p):\n    pathlib.Path(p).unlink()\n"
    )
    violations = audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,))
    assert any(violation.kind == "raw_unlink" for violation in violations)
