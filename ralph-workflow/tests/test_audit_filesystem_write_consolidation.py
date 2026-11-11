from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_write_consolidation as audit

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a write finding",
    "raw write detection": "the raw-write detection message now names the structural exemption registry",
    "structural exemption": "the structural exemption registry now carries the waived call site",
}


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_invalid_candidate_free_module_fails_closed(tmp_path: Path) -> None:
    module_rel = "alpha/broken.py"
    package_root = _write_fake_package(tmp_path, module_rel, "def broken(:\n")
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "invalid_module"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 1
    assert "module could not be parsed" in violations[0].message


def test_valid_candidate_free_module_passes(tmp_path: Path) -> None:
    module_rel = "alpha/inert.py"
    package_root = _write_fake_package(tmp_path, module_rel, "VALUE = 1\n")
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_flags_unknown_raw_write_text(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "def persist(path, content):\n    path.write_text(content)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 2
    assert (
        "write_text_if_changed" in violations[0].message or "atomic_write" in violations[0].message
    )


def test_regression_flags_dynamic_raw_write_text_lookup(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    getattr(path, 'write_text')(content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"]
    assert "write_text_if_changed" in violations[0].message


def test_ignores_guarded_write_via_canonical_helper(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\ndef persist(backend, path, content):\n    write_text_if_changed(backend, path, content)\n",
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_media_cache_regression_identical_bytes_skip_the_persistence_boundary() -> None:
    from ralph.mcp.tools.workspace._media_io import write_durable_media_cache

    class RecordingBackend:
        def __init__(self) -> None:
            self.files: dict[Path, bytes] = {}
            self.writes: list[tuple[Path, bytes]] = []
            self.created_directories: list[Path] = []

        def read_bytes(self, path: Path) -> bytes:
            return self.files[path]

        def write_bytes(self, path: Path, content: bytes) -> None:
            self.writes.append((path, content))
            self.files[path] = content

        def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
            del parents, exist_ok
            self.created_directories.append(path)

    class CacheWorkspace:
        def absolute_path(self, path: str) -> str:
            return f"/virtual-workspace/{path}"

    def no_cache_prune(*_args: object, **_kwargs: object) -> None:
        return None

    backend = RecordingBackend()
    workspace = CacheWorkspace()
    first = write_durable_media_cache(
        workspace, "same-artifact", b"payload", backend=backend, cache_pruner=no_cache_prune
    )
    second = write_durable_media_cache(
        workspace, "same-artifact", b"payload", backend=backend, cache_pruner=no_cache_prune
    )
    assert first == second
    assert backend.writes == [(Path("/virtual-workspace") / first, b"payload")]
    assert backend.created_directories == [Path("/virtual-workspace") / ".agent/tmp/media"]


def test_ignores_byte_write_via_canonical_helper(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from ralph.mcp.artifacts.idempotent_write import write_bytes_if_changed\ndef persist(backend, path, content):\n    write_bytes_if_changed(backend, path, content)\n",
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_raw_byte_write_diagnostic_names_existing_atomic_byte_primitive(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "def persist(path, content):\n    path.write_bytes(content)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert "write_bytes_if_changed" in violations[0].message
    assert "atomic_write_bytes_if_changed" in violations[0].message


def test_marker_comment_no_longer_silences_a_violation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def write_scratch(path, content):\n    # filesystem-write-ok: transient scratch file\n    path.write_text(content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_marker_comment_fails_closed(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    # filesystem-write-ok:\n    path.write_text(content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_raw_write_bytes_also_flagged(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "def persist(path, data):\n    path.write_bytes(data)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_whitespace_around_attr_does_not_evade(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "def persist(path, content):\n    path.write_text (content)\n"
    )
    assert (
        len(audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)))
        == 1
    )


def test_backend_write_text_is_flagged_outside_the_canonical_primitive(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def archive(backend, dest, src):\n    backend.write_text(dest, backend.read_text(src))\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"
    assert "write_text_if_changed" in violations[0].message


def test_self_write_text_is_flagged_outside_the_canonical_primitive(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "class WrappedBackend:\n    def persist(self, path, content):\n        self.write_text(path, content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_write_text"


def test_arbitrary_name_write_text_is_flagged(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "def persist(fs, path, content):\n    fs.write_text(path, content)\n"
    )
    assert (
        len(audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)))
        == 1
    )


def test_marker_on_prior_line_no_longer_suppresses(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    # filesystem-write-ok: timestamped marker file\n    path.write_text(content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_regression_rejects_marker_token_inside_a_string_literal(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    note = 'filesystem-write-ok: not an annotation'\n    path.write_text(content)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"]
    assert violations[0].line == 3


def test_marker_on_same_line_no_longer_suppresses(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)  # filesystem-write-ok: timestamped\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_default_scope_excludes_the_audit_implementation_tree(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    production = repo_root / "ralph"
    production.mkdir(parents=True)
    (production / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    audit_dir = production / "testing"
    audit_dir.mkdir()
    (audit_dir / "raw_writer.py").write_text(
        "def persist(path):\n    path.write_text('audit fixture')\n", encoding="utf-8"
    )
    assert audit.audit_filesystem_write_consolidation(repo_root) == []


def test_default_scope_walks_only_production_package(tmp_path: Path) -> None:
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
    clean_root = tmp_path / "ralph"
    clean_root.mkdir()
    (clean_root / "good.py").write_text(
        "from ralph.mcp.artifacts.idempotent_write import write_text_if_changed\n", encoding="utf-8"
    )
    assert audit.main([str(clean_root)]) == 0


def test_cli_returns_violation_count_when_dirty(tmp_path: Path) -> None:
    dirty_root = tmp_path / "ralph"
    dirty_root.mkdir()
    (dirty_root / "bad.py").write_text(
        "def persist(path, content):\n    path.write_text(content)\n", encoding="utf-8"
    )
    assert audit.main([str(dirty_root)]) == 1


def test_cli_returns_bad_root_when_missing(tmp_path: Path) -> None:
    assert audit.main([str(tmp_path / "missing")]) == 2


def test_missing_package_root_fails_closed(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    violations = audit.audit_filesystem_write_consolidation(missing_root)
    assert len(violations) == 1
    assert violations[0].kind == "missing_package_root"
    assert violations[0].file_path == str(missing_root)
    assert "could not be walked" in violations[0].message


def test_flags_raw_os_replace(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef swap(src, dst):\n    os.replace(src, dst)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_replace"


def test_flags_import_aliased_os_replace(tmp_path: Path) -> None:
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
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef swap(src, dst):\n    os.rename(src, dst)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_rename"


def test_flags_raw_path_unlink(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "from pathlib import Path\ndef drop(p):\n    Path(p).unlink()\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_unlink"


def test_flags_path_derived_variable_without_path_suffix(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\ndef drop() -> None:\n    target = Path('stale')\n    target.unlink(missing_ok=True)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_unlink"]
    assert violations[0].line == 4


def test_regression_instance_path_provenance_flags_raw_unlink(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from pathlib import Path\n"
            "class Workspace:\n"
            "    def remove(self) -> None:\n"
            "        self.target = Path('stale')\n"
            "        self.target.unlink(missing_ok=True)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_unlink"]
    assert violations[0].line == 5


def test_path_provenance_does_not_leak_between_function_scopes(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\ndef build() -> None:\n    target = Path('stale')\ndef detach(target) -> None:\n    target.unlink()\n",
    )
    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_flags_workspace_resolved_path_mutation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "class Workspace:\n    def remove(self) -> None:\n        target = self._abs('stale.txt')\n        target.unlink(missing_ok=True)\n",
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_unlink"]
    assert violations[0].line == 4


def test_flags_raw_os_remove(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef drop(p):\n    os.remove(p)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_remove"


def test_flags_raw_path_mkdir(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "from pathlib import Path\ndef make(p):\n    Path(p).mkdir()\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_mkdir"


def test_flags_raw_os_makedirs(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import os\ndef make(p):\n    os.makedirs(p)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_makedirs"


def test_flags_raw_shutil_rmtree(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import shutil\ndef drop_tree(p):\n    shutil.rmtree(p)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_rmtree"


def test_flags_raw_shutil_copy2(tmp_path: Path) -> None:
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


def test_regression_flags_raw_shutil_copytree(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import shutil\ndef duplicate_tree(src, dst):\n    shutil.copytree(src, dst)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_copytree"]
    assert "EXEMPT_CALL_SITES" in violations[0].message


def test_flags_raw_shutil_move(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path, module_rel, "import shutil\ndef relocate(src, dst):\n    shutil.move(src, dst)\n"
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_move"


def test_flags_raw_os_open_with_write_flags(tmp_path: Path) -> None:
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
    assert "EXEMPT_CALL_SITES" in violations[0].message


def test_flags_raw_os_fsync(tmp_path: Path) -> None:
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


def test_regression_flags_path_chmod_metadata_mutation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\ndef restrict(p):\n    Path(p).chmod(0o600)\n",
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_chmod"]
    assert "EXEMPT_CALL_SITES" in violations[0].message


def test_regression_flags_path_variable_chmod_metadata_mutation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from pathlib import Path\n"
            "def restrict(p):\n"
            "    protected = Path(p)\n"
            "    protected.chmod(0o600)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )

    assert [violation.kind for violation in violations] == ["raw_chmod"]


def test_reasoned_marker_no_longer_allows_path_chmod_metadata_mutation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "from pathlib import Path\n"
            "def restrict(p):\n"
            "    protected = Path(p)\n"
            "    # filesystem-write-ok: persistent identity staging\n"
            "    protected.chmod(0o600)\n"
        ),
    )

    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_chmod"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_flags_raw_path_touch(tmp_path: Path) -> None:
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
def test_does_not_flag_direct_imported_open_alias_in_read_mode(tmp_path: Path, module: str) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        f'from {module} import open as load\ndef read(path):\n    return load(path, "rb").read()\n',
    )

    assert (
        audit.audit_filesystem_write_consolidation(package_root, module_paths=(module_rel,)) == []
    )


def test_flags_raw_path_open_write_mode(tmp_path: Path) -> None:
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
    assert "EXEMPT_CALL_SITES" in violations[0].message


def test_does_not_flag_builtin_open_read_mode(tmp_path: Path) -> None:
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


def test_marker_no_longer_suppresses_raw_os_replace(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        (
            "import os\n"
            "def swap(src, dst):\n"
            "    # filesystem-write-ok: atomic-replace boundary\n"
            "    os.replace(src, dst)\n"
        ),
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
        (
            "import shutil\n"
            "def drop(p):\n"
            "    # filesystem-write-ok: bounded retention cleanup\n"
            "    shutil.rmtree(p)\n"
        ),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_rmtree"], (
        "an inline marker comment MUST NOT silence the filesystem-write audit"
    )


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    violations = audit.audit_filesystem_write_consolidation(tmp_path)

    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"
    assert "fail closed" in violations[0].message


def test_regression_explicit_module_path_cannot_escape_package_root(tmp_path: Path) -> None:
    package_root = _write_fake_package(tmp_path, "alpha/inert.py", "VALUE = 1\n")
    external_module = tmp_path / "outside.py"
    external_module.write_text("VALUE = 1\n", encoding="utf-8")

    violations = audit.audit_filesystem_write_consolidation(
        package_root,
        module_paths=("../outside.py",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "invalid_module_path"
    assert violations[0].file_path == "../outside.py"
    assert "escapes the package root" in violations[0].message


def test_synthetic_unknown_writer_in_production_would_fail(tmp_path: Path) -> None:
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
    assert any("idempotent_write" in v.message for v in violations)


def test_pathlib_path_unlink_detected(tmp_path: Path) -> None:
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


def test_registered_call_site_is_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "persist", "raw_write_text")}),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert violations == []


def test_registered_scope_does_not_exempt_another_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "other", "raw_write_text")}),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"]


def test_registered_kind_does_not_exempt_another_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "persist", "raw_open_write")}),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"]


def test_registered_call_site_does_not_exempt_another_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "def persist(path, content):\n    path.write_text(content)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/other.py", "persist", "raw_write_text")}),
    )
    violations = audit.audit_filesystem_write_consolidation(
        package_root, module_paths=(module_rel,)
    )
    assert [violation.kind for violation in violations] == ["raw_write_text"]
