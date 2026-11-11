from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_read_consolidation as audit

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a read finding",
    "raw read detection": "the raw-read detection message now names the structural exemption registry",
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
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind == "invalid_module"
    assert violations[0].file_path == module_rel
    assert violations[0].line == 1


def test_non_utf8_candidate_module_fails_closed(tmp_path: Path) -> None:
    module_rel = "alpha/non_utf8.py"
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_bytes(b"\xff")

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "unreadable_module"
    assert violations[0].file_path == module_rel


def test_valid_candidate_free_module_passes(tmp_path: Path) -> None:
    module_rel = "alpha/inert.py"
    package_root = _write_fake_package(tmp_path, module_rel, "VALUE = 1\n")
    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_flags_raw_path_constructor_read_text(tmp_path: Path) -> None:
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


def test_regression_flags_local_path_variable_read_text(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    source = Path('x')\n"
        "    return source.read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"
    assert violations[0].line == 4


def test_regression_flags_path_read_closed_over_by_nested_function(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def outer() -> str:\n"
        "    source = Path('x')\n"
        "    def inner() -> str:\n"
        "        return source.read_text(encoding='utf-8')\n"
        "    return inner()\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"
    assert violations[0].line == 5


def test_flags_raw_path_open_read(tmp_path: Path) -> None:
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
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef has(p):\n    return os.path.exists(p)\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_exists"
    assert violations[0].line == 3


def test_flags_os_path_module_aliases(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from os import path as filesystem_path\n"
        "import os.path as alternate_path\n"
        "def has(p: str) -> bool:\n"
        "    return filesystem_path.exists(p) or alternate_path.isfile(p)\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_exists", "raw_isfile"]


def test_regression_flags_path_constructor_alias_assignment(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "path_factory = Path\n"
        "def load() -> str:\n"
        "    source = path_factory('x')\n"
        "    return source.read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_path_read_text"
    assert violations[0].line == 5


def test_flags_raw_os_stat(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\ndef probe(p):\n    return os.stat(p)\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
    assert violations[0].kind == "raw_stat"


def test_marker_comment_no_longer_silences_a_read_violation(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")  # filesystem-read-ok: fixture\n',
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1, (
        "an inline marker comment MUST NOT silence the filesystem-read audit"
    )
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}


def test_marker_without_reason_does_not_exempt_either(tmp_path: Path) -> None:
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


def test_marker_on_preceding_line_no_longer_exempts(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    # filesystem-read-ok: shipped data fixture\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1, (
        "an inline marker comment MUST NOT silence the filesystem-read audit"
    )
    assert violations[0].kind in {"raw_path_read_text", "raw_read_text"}


def test_recovery_controller_is_not_exempt_from_read_enforcement(tmp_path: Path) -> None:
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


def test_regression_explicit_module_path_cannot_escape_package_root(tmp_path: Path) -> None:
    package_root = _write_fake_package(tmp_path, "alpha/inert.py", "VALUE = 1\n")
    external_module = tmp_path / "outside.py"
    external_module.write_text("VALUE = 1\n", encoding="utf-8")

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=("../outside.py",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "invalid_module_path"
    assert violations[0].file_path == "../outside.py"


def test_regression_exempt_suffix_cannot_skip_unrelated_module(tmp_path: Path) -> None:
    module_rel = "unrelated/mcp/artifacts/file_backend.py"
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

    assert [violation.kind for violation in violations] == ["raw_path_read_text"]


def test_regression_workspace_monitor_is_not_broadly_exempt_from_read_enforcement(
    tmp_path: Path,
) -> None:
    module_rel = "agents/invoke/_workspace.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    return Path('state.txt').read_text(encoding='utf-8')\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_path_read_text"]


def test_exempt_paths_are_skipped(tmp_path: Path) -> None:
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
    missing = tmp_path / "does-not-exist"
    violations = audit.audit_filesystem_read_consolidation(missing)
    assert len(violations) == 1
    assert violations[0].kind == "missing_package_root"


def test_main_module_entry_point_clean_returns_zero(tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    (package_root / "ralph").mkdir(parents=True)
    (package_root / "ralph" / "inert.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert audit.main([str(package_root)]) == 0


def test_main_module_entry_point_violations_returns_one(tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    (package_root / "ralph").mkdir(parents=True)
    (package_root / "ralph" / "bad.py").write_text(
        "import os\nos.path.exists('x')\n",
        encoding="utf-8",
    )
    assert audit.main([str(package_root)]) == 1


def test_flags_raw_path_traversal_methods(tmp_path: Path) -> None:
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


def test_marked_traversal_is_no_longer_exempt(tmp_path: Path) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import os\n"
        "def allowed(root: str) -> object:\n"
        "    # filesystem-read-ok: canonical workspace traversal\n"
        "    return os.walk(root)\n"
        "def rejected(root: str) -> object:\n"
        "    return os.scandir(root)\n",
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert [violation.kind for violation in violations] == ["raw_walk", "raw_scandir"], (
        "an inline marker comment MUST NOT silence the filesystem-read audit"
    )


def test_unrelated_method_chain_passes(tmp_path: Path) -> None:
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
    assert (
        audit.audit_filesystem_read_consolidation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    violations = audit.audit_filesystem_read_consolidation(repository_root)

    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"


def test_regression_marker_in_string_literal_does_not_bypass_enforcement(tmp_path: Path) -> None:
    module_rel = "alpha/string_marker.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        "    reason = 'filesystem-read-ok: not a comment'\n"
        "    return Path('x').read_text()\n",
    )

    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_path_read_text"]


def test_registered_call_site_is_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "load", "raw_path_read_text")}),
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
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
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "other", "raw_path_read_text")}),
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1


def test_registered_kind_does_not_exempt_another_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "alpha/example.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from pathlib import Path\n"
        "def load() -> str:\n"
        '    return Path("x").read_text(encoding="utf-8")\n',
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("alpha/example.py", "load", "raw_path_exists")}),
    )
    violations = audit.audit_filesystem_read_consolidation(
        package_root,
        module_paths=(module_rel,),
    )
    assert len(violations) == 1
