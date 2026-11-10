"""Meta-tests verifying real-config guard enforcement across the test suite."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import ralph.config.loader as config_loader
from ralph.skills import state_store
from tests.conftest import (
    _ConfigPathSnapshot,
    _diff_config_snapshots,
    _resolve_guarded_config_dirs,
    _scan_guarded_config_files,
)

pytest_plugins = ("pytester",)


def test_resolve_guarded_config_dirs_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default guarded directories must include real user config and home paths."""
    monkeypatch.delenv("RALPH_TEST_GUARDED_CONFIG_DIRS", raising=False)
    dirs = _resolve_guarded_config_dirs()
    assert len(dirs) >= 1
    dir_strings = [str(d) for d in dirs]
    assert any(".config" in s for s in dir_strings)


def test_resolve_guarded_config_dirs_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RALPH_TEST_GUARDED_CONFIG_DIRS overrides monitored directories."""
    custom_dir = tmp_path / "custom_config"
    custom_dir.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(custom_dir))
    dirs = _resolve_guarded_config_dirs()
    assert dirs == [custom_dir.resolve()]


def test_diff_config_snapshots_detection(tmp_path: Path) -> None:
    """Snapshot diffing detects file creation, modification, and deletion."""
    file_a = tmp_path / "ralph-workflow-a.toml"
    file_b = tmp_path / "ralph-workflow-b.toml"

    snap_a1 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1")
    snap_b = _ConfigPathSnapshot(file_b, is_dir=False, mtime_ns=2000, size=20, sha256="hash2")

    # Identical snapshots: no violations
    assert _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1}) == []

    # Created file
    diff_created = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a1, file_b: snap_b})
    assert len(diff_created) == 1
    assert f"CREATED: {file_b}" in diff_created[0]

    # Deleted file
    diff_deleted = _diff_config_snapshots({file_a: snap_a1, file_b: snap_b}, {file_a: snap_a1})
    assert len(diff_deleted) == 1
    assert f"DELETED: {file_b}" in diff_deleted[0]

    # Modified content hash
    snap_a2 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1001, size=12, sha256="hash_mod")
    diff_hash = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a2})
    assert len(diff_hash) == 1
    assert f"MODIFIED (content hash changed): {file_a}" in diff_hash[0]

    # Modified mtime only
    snap_a3 = _ConfigPathSnapshot(file_a, is_dir=False, mtime_ns=1002, size=10, sha256="hash1")
    diff_mtime = _diff_config_snapshots({file_a: snap_a1}, {file_a: snap_a3})
    assert len(diff_mtime) == 1
    assert f"MODIFIED (mtime changed): {file_a}" in diff_mtime[0]

    # Modified mode only
    snap_a4 = _ConfigPathSnapshot(
        file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1", mode=0o644
    )
    snap_a5 = _ConfigPathSnapshot(
        file_a, is_dir=False, mtime_ns=1000, size=10, sha256="hash1", mode=0o600
    )
    diff_mode = _diff_config_snapshots({file_a: snap_a4}, {file_a: snap_a5})
    assert len(diff_mode) == 1
    assert f"MODIFIED (mode changed): {file_a}" in diff_mode[0]


def test_scan_guarded_config_files_scans_recursively(tmp_path: Path) -> None:
    """Guarded scanner captures files and nested directories matching ralph-workflow*."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    top_file = config_dir / "ralph-workflow.toml"
    top_file.write_text("key = 1\n", encoding="utf-8")

    sub_dir = config_dir / "ralph-workflow-data"
    sub_dir.mkdir()
    nested_file = sub_dir / "state.json"
    nested_file.write_text("{}", encoding="utf-8")

    # Unrelated file that does not match the pattern
    unrelated = config_dir / "other.toml"
    unrelated.write_text("x = 1\n", encoding="utf-8")

    snapshot = _scan_guarded_config_files([config_dir])
    assert top_file.resolve() in snapshot
    assert not snapshot[top_file.resolve()].is_dir
    assert snapshot[top_file.resolve()].sha256 != ""

    assert sub_dir.resolve() in snapshot
    assert snapshot[sub_dir.resolve()].is_dir

    assert nested_file.resolve() in snapshot
    assert not snapshot[nested_file.resolve()].is_dir

    assert unrelated.resolve() not in snapshot


def test_real_config_guard_catches_created_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that creates a file under the guarded config path fails loudly."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_leak=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_leak_creation():
                leaked = Path({str(guarded_dir)!r}) / "ralph-workflow-leak.toml"
                leaked.write_text("leaked_key = true\\n", encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "CREATED:" in output
    assert "ralph-workflow-leak.toml" in output


def test_real_config_guard_catches_modified_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that modifies a file under the guarded config path fails loudly."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_modify=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_modification():
                target = Path({str(existing_file)!r})
                target.write_text("tampered = 2\\n", encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "MODIFIED (content hash changed):" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_deleted_file_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that deletes a file under the guarded config path fails loudly."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_delete=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_deletion():
                target = Path({str(existing_file)!r})
                target.unlink()
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "DELETED:" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_chmod_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that chmods a file under the guarded config path fails loudly."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_chmod=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_chmod():
                target = Path({str(existing_file)!r})
                target.chmod(0o600)
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "MODIFIED (mode changed):" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_catches_read_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that reads a file under the guarded config path fails loudly."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_read=textwrap.dedent(
            f"""
            from pathlib import Path

            def test_deliberate_read():
                target = Path({str(existing_file)!r})
                _ = target.read_text(encoding="utf-8")
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=1)
    output = result.stdout.str()
    assert (
        "Test suite touched real user configuration files in violation of isolation rules:"
        in output
    )
    assert "READ:" in output
    assert "ralph-workflow.toml" in output


def test_real_config_guard_clean_suite_passes_in_pytester(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean test suite that does not touch guarded files passes without errors."""
    guarded_dir = tmp_path / "guarded_config"
    guarded_dir.mkdir(parents=True)
    existing_file = guarded_dir / "ralph-workflow.toml"
    existing_file.write_text("initial = 1\n", encoding="utf-8")

    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded_dir))

    pytester.makeconftest(
        textwrap.dedent(
            """
            from tests.conftest import _guard_real_config_files
            """
        )
    )
    pytester.makepyfile(
        test_clean=textwrap.dedent(
            """
            def test_clean_behavior():
                assert 1 + 1 == 2
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")
    result.assert_outcomes(passed=1, errors=0, failed=0)


def test_per_test_isolation_redirects_import_time_constants() -> None:
    """The autouse test environment isolates HOME, XDG_CONFIG_HOME, and config paths."""
    # Under the autouse isolation fixture:
    # 1. Path.home() is sandboxed
    home = Path.home()
    assert "autouse-home" in str(home)

    # 2. GLOBAL_CONFIG_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(config_loader.GLOBAL_CONFIG_PATH)
    assert config_loader.GLOBAL_CONFIG_PATH.name == "ralph-workflow.toml"

    # 3. GLOBAL_AGENTS_CONFIG_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(config_loader.GLOBAL_AGENTS_CONFIG_PATH)
    assert config_loader.GLOBAL_AGENTS_CONFIG_PATH.name == "ralph-workflow-agents.toml"

    # 4. DEFAULT_STATE_PATH is redirected to the sandboxed path
    assert "autouse-home" in str(state_store.DEFAULT_STATE_PATH)
    assert state_store.DEFAULT_STATE_PATH.name == "ralph-workflow-capabilities.json"
