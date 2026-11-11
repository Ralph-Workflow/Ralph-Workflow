from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_write_consolidation as audit
from tests.test_audit_filesystem_write_consolidation import _write_fake_package

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a write finding",
    "raw open detection": "the raw-open detection message now names the structural exemption registry",
}


def test_flags_raw_os_fdopen_with_write_mode(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'import os\ndef persist(fd):\n    with os.fdopen(fd, "w") as stream:\n        stream.write("content")\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_fdopen_write"]
    assert "EXEMPT_CALL_SITES" in violations[0].message


def test_flags_aliased_os_fdopen_with_write_mode(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'from os import fdopen as persist\ndef write(fd):\n    return persist(fd, "ab")\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_fdopen_write"]


def test_does_not_flag_os_fdopen_with_read_mode(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, 'import os\ndef read(fd):\n    return os.fdopen(fd, "rb").read()\n'
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_flags_direct_workspace_resolver_unlink(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "class Workspace:\n    def _abs(self, path):\n        return path\n    def remove(self, path):\n        self._abs(path).unlink(missing_ok=True)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_unlink"]


def test_regression_flags_workspace_resolver_parent_mkdir(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "class Workspace:\n"
            "    def _abs(self, path):\n"
            "        return path\n"
            "    def prepare(self, path):\n"
            "        self._abs(path).parent.mkdir(parents=True, exist_ok=True)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_mkdir"]


def test_regression_flags_workspace_resolver_parent_variable_mkdir(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "class Workspace:\n"
            "    def _abs(self, path):\n"
            "        return path\n"
            "    def prepare(self, path):\n"
            "        destination = self._abs(path)\n"
            "        destination.parent.mkdir(parents=True, exist_ok=True)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_mkdir"]


def test_flags_raw_builtin_open_write_mode(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        'def persist(path, content):\n    f = open(path, "w")\n    f.write(content)\n    f.close()\n',
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(violation.kind == "raw_open_write" for violation in violations)


@pytest.mark.parametrize("mode", ["r", "rb", None])
def test_does_not_flag_builtin_open_read_modes(tmp_path: Path, mode: str | None) -> None:
    module_rel = "alpha/example.py"
    call = "open(path)" if mode is None else f'open(path, "{mode}")'
    package_root = _write_fake_package(
        tmp_path, module_rel, f"def load(path):\n    return {call}.read()\n"
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_does_not_flag_builtin_open_keyword_read_mode(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, 'def load(path):\n    return open(path, mode="r").read()\n'
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_marker_no_longer_suppresses_raw_os_replace(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef swap(src, dst):\n    # filesystem-write-ok: atomic-replace boundary\n    os.replace(src, dst)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_replace"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_marker_no_longer_suppresses_raw_shutil_rmtree(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef drop(p):\n    # filesystem-write-ok: bounded retention cleanup\n    shutil.rmtree(p)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_rmtree"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_synthetic_unknown_writer_in_production_would_fail(tmp_path: Path) -> None:
    module_rel = "sneaky/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef install(tmp, final):\n    os.replace(tmp, final)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(violation.kind == "raw_replace" for violation in violations)
    assert any("idempotent_write" in violation.message for violation in violations)


def test_pathlib_path_unlink_detected(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import pathlib\ndef drop(p):\n    pathlib.Path(p).unlink()\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert any(violation.kind == "raw_unlink" for violation in violations)
