from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

import pytest

from ralph.testing.audit_test_policy import (
    TestPolicyViolation,
    audit_test_file,
    audit_tests_directory,
    collect_whitebox_inventory,
    count_private_attrs,
    count_private_imports,
    count_setattr_private,
    main,
)
from ralph.testing.test_whitebox_baseline import WHITEBOX_BASELINE
from ralph.testing.whitebox_reconciliation import reconcile_whitebox_baseline

_TESTS_ROOT = Path(__file__).parent
_WHITEBOX_BASELINE_MODULE = (
    Path(__file__).parent.parent / "ralph" / "testing" / "test_whitebox_baseline.py"
)


def test_sleep_with_positive_arg_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.sleep(1)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "sleep"


def test_sleep_with_zero_arg_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.sleep(0)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_asyncio_sleep_zero_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import asyncio\nasyncio.sleep(0)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_asyncio_sleep_positive_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import asyncio\nasyncio.sleep(0.5)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "sleep"


def test_open_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text('open("file.txt")\n')
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_open_with_exact_monkeypatch_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("monkeypatch.setattr('builtins.open', fake_open)\nopen('file.txt')\n")
    violations = audit_test_file(test_file)
    assert violations == []


def test_path_read_text_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("from pathlib import Path\nPath('f.txt').read_text()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_path_read_text_with_tmp_path_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("from pathlib import Path\np = tmp_path / 'f.txt'\nPath(p).read_text()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_subprocess_run_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import subprocess\nsubprocess.run(['cmd'])\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_subprocess_with_e2e_marker_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "import pytest\nimport subprocess\n\n"
        "@pytest.mark.subprocess_e2e\n"
        "def test_thing():\n"
        "    subprocess.run(['cmd'])\n"
    )
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_gitpython_repo_init_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "from pathlib import Path\n"
        "from git import Repo\n"
        "\n"
        "def _seed_repo(tmp_path: Path) -> None:\n"
        "    repo = Repo.init(tmp_path)\n"
        "    repo.index.commit('seed')\n"
    )
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"
    assert "Repo.init" in violations[0].detail


def test_gitpython_aliased_repo_import_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "from git import Repo as GitRepo\n\ndef _seed_repo(tmp_path):\n    GitRepo.init(tmp_path)\n"
    )
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"
    assert "GitRepo.init" in violations[0].detail


def test_git_module_attribute_repo_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import git\n\ndef _seed_repo(tmp_path):\n    git.Repo.init(tmp_path)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"
    assert "git.Repo.init" in violations[0].detail


def test_git_exception_constructor_is_not_a_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "from git import GitCommandError, InvalidGitRepositoryError\n"
        "\n"
        "def test_error_kinds():\n"
        "    assert str(GitCommandError('git status'))\n"
        "    assert str(InvalidGitRepositoryError())\n"
    )
    assert audit_test_file(test_file) == []


def test_gitpython_with_e2e_marker_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "import pytest\n"
        "from git import Repo\n"
        "\n"
        "pytestmark = pytest.mark.subprocess_e2e\n"
        "\n"
        "def test_seed(tmp_path):\n"
        "    Repo.init(tmp_path)\n"
    )
    assert audit_test_file(test_file) == []


def test_registered_real_git_file_is_exempt(tmp_path: Path) -> None:
    import ralph.testing.audit_test_policy as audit_module

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_registered_real_git.py"
    test_file.write_text(
        "from git import Repo\n\ndef test_seed(tmp_path):\n    Repo.init(tmp_path)\n"
    )
    with (
        mock.patch.object(audit_module, "_PROJECT_ROOT", tmp_path),
        mock.patch.object(
            audit_module,
            "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
            frozenset({"tests/test_registered_real_git.py"}),
        ),
    ):
        assert audit_module.audit_test_file(test_file) == []
    with mock.patch.object(audit_module, "_PROJECT_ROOT", tmp_path):
        violations = audit_module.audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["io"]


def test_sanctioned_default_lane_real_git_file_is_exempt(tmp_path: Path) -> None:
    import ralph.testing.audit_test_policy as audit_module

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_sanctioned_real_git.py"
    test_file.write_text(
        "from git import Repo\n\ndef test_seed(tmp_path):\n    Repo.init(tmp_path)\n"
    )
    with (
        mock.patch.object(audit_module, "_PROJECT_ROOT", tmp_path),
        mock.patch.object(
            audit_module,
            "SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES",
            frozenset({"tests/test_sanctioned_real_git.py"}),
        ),
    ):
        assert audit_module.audit_test_file(test_file) == []
    with mock.patch.object(audit_module, "_PROJECT_ROOT", tmp_path):
        violations = audit_module.audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["io"]


def test_socket_creation_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import socket\nsocket.socket()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "io"


def test_time_monotonic_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "wall-clock"


def test_blocking_wait_is_violation(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import threading\nevent = threading.Event()\nevent.wait()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert violations[0].category == "blocking-wait"


def test_wait_with_timeout_is_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import threading\nevent = threading.Event()\nevent.wait(timeout=5)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


@pytest.mark.parametrize(
    ("source", "category"),
    [
        ("import time as t\nt.sleep(5)\n", "sleep"),
        ("from time import sleep\nsleep(5)\n", "sleep"),
        ("from time import sleep as nap\nnap(5)\n", "sleep"),
        ("from asyncio import sleep\nsleep(0.5)\n", "sleep"),
        ("import subprocess as sp\nsp.run(['true'])\n", "io"),
        ("from subprocess import run\nrun(['true'])\n", "io"),
        ("from os import system\nsystem('true')\n", "io"),
        ("from socket import create_connection\ncreate_connection(('h', 1))\n", "io"),
        ("import urllib.request as ur\nur.urlopen('http://h')\n", "io"),
        ("from time import monotonic\nmonotonic()\n", "wall-clock"),
        ("def test_x():\n    import subprocess as sp\n    sp.check_output(['true'])\n", "io"),
    ],
)
def test_calls_through_an_import_alias_are_violations(
    tmp_path: Path, source: str, category: str
) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(source)
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == [category]


def test_aliased_sleep_zero_is_still_allowed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("from asyncio import sleep\nsleep(0)\n")
    assert audit_test_file(test_file) == []


def test_patching_the_alias_target_exempts_the_aliased_call(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("import time as t\nmonkeypatch.setattr(t, 'sleep', fake)\nt.sleep(0.3)\n")
    assert audit_test_file(test_file) == []


def test_unrelated_names_sharing_a_banned_suffix_are_not_violations(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        "from ralph.example import run\n"
        "def sleep(seconds):\n    return seconds\n"
        "run(['true'])\nsleep(5)\n"
    )
    assert audit_test_file(test_file) == []


def test_clean_file_no_violations(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_hello():\n    assert True\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_non_py_file_returns_empty(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.txt"
    test_file.write_text("time.sleep(1)\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_io_allowlist_skips_only_io_and_keeps_whitebox_check(tmp_path: Path) -> None:
    test_file = tmp_path / "test_multimodal_session_memory_regression.py"
    test_file.write_text("from ralph.example import _private\nopen('file.txt')\n")
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["whitebox-baseline-missing"]


def test_wall_clock_allowlist_file_is_skipped(tmp_path: Path) -> None:
    test_file = tmp_path / "test_timeout_clock.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 0


def test_source_read_allowlist_permits_reading_shipped_files(tmp_path: Path) -> None:
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("from pathlib import Path\nPath('policy.md').read_text()\n")
    assert audit_test_file(test_file) == []


def test_source_read_allowlist_still_flags_wall_clock(tmp_path: Path) -> None:
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("import time\ntime.monotonic()\n")
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["wall-clock"]


def test_source_read_allowlist_still_flags_subprocess(tmp_path: Path) -> None:
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text("import subprocess\nsubprocess.run(['ls'])\n")
    violations = audit_test_file(test_file)
    assert len(violations) == 1
    assert "subprocess.run" in violations[0].detail


def test_source_read_allowlist_still_flags_sleep_and_writes(tmp_path: Path) -> None:
    test_file = tmp_path / "test_visual_verdict_policy_alignment.py"
    test_file.write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(1)\nPath('policy.md').write_text('x')\n"
    )
    violations = audit_test_file(test_file)
    assert sorted(violation.category for violation in violations) == ["io", "sleep"]


def test_const_first_private_setattr_is_flagged() -> None:
    tree = ast.parse('monkeypatch.setattr("ralph.example._secret", value)\n')
    assert count_setattr_private(tree) == 1


def test_name_first_private_setattr_is_flagged() -> None:
    tree = ast.parse('monkeypatch.setattr(run_loop, "_save_checkpoint", fake)\n')
    assert count_setattr_private(tree) == 1


def test_public_setattr_target_is_not_flagged() -> None:
    tree = ast.parse('monkeypatch.setattr("ralph.example.public_name", value)\n')
    assert count_setattr_private(tree) == 0


def test_dunder_setattr_target_is_not_flagged() -> None:
    tree = ast.parse('monkeypatch.setattr("ralph.example.__wrapped__", value)\n')
    assert count_setattr_private(tree) == 0


def test_private_ralph_symbol_import_is_flagged_but_public_import_is_not() -> None:
    private_tree = ast.parse("from ralph.example import _private\n")
    public_tree = ast.parse("from ralph.example import public_name\n")
    assert count_private_imports(private_tree) == 1
    assert count_private_imports(public_tree) == 0


def test_dunder_private_import_is_not_flagged() -> None:
    tree = ast.parse("from ralph.example import __doc__\n")
    assert count_private_imports(tree) == 0


def test_public_symbol_through_private_module_is_flagged() -> None:
    tree = ast.parse("from ralph.example._private_module import public_name\n")
    assert count_private_imports(tree) == 1


def test_private_attribute_read_inside_assert_is_flagged() -> None:
    tree = ast.parse("def test_value(owner):\n    assert owner._secret == 1\n")
    assert count_private_attrs(tree) == 1


def test_private_attribute_read_outside_assert_is_not_flagged() -> None:
    tree = ast.parse("value = owner._secret\n")
    assert count_private_attrs(tree) == 0


def test_unrelated_monkeypatch_does_not_exempt_sleep(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sleep_policy.py"
    test_file.write_text(
        "monkeypatch.setattr('ralph.example.public_name', fake)\ntime.sleep(0.3)\n"
    )
    violations = audit_test_file(test_file)
    assert [violation.category for violation in violations] == ["sleep"]


def test_exact_sleep_target_patch_exempts_sleep(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sleep_policy.py"
    test_file.write_text("monkeypatch.setattr('time.sleep', fake)\ntime.sleep(0.3)\n")
    violations = audit_test_file(test_file)
    assert violations == []


def test_whitebox_count_above_baseline_exits_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    test_file = tmp_path / "test_whitebox_regression.py"
    test_file.write_text("from ralph.example import _private\n")
    baseline: Mapping[str, Mapping[str, int]] = {
        "tests/test_whitebox_regression.py": {
            "setattr_private": 0,
            "private_import": 0,
            "private_attr": 0,
        }
    }
    exit_code = main([str(tmp_path)], baseline=baseline)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "whitebox-regression" in output


def test_nonzero_whitebox_count_missing_from_baseline_exits_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    test_file = tmp_path / "test_whitebox_missing.py"
    test_file.write_text("from ralph.example import _private\n")
    exit_code = main([str(tmp_path)], baseline={})
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "whitebox-baseline-missing" in output


def test_emit_baseline_entry_set_matches_frozen_baseline() -> None:
    source = _WHITEBOX_BASELINE_MODULE.read_text(encoding="utf-8")
    parsed = ast.parse(source)
    raw_assignment = next(
        node
        for node in parsed.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_RAW"
    )
    raw = ast.literal_eval(raw_assignment.value)
    emitted = {}
    for line in raw.splitlines():
        if not line:
            continue
        path, counts = line.split("|")
        setattr_private, private_import, private_attr = counts.split(",")
        emitted[path] = {
            "setattr_private": int(setattr_private),
            "private_import": int(private_import),
            "private_attr": int(private_attr),
        }

    assert set(emitted) == set(WHITEBOX_BASELINE)
    assert emitted == {path: dict(counts) for path, counts in WHITEBOX_BASELINE.items()}


def _whitebox_counts(
    setattr_private: int = 0,
    private_import: int = 0,
    private_attr: int = 0,
) -> dict[str, int]:
    return {
        "setattr_private": setattr_private,
        "private_import": private_import,
        "private_attr": private_attr,
    }


def _existing_paths(tree_root: Path) -> set[str]:
    return {
        (Path("tests") / path.relative_to(tree_root)).as_posix()
        for path in tree_root.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def test_reconciliation_reports_a_fully_converted_row_as_stale(tmp_path: Path) -> None:
    tree_root = tmp_path / "tests"
    tree_root.mkdir()
    (tree_root / "test_converted.py").write_text(
        "def test_ok() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    key = "tests/test_converted.py"
    baseline = {key: _whitebox_counts(private_import=1)}
    inventory = collect_whitebox_inventory(tree_root)
    assert inventory == {}
    drifts = reconcile_whitebox_baseline(
        inventory,
        baseline,
        existing_paths=_existing_paths(tree_root),
    )
    assert [(drift.path, drift.category) for drift in drifts] == [(key, "whitebox-baseline-stale")]


def test_reconciliation_reports_a_deleted_file_as_removed(tmp_path: Path) -> None:
    tree_root = tmp_path / "tests"
    tree_root.mkdir()
    key = "tests/test_deleted.py"
    baseline = {key: _whitebox_counts(private_import=1)}
    inventory = collect_whitebox_inventory(tree_root)
    drifts = reconcile_whitebox_baseline(
        inventory,
        baseline,
        existing_paths=_existing_paths(tree_root),
    )
    assert [(drift.path, drift.category) for drift in drifts] == [
        (key, "whitebox-baseline-removed")
    ]


def test_reconciliation_reports_a_ceiling_above_the_count_as_unlowered(
    tmp_path: Path,
) -> None:
    tree_root = tmp_path / "tests"
    tree_root.mkdir()
    (tree_root / "test_partial.py").write_text(
        "from ralph.example import _private\n",
        encoding="utf-8",
    )
    key = "tests/test_partial.py"
    baseline = {key: _whitebox_counts(private_import=3)}
    inventory = collect_whitebox_inventory(tree_root)
    assert inventory[key]["private_import"] == 1
    drifts = reconcile_whitebox_baseline(
        inventory,
        baseline,
        existing_paths=_existing_paths(tree_root),
    )
    assert [(drift.path, drift.category) for drift in drifts] == [
        (key, "whitebox-baseline-unlowered")
    ]


def test_reconciliation_reports_no_drift_for_an_exact_match(tmp_path: Path) -> None:
    tree_root = tmp_path / "tests"
    tree_root.mkdir()
    (tree_root / "test_matching.py").write_text(
        "from ralph.example import _private\n",
        encoding="utf-8",
    )
    key = "tests/test_matching.py"
    baseline = {key: _whitebox_counts(private_import=1)}
    inventory = collect_whitebox_inventory(tree_root)
    assert inventory[key]["private_import"] == 1
    drifts = reconcile_whitebox_baseline(
        inventory,
        baseline,
        existing_paths=_existing_paths(tree_root),
    )
    assert drifts == []


def test_frozen_baseline_paths_all_exist_in_the_test_tree() -> None:
    project_root = Path(__file__).resolve().parents[1]
    existing = {
        path.relative_to(project_root).as_posix()
        for path in (project_root / "tests").rglob("*.py")
        if "__pycache__" not in path.parts
    }
    missing = sorted(path for path in WHITEBOX_BASELINE if path not in existing)
    assert missing == []


def test_frozen_baseline_holds_no_all_zero_row() -> None:
    all_zero = sorted(
        path for path, counts in WHITEBOX_BASELINE.items() if not any(counts.values())
    )
    assert all_zero == []


@pytest.mark.timeout_seconds(5)
def test_frozen_baseline_matches_a_fresh_scan_of_the_project_policy_cluster() -> None:
    project_root = Path(__file__).resolve().parents[1]
    cluster_root = project_root / "tests" / "project_policy"
    inventory = collect_whitebox_inventory(cluster_root)
    baseline = {
        path: counts
        for path, counts in WHITEBOX_BASELINE.items()
        if path.startswith("tests/project_policy/")
    }
    existing = {
        (Path("tests") / "project_policy" / path.relative_to(cluster_root)).as_posix()
        for path in cluster_root.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    drifts = reconcile_whitebox_baseline(inventory, baseline, existing_paths=existing)
    assert drifts == []


def test_audit_tests_directory(tmp_path: Path) -> None:
    (tmp_path / "test_clean.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "test_bad.py").write_text("import time\ntime.sleep(2)\n")
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "test_cached.py").write_text("import time\ntime.sleep(99)\n")

    violations, files_checked = audit_tests_directory(tmp_path)
    assert len(violations) == 1
    assert violations[0].category == "sleep"
    assert files_checked >= 2


def test_audit_test_policy_main_exit_zero(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "test_b.py").write_text("def test_also_ok():\n    assert 1 + 1 == 2\n")

    result = main([str(tmp_path)])
    assert result == 0


def test_audit_test_policy_main_exit_one(tmp_path: Path) -> None:
    (tmp_path / "test_bad.py").write_text("import time\ntime.sleep(3)\n")

    result = main([str(tmp_path)])
    assert result == 1


def test_audit_test_policy_main_missing_dir() -> None:
    result = main(["/nonexistent/path/98765"])
    assert result == 2


def test_policy_violation_str_representation() -> None:
    v = TestPolicyViolation(
        file_path="tests/test_bad.py",
        line=42,
        category="sleep",
        detail="time.sleep(1) is bad",
    )
    s = str(v)
    assert "tests/test_bad.py" in s
    assert "42" in s
    assert "sleep" in s
    assert "time.sleep(1) is bad" in s
